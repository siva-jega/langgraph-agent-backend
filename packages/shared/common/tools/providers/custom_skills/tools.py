"""Workspace custom-skill tools (`use_skill`, `use_skill_channel`).

Both tools share the same skill-row resolution, AI-skill text response, and flow
`afterSubmit` execution path; only the field-collection step differs:

- ``use_skill``         — web runtime. Raises a LangGraph ``interrupt`` so the UI
                          renders the configured form; the user submits values.
- ``use_skill_channel`` — channel runtime (WhatsApp / Messenger / Instagram /
                          Gmail). No interrupt — the calling LLM collects field
                          values from the user in chat and passes them in via
                          ``fields``. The tool validates the dict against the
                          skill's required-field config before running
                          ``afterSubmit``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, List

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt

from common.tools.interrupt_payload import merge_interrupt_value
from common.tools.providers.aide.field_mapping import validate_required_form_fields

logger = logging.getLogger(__name__)

_USE_SKILL = "use_skill"
_USE_SKILL_CHANNEL = "use_skill_channel"


@dataclass(frozen=True)
class _ParsedSkill:
    """A custom-skill row parsed into the pieces both tool entry points need."""

    cs: dict[str, Any]
    kind: str  # "ai" | "flow" (or other, treated as unknown)
    display: str


# A tool result tuple: (text shown to the LLM, artifact dict for UI / debugging).
_SkillResult = tuple[str, dict[str, Any]]


def _normalize_form_fields(raw: Any) -> List[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[dict[str, Any]] = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        d = dict(f)
        fid = str(d.get("id") or d.get("name") or "").strip()
        if not fid:
            continue
        d.setdefault("name", fid)
        d.setdefault("id", fid)
        d.setdefault("label", d.get("label") or fid)
        d.setdefault("type", d.get("type") or "text")
        out.append(d)
    return out


def _find_skill_row(config: RunnableConfig, agent_skill_id: str) -> dict[str, Any] | None:
    skills = (config.get("configurable") or {}).get("skills") or []
    if not isinstance(skills, list):
        return None
    for s in skills:
        if isinstance(s, dict) and str(s.get("id") or "") == str(agent_skill_id):
            return s
    return None


def _collect_effective_apps(configurable: dict[str, Any]) -> list[dict[str, Any]]:
    """Union `apps` and `apps_config` so flow completion still sees connectors after HITL resume."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_apps = configurable.get("apps") or []
    if isinstance(raw_apps, list):
        for a in raw_apps:
            if not isinstance(a, dict):
                continue
            slug = str(a.get("slug") or a.get("name") or "").strip().lower()
            if slug and slug not in seen:
                seen.add(slug)
                out.append(dict(a))
    ac = configurable.get("apps_config") or []
    if isinstance(ac, list):
        for row in ac:
            if not isinstance(row, dict):
                continue
            slug = str(row.get("slug") or "").strip().lower()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            aid = row.get("id") or row.get("appId") or row.get("app_id")
            out.append({"id": aid, "slug": row.get("slug") or slug})
    return out


def _apps_include_slug(apps: list[dict[str, Any]], app_slug: str) -> bool:
    want = app_slug.strip().lower()
    if not want:
        return False
    for a in apps:
        if not isinstance(a, dict):
            continue
        s = str(a.get("slug") or a.get("name") or "").strip().lower()
        if s == want:
            return True
    return False


def _upsert_app_for_slug(
    apps: list[dict[str, Any]], entry: dict[str, Any]
) -> list[dict[str, Any]]:
    """Replace any row with the same slug so flow/DB resolution wins over stale config."""
    sl = str(entry.get("slug") or entry.get("name") or "").strip().lower()
    if not sl:
        return [*apps, entry]
    rest = [
        a
        for a in apps
        if str(a.get("slug") or a.get("name") or "").strip().lower() != sl
    ]
    rest.append(entry)
    return rest


def _resolve_registry_tool(tool_registry: Any, app_slug: str, tool_name: str) -> Any:
    """Resolve a flow `afterSubmit.toolName` to a LangChain tool.

    Older UI stored human titles (e.g. \"Search Gmail Messages\") instead of registry ids
    (`search_messages`). Try exact id first, then match provider metadata `name` or `id`.
    """
    tc = tool_registry.get_tool_by_name(tool_name)
    if tc:
        return tc
    slug = (app_slug or "").strip().lower()
    if not slug:
        return None
    for meta in tool_registry.get_tools_metadata_by_provider(slug):
        if not isinstance(meta, dict):
            continue
        tid = str(meta.get("id") or "").strip()
        display = str(meta.get("name") or "").strip()
        if tool_name == tid or tool_name == display:
            if tid:
                return tool_registry.get_tool_by_name(tid)
    return None


def _explicit_app_id_from_flow(after: dict[str, Any]) -> str | None:
    """Optional `Apps.id` from flow config — source of truth when set."""
    for key in ("appId", "appRecordId", "connectorAppId"):
        raw = after.get(key)
        if raw is None:
            continue
        s = str(raw).strip()
        if s:
            return s
    return None


async def _db_fetch_app_by_id(app_id: str) -> dict[str, Any] | None:
    try:
        from common.infrastructure import get_pg

        pg = get_pg()
    except RuntimeError:
        return None
    try:
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT app.id AS id, app.slug
                FROM "Apps" app
                WHERE app.id = $1
                LIMIT 1
                """,
                app_id,
            )
        if row and row.get("id"):
            return {"id": row["id"], "slug": row.get("slug") or ""}
    except Exception as e:
        logger.warning("use_skill fetch app by id failed: %s", e, exc_info=True)
    return None


async def _db_resolve_app_for_agent(
    agent_id: str,
    user_id: str | None,
    app_slug: str,
) -> dict[str, Any] | None:
    """Resolve an `Apps` row for flow `appSlug` (agent link, then workspace, then creator)."""
    slug = app_slug.strip()
    if not agent_id or not slug:
        return None
    try:
        from common.infrastructure import get_pg

        pg = get_pg()
    except RuntimeError:
        return None

    # Prisma uses TEXT for Agent.id, Apps.id, and _AgentToApps A/B — do not cast bind params to uuid.
    q_agent_link = """
        SELECT app.id AS id, app.slug
        FROM "_AgentToApps" j
        INNER JOIN "Apps" app ON j."B" = app.id
        WHERE j."A" = $1
          AND LOWER(TRIM(app.slug)) = LOWER(TRIM($2))
        LIMIT 1
    """
    q_workspace = """
        SELECT app.id AS id, app.slug
        FROM "Apps" app
        INNER JOIN "Agent" ag ON ag."workspaceId" = app."workspaceId"
        WHERE ag.id = $1
          AND LOWER(TRIM(app.slug)) = LOWER(TRIM($2))
        LIMIT 1
    """
    q_creator = """
        SELECT app.id AS id, app.slug
        FROM "Apps" app
        WHERE app."createdBy" = $1
          AND LOWER(TRIM(app.slug)) = LOWER(TRIM($2))
        ORDER BY app."updatedAt" DESC
        LIMIT 1
    """

    try:
        async with pg.acquire() as conn:
            row = await conn.fetchrow(q_agent_link, agent_id, slug)
            if row and row.get("id"):
                return {"id": row["id"], "slug": row.get("slug") or slug}
            row = await conn.fetchrow(q_workspace, agent_id, slug)
            if row and row.get("id"):
                return {"id": row["id"], "slug": row.get("slug") or slug}
            if user_id:
                row = await conn.fetchrow(q_creator, user_id, slug)
                if row and row.get("id"):
                    return {"id": row["id"], "slug": row.get("slug") or slug}
    except Exception as e:
        logger.warning("use_skill DB app resolve failed: %s", e, exc_info=True)
    return None


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _parse_custom_skill_row(
    config: RunnableConfig, agent_skill_id: str
) -> _ParsedSkill | _SkillResult:
    """Resolve and validate a custom-skill row from agent config.

    On success returns ``_ParsedSkill``; on failure returns a ready-to-return
    tool result tuple so the caller can ``return parsed`` straight back.
    """
    row = _find_skill_row(config, agent_skill_id)
    if not row:
        return (
            "Custom skill not found for this agent.",
            {"success": False, "error": "skill_not_found", "agent_skill_id": agent_skill_id},
        )

    raw_cfg = row.get("config") if isinstance(row.get("config"), dict) else {}
    cs = raw_cfg.get("customSkill") if isinstance(raw_cfg, dict) else None
    if not isinstance(cs, dict):
        return (
            "This skill row is not configured as a custom skill.",
            {"success": False, "error": "invalid_config"},
        )

    return _ParsedSkill(
        cs=cs,
        kind=str(cs.get("kind") or "ai").strip().lower(),
        display=str(cs.get("displayName") or "Custom skill").strip(),
    )


def _build_ai_skill_response(
    parsed: _ParsedSkill, agent_skill_id: str, context: str
) -> _SkillResult:
    """Render the assistant-facing instructions for an AI-kind custom skill."""
    prompt = str(parsed.cs.get("aiPrompt") or "").strip() or parsed.display
    text = (
        f"[Custom skill: {parsed.display}]\n\n"
        f"Follow these instructions when helping the user:\n\n{prompt}"
    )
    if context.strip():
        text += f"\n\n(Model context: {context.strip()})"
    return text, {
        "type": "custom_ai_skill",
        "agent_skill_id": agent_skill_id,
        "displayName": parsed.display,
        "prompt": prompt,
    }


def _flow_form_fields(parsed: _ParsedSkill) -> List[dict[str, Any]]:
    """Extract & normalize the configured form fields for a flow-kind skill."""
    raw_form = parsed.cs.get("form")
    form: dict[str, Any] = raw_form if isinstance(raw_form, dict) else {}
    fields_raw = form.get("fields") if isinstance(form.get("fields"), list) else []
    return _normalize_form_fields(fields_raw)


def _normalize_submitted_fields(raw: Any) -> dict[str, str]:
    """Coerce a raw submission dict into ``{field_id: stripped_string}``.

    Used by both the interrupt-resume path (web) and the LLM-supplied dict path
    (channel). Empty / None values are dropped so downstream validation can
    detect missing required fields cleanly.
    """
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out[str(k)] = s
    return out


def _parse_http_headers(raw: Any) -> dict[str, str]:
    """Accept either a dict or a JSON string and return a flat ``str → str`` map."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if v is not None}
    return {}


async def _fire_webhook(
    after: dict[str, Any], agent_skill_id: str, submitted: dict[str, str]
) -> None:
    """Best-effort webhook fire. Logs and swallows failures; the flow still completes."""
    url = str(after.get("webhookUrl") or "").strip()
    if not url:
        return
    method = str(after.get("webhookMethod") or "POST").strip().upper() or "POST"
    headers = _parse_http_headers(after.get("httpHeaders")) or None
    body_template = after.get("httpBody")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if method == "GET":
                r = await client.get(url, params=submitted, headers=headers)
            elif isinstance(body_template, str) and body_template.strip():
                rendered = body_template
                for k, v in submitted.items():
                    rendered = rendered.replace("{{" + k + "}}", v)
                try:
                    r = await client.request(
                        method, url, json=json.loads(rendered), headers=headers,
                    )
                except json.JSONDecodeError:
                    r = await client.request(
                        method, url, content=rendered.encode("utf-8"), headers=headers,
                    )
            elif isinstance(body_template, dict):
                r = await client.request(method, url, json=body_template, headers=headers)
            else:
                r = await client.request(
                    method,
                    url,
                    json={"agent_skill_id": agent_skill_id, "fields": submitted},
                    headers=headers,
                )
            logger.info("use_skill http %s %s -> %s", method, url, r.status_code)
    except Exception as e:
        logger.warning("use_skill http failed: %s", e, exc_info=True)


async def _run_app_action(
    config: RunnableConfig,
    after: dict[str, Any],
    parsed: _ParsedSkill,
    agent_skill_id: str,
    submitted: dict[str, str],
) -> _SkillResult:
    """Resolve the configured app + tool and invoke it with the submitted fields."""
    from common.tools.registry import registry as tool_registry

    tool_name = str(after.get("toolName") or "").strip()
    app_slug = str(after.get("appSlug") or "").strip().lower()
    if not tool_name:
        return (
            "App action is missing toolName.",
            {"success": False, "error": "missing_tool_name"},
        )
    if not app_slug:
        return (
            "App action requires appSlug in the skill configuration.",
            {"success": False, "error": "missing_app_slug"},
        )

    raw_configurable = config.get("configurable")
    configurable: dict[str, Any] = raw_configurable if isinstance(raw_configurable, dict) else {}

    # Flow `afterSubmit` is the source of truth for which app/tool runs — do not require
    # the integration to appear on `_AgentToApps` if the workspace (or flow pin) can resolve it.
    effective_apps = _collect_effective_apps(configurable)
    agent_id = str(configurable.get("agent_id") or "").strip()
    user_id = str(configurable.get("user_id") or "").strip() or None

    resolved = await _db_resolve_app_for_agent(agent_id, user_id, app_slug)
    if resolved and resolved.get("id"):
        effective_apps = _upsert_app_for_slug(effective_apps, resolved)

    explicit_app_id = _explicit_app_id_from_flow(after)
    if explicit_app_id:
        pinned = await _db_fetch_app_by_id(explicit_app_id)
        if pinned and pinned.get("id"):
            effective_apps = _upsert_app_for_slug(
                effective_apps, {"id": pinned["id"], "slug": app_slug},
            )
        else:
            effective_apps = _upsert_app_for_slug(
                effective_apps, {"id": explicit_app_id, "slug": app_slug},
            )

    if not _apps_include_slug(effective_apps, app_slug):
        return (
            f"No «{app_slug}» installation could be resolved for this flow "
            "(workspace app, agent link, or flow `appId`). Add the app in the workspace "
            "or set `afterSubmit.appId` on the flow.",
            {"success": False, "error": "app_not_connected", "appSlug": app_slug},
        )

    tc_tool = _resolve_registry_tool(tool_registry, app_slug, tool_name)
    if not tc_tool:
        return (
            f"Tool `{tool_name}` is not available.",
            {"success": False, "error": "tool_not_found", "toolName": tool_name},
        )

    exec_config: RunnableConfig = {
        **config,
        "configurable": {**configurable, "apps": effective_apps},
    }
    try:
        ainv = getattr(tc_tool, "ainvoke", None)
        if ainv is not None:
            tool_out = await ainv(submitted, config=exec_config)
        else:
            tool_out = tc_tool.invoke(submitted, config=exec_config)  # type: ignore[union-attr]
    except Exception as e:
        logger.warning("use_skill app_action failed: %s", e, exc_info=True)
        return (
            f"Flow «{parsed.display}» ran but the app action failed.",
            {"success": False, "error": str(e), "toolName": tool_name},
        )
    return (
        f"Flow «{parsed.display}» completed.",
        {
            "success": True,
            "agent_skill_id": agent_skill_id,
            "submitted": submitted,
            "tool_result": tool_out,
        },
    )


async def _run_after_submit(
    config: RunnableConfig,
    parsed: _ParsedSkill,
    agent_skill_id: str,
    submitted: dict[str, str],
) -> _SkillResult:
    """Dispatch to the configured ``afterSubmit`` action (webhook | app_action | none)."""
    raw_after = parsed.cs.get("afterSubmit")
    after: dict[str, Any] = raw_after if isinstance(raw_after, dict) else {}
    action = str(after.get("action") or "none").strip().lower()

    if action in ("webhook", "http"):
        await _fire_webhook(after, agent_skill_id, submitted)
    elif action == "app_action":
        return await _run_app_action(config, after, parsed, agent_skill_id, submitted)

    return (
        f"Flow «{parsed.display}» completed.",
        {"success": True, "agent_skill_id": agent_skill_id, "submitted": submitted},
    )


def _format_required_field_labels(form_fields: List[dict[str, Any]]) -> str:
    labels = [
        str(f.get("label") or f.get("id") or f.get("name") or "").strip()
        for f in form_fields
        if isinstance(f, dict) and f.get("required")
    ]
    return ", ".join(label for label in labels if label)


# ── Tool entry points ──────────────────────────────────────────────────────────


@tool(response_format="content_and_artifact")
async def use_skill(
    config: RunnableConfig,
    agent_skill_id: str,
    context: str = "",
):
    """Invoke a workspace custom skill by its AgentSkills row id (web runtime).

    For **AI** skills, returns behavioral instructions for the assistant. For
    **flow** skills, raises an interrupt so the UI renders the configured form;
    after the user submits, the configured `afterSubmit` action runs.

    Args:
        agent_skill_id: The AgentSkills.id for this custom skill.
        context: Optional short note from the model on why this skill was chosen.
    """
    parsed = _parse_custom_skill_row(config, agent_skill_id)
    if isinstance(parsed, tuple):
        return parsed

    if parsed.kind == "ai":
        return _build_ai_skill_response(parsed, agent_skill_id, context)

    if parsed.kind == "flow":
        form_fields = _flow_form_fields(parsed)
        tool_args: dict[str, Any] = {"agent_skill_id": agent_skill_id}
        if form_fields:
            tool_args["form_schema"] = {"fields": form_fields}

        interrupt_value = merge_interrupt_value(
            config,
            tool_name=_USE_SKILL,
            tool_display_name=parsed.display,
            tool_args=tool_args or None,
        )
        response = interrupt(value=interrupt_value)

        try:
            user_approved = response.get("decisions", [])[0].get("type") == "approve"
        except (IndexError, AttributeError, TypeError):
            user_approved = False
        if not user_approved:
            return (
                "User declined the custom flow.",
                {"success": False, "agent_skill_id": agent_skill_id},
            )

        submitted = _normalize_submitted_fields(response.get("fields"))
        return await _run_after_submit(config, parsed, agent_skill_id, submitted)

    return (
        f"Unknown custom skill kind: {parsed.kind}",
        {"success": False, "error": "unknown_kind"},
    )


@tool(response_format="content_and_artifact")
async def use_skill_channel(
    config: RunnableConfig,
    agent_skill_id: str,
    fields: dict[str, str] | None = None,
    context: str = "",
):
    """Invoke a workspace custom skill from a channel runtime (no interrupt form).

    For **AI** skills, returns behavioral instructions (same as the web variant).
    For **flow** skills, the LLM must collect every required form field from the
    user in chat and pass them in via ``fields``. The tool validates the dict
    against the skill's required-field config; if anything is missing it returns
    an error so the LLM can ask the user and retry. When complete it runs the
    skill's ``afterSubmit`` action (webhook or app tool) directly.

    Args:
        agent_skill_id: The AgentSkills.id for this custom skill.
        fields: Submitted form values keyed by field id, collected from the user
            in chat. Required for flow skills; ignored for AI skills.
        context: Optional short note from the model on why this skill was chosen.
    """
    parsed = _parse_custom_skill_row(config, agent_skill_id)
    if isinstance(parsed, tuple):
        return parsed

    if parsed.kind == "ai":
        return _build_ai_skill_response(parsed, agent_skill_id, context)

    if parsed.kind == "flow":
        form_fields = _flow_form_fields(parsed)
        submitted = _normalize_submitted_fields(fields if isinstance(fields, dict) else {})

        ok, err = validate_required_form_fields(form_fields, submitted)
        if not ok:
            required_labels = _format_required_field_labels(form_fields)
            hint = (
                f"{err}. Ask the user for the missing value, then call "
                f"`use_skill_channel` again with all required fields"
            )
            if required_labels:
                hint += f": {required_labels}."
            else:
                hint += "."
            return (
                hint,
                {
                    "success": False,
                    "error": "missing_required_field",
                    "agent_skill_id": agent_skill_id,
                    "required_fields": required_labels,
                },
            )

        return await _run_after_submit(config, parsed, agent_skill_id, submitted)

    return (
        f"Unknown custom skill kind: {parsed.kind}",
        {"success": False, "error": "unknown_kind"},
    )


def get_custom_skill_tools():
    return [use_skill, use_skill_channel]
