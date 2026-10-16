import logging
from typing import Any, List, Optional

from langchain_core.tools import BaseTool, tool
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import Field

from .models import HumanHandoffResponse
from common.tools.interrupt_payload import merge_interrupt_value
from common.tools.dynamic_args_schema import build_args_schema, split_kwargs_by_base
from common.modules.handoff_client import trigger_handoff_api
from common.modules.interrupt_client import create_interrupt
from common.tools.crm_ai_form_fields import crm_ai_keys_for_entity, strip_ai_populated_form_fields

logger = logging.getLogger(__name__)

_HANDOFF_TOOL = "human_handoff_trigger"
_HANDOFF_ENTITY = "HANDOFF"

# Fallback when the server sends no skill form (ids align with default HANDOFF CrmFieldDefinition keys).
_DEFAULT_FORM_FIELDS: list[dict[str, Any]] = [
    {
        "name": "visitorName",
        "id": "visitorName",
        "label": "Your name",
        "type": "text",
        "required": True,
    },
    {
        "name": "visitorEmail",
        "id": "visitorEmail",
        "label": "Your email",
        "type": "email",
        "required": True,
    },
]


def _get_handoff_form_fields(config: RunnableConfig) -> list:
    """Form fields for the interrupt: skill_configs[human_handoff_trigger] or legacy skill_config.

    Strips HANDOFF keys the model fills via tool args (priority, reason/summary). Falls back to
    default name/email when nothing remains.
    """
    c = config.get("configurable", {}) or {}
    fields: list | None = None

    skill_configs = c.get("skill_configs") or {}
    if isinstance(skill_configs, dict):
        sc = skill_configs.get(_HANDOFF_TOOL) or {}
        if isinstance(sc, dict):
            form = sc.get("form") or {}
            if isinstance(form, dict):
                raw = form.get("fields")
                if isinstance(raw, list) and len(raw) > 0:
                    fields = raw

    if fields is None:
        legacy = c.get("skill_config") or {}
        if isinstance(legacy, dict):
            form = legacy.get("form") or {}
            if isinstance(form, dict):
                raw = form.get("fields")
                if isinstance(raw, list) and len(raw) > 0:
                    fields = raw

    if not fields:
        return [dict(f) for f in _DEFAULT_FORM_FIELDS]

    normalized: list[dict[str, Any]] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        field = dict(f)
        field.setdefault("name", field.get("id", ""))
        field.setdefault("id", field.get("name", ""))
        normalized.append(field)

    ai_keys = crm_ai_keys_for_entity(config, _HANDOFF_ENTITY)
    filtered = strip_ai_populated_form_fields(normalized, ai_keys)
    if filtered:
        return filtered
    return [dict(f) for f in _DEFAULT_FORM_FIELDS]


@tool(response_format="content_and_artifact")
async def human_handoff_trigger(
    config: RunnableConfig,
    priority: str,
    summary: str,
):
    """Escalate to a human agent. Use when the user asks for a person or the situation needs a human.

    Args:
        priority: One of LOW, MEDIUM, HIGH, URGENT — sent to NestJS as handoff priority (not shown in the form).
        summary: Short reason for escalation from the conversation — sent as handoff summary/reason (not in the form).
    """
    form_fields = _get_handoff_form_fields(config)

    tool_args: dict[str, Any] = {}
    if form_fields:
        tool_args["form_schema"] = {"fields": form_fields}

    interrupt_value = merge_interrupt_value(
        config,
        tool_name=_HANDOFF_TOOL,
        tool_display_name="Human handoff",
        tool_args=tool_args if tool_args else None,
    )

    response = interrupt(value=interrupt_value)

    try:
        decision_type = response.get("decisions", [])[0].get("type")
        user_approved = decision_type == "approve"
    except (IndexError, AttributeError, TypeError):
        user_approved = False

    if not user_approved:
        return (
            "User rejected to start human handoff",
            HumanHandoffResponse(
                text="User rejected to start human handoff", success=False
            ),
        )

    configurable = config.get("configurable", {}) or {}
    chat_id = configurable.get("chat_id")
    agent_id = configurable.get("agent_id")
    session_id = configurable.get("session_id")
    user_id: str | None = configurable.get("user_id")

    raw_fields = response.get("fields")
    submitted: dict[str, Any] = {}
    if isinstance(raw_fields, dict):
        for k, v in raw_fields.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)):
                if v:  # keep non-empty media arrays/objects as-is
                    submitted[str(k)] = v
            else:
                s = str(v).strip()
                if s:
                    submitted[str(k)] = s

    if not chat_id or not agent_id:
        logger.error(
            "human_handoff_trigger missing chat_id or agent_id in config"
        )
        result = HumanHandoffResponse(
            text="Failed to trigger human handoff (missing session context)",
            success=False,
        )
        return result.text, result

    if not session_id:
        logger.error("human_handoff_trigger missing session_id in config")
        result = HumanHandoffResponse(
            text="Failed to trigger human handoff (missing session context)",
            success=False,
        )
        return result.text, result

    logger.info(
        "human_handoff_trigger: calling NestJS handoff chat_id=%s agent_id=%s "
        "session_id=%s priority=%s field_keys=%s",
        chat_id,
        agent_id,
        session_id,
        (priority or "MEDIUM"),
        sorted(submitted.keys()),
    )
    success = await trigger_handoff_api(
        agent_chat_id=chat_id,
        session_id=session_id,
        agent_id=agent_id,
        priority=priority or "MEDIUM",
        summary=summary or "",
        fields=submitted,
        end_user_id=user_id,
    )

    if not success:
        logger.warning(
            "human_handoff_trigger: NestJS handoff failed chat_id=%s — "
            "user may see 'unable to connect to human'; check logs above for HTTP status/body",
            chat_id,
        )
        result = HumanHandoffResponse(
            text="Failed to trigger human handoff", success=False
        )
    else:
        logger.info(
            "human_handoff_trigger: NestJS handoff succeeded chat_id=%s", chat_id
        )
        result = HumanHandoffResponse(
            text="Human handoff triggered successfully", success=True
        )
    return result.text, result


# ── Channel-runtime handoff tool ───────────────────────────────────────────────
#
# Same pattern as the aide channel tools: workspace-configured `form.fields` are
# exposed as typed args alongside the base `priority` + `summary` so the LLM is
# forced (by the args_schema) to collect them from the user before invoking.
# Built per-turn by ``channels/ai/nodes._get_tools`` using
# ``build_human_handoff_trigger_channel_tool(form_fields)``.

_VALID_HANDOFF_PRIORITIES = ("LOW", "MEDIUM", "HIGH", "URGENT")


_HANDOFF_CHANNEL_BASE_FIELDS: dict[str, tuple[Any, Any]] = {
    "summary": (str, Field(
        ..., description=(
            "Short reason for escalation — derived by the assistant from the "
            "conversation. Not asked of the user directly."
        ),
    )),
}


def _coerce_priority(value: Any) -> str:
    """Coerce a workspace-supplied priority value to one of LOW/MEDIUM/HIGH/URGENT.
    Defaults to MEDIUM when missing or invalid."""
    if not isinstance(value, str):
        return "MEDIUM"
    normalized = value.strip().upper()
    return normalized if normalized in _VALID_HANDOFF_PRIORITIES else "MEDIUM"


async def _human_handoff_trigger_channel_impl(
    *,
    config: RunnableConfig,
    base: dict[str, Any],
    submitted: dict[str, str],
) -> tuple[str, HumanHandoffResponse]:
    """Channel handoff trigger. `summary` is the static base arg; `priority`
    and everything else come from workspace `form.fields` (or default to
    MEDIUM when no `priority` field is configured)."""
    configurable = config.get("configurable", {}) or {}
    chat_id = configurable.get("chat_id")
    agent_id = configurable.get("agent_id")
    session_id = configurable.get("session_id")
    user_id: str | None = configurable.get("user_id")

    summary = str(base.get("summary") or "").strip()
    priority = _coerce_priority(submitted.get("priority"))

    if not chat_id or not agent_id or not session_id:
        logger.error("human_handoff_trigger_channel missing chat_id, agent_id, or session_id")
        result = HumanHandoffResponse(
            text="Failed to trigger human handoff (missing session context)",
            success=False,
        )
        return result.text, result

    logger.info(
        "human_handoff_trigger_channel: chat_id=%s agent_id=%s session_id=%s priority=%s "
        "configured_field_count=%d",
        chat_id, agent_id, session_id, priority, len(submitted),
    )

    success = await trigger_handoff_api(
        agent_chat_id=chat_id,
        session_id=session_id,
        agent_id=agent_id,
        priority=priority,
        summary=summary,
        fields=submitted,
        end_user_id=user_id,
    )

    if not success:
        logger.warning("human_handoff_trigger_channel: handoff failed chat_id=%s", chat_id)
        result = HumanHandoffResponse(text="Failed to trigger human handoff", success=False)
    else:
        logger.info("human_handoff_trigger_channel: handoff succeeded chat_id=%s", chat_id)
        result = HumanHandoffResponse(text="Human handoff triggered successfully", success=True)
        try:
            await create_interrupt(
                agent_chat_id=chat_id,
                interrupt_type="channel_handoff",
                tool_display_name="Channel handoff triggered",
                tool_name="human_handoff_trigger_channel",
                status="auto",
                tool_args={"submitted": submitted, "priority": priority} if submitted else {"priority": priority},
            )
        except Exception as e:
            logger.warning(
                "persist channel handoff interrupt failed chat_id=%s: %s",
                chat_id,
                e,
            )
    return result.text, result


def build_human_handoff_trigger_channel_tool(form_fields: list[dict] | None) -> BaseTool:
    """Build a per-skill channel handoff tool. `summary` plus workspace
    `form.fields`. If the workspace configures a `priority` field, the LLM
    collects it; otherwise priority defaults to MEDIUM."""
    base_fields = _HANDOFF_CHANNEL_BASE_FIELDS
    base_keys = set(base_fields.keys())
    schema = build_args_schema(
        name="human_handoff_trigger_channel",
        base_fields=base_fields,
        configured_form_fields=form_fields,
    )

    @tool(
        "human_handoff_trigger_channel",
        args_schema=schema,
        response_format="content_and_artifact",
    )
    async def human_handoff_trigger_channel(
        config: RunnableConfig, **kwargs: Any
    ) -> tuple[str, HumanHandoffResponse]:
        """Escalate to a human agent on messaging channels (WhatsApp, Instagram,
        Messenger, Gmail). Triggers immediately — no interrupt form. You MUST
        supply every required argument shown in this tool's schema; the
        workspace configures which fields are required."""
        base, submitted = split_kwargs_by_base(kwargs, base_keys)
        return await _human_handoff_trigger_channel_impl(
            config=config,
            base=base,
            submitted=submitted,
        )

    return human_handoff_trigger_channel


def get_human_handoff_tools() -> List[BaseTool]:
    """Web/widget handoff tool only. The channel variant is built per-turn by
    ``build_human_handoff_trigger_channel_tool`` directly from skill config and
    is intentionally NOT registered here."""
    return [human_handoff_trigger]
