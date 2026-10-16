"""Channel-first system prompt builder.

Replaces the prior import-from-``agents/graph/prompt.py`` approach. Every section
is shaped for messaging-channel runtimes: tool names match the ``_channel``
variants actually bound on the model (see ``skill_map.CHANNEL_SKILL_MAP``), there
is no interrupt-form copy, web "widget renders cards" guidance is dropped, and
each provider supplies its own format rules, few-shots, and role block via the
optional methods on ``ChannelProvider``.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from app.modules.channels.ai.skill_map import CHANNEL_SKILL_MAP
from app.modules.channels.schemas import NormalizedInboundMessage

# ---------------------------------------------------------------------------
# additional_params + prompt_params (legacy entry points, still used by service)
# ---------------------------------------------------------------------------


def build_additional_params(
    agent_config: dict, inbound: NormalizedInboundMessage
) -> Dict[str, Any]:
    """Build the dict passed into the channel graph (agent row + routing ids)."""
    params: Dict[str, Any] = {
        **agent_config,
        "channel": inbound.channel,
        "chat_id": inbound.chatId,
        "session_id": "",
    }
    if inbound.sender.phone:
        params["sender_phone"] = inbound.sender.phone
    if inbound.sender.displayName:
        params["sender_display_name"] = inbound.sender.displayName
        params["customer_name"] = inbound.sender.displayName
    if inbound.sender.email:
        params["customer_email"] = inbound.sender.email
    if inbound.meta and inbound.meta.whatsappPhoneNumberId:
        params["whatsapp_phone_number_id"] = inbound.meta.whatsappPhoneNumberId
    return params


def build_channel_prompt_params(
    agent_config: dict, inbound: NormalizedInboundMessage
) -> Dict[str, Any]:
    """Build ``prompt_params`` for the channel builder — handoff disabled for channels."""
    config_dict = agent_config.get("config", {})
    if not isinstance(config_dict, dict):
        config_dict = {}

    prompt_params = dict(config_dict)
    prompt_params["channel"] = inbound.channel
    prompt_params["agent_id"] = inbound.agentId
    prompt_params["human_handoff_enabled"] = False
    return prompt_params


# ---------------------------------------------------------------------------
# Customer context
# ---------------------------------------------------------------------------


def build_customer_context(inbound: NormalizedInboundMessage) -> Dict[str, Any]:
    """Per-message customer block surfaced verbatim in the system prompt."""
    ctx: Dict[str, Any] = {
        "channel": inbound.channel,
        "display_name": (inbound.sender.displayName or "").strip(),
        "phone": (inbound.sender.phone or "").strip(),
        "email": (inbound.sender.email or "").strip(),
        "external_user_id": (inbound.sender.id or "").strip(),
    }
    if inbound.message and inbound.message.subject:
        ctx["subject"] = inbound.message.subject.strip()
    return ctx


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------

_INVISIBLE_CHARS_RE = re.compile(
    r"[​‌‍⁠﻿]"
)  # zero-width space/non-joiner/joiner, word joiner, BOM
_SKILL_PROMPT_MAX_LEN = 420
# Fragments like "when greet - trigger" or "trigger on hi" carry no real
# instruction; bin anything that's short AND doesn't end with sentence
# punctuation AND contains a tell-tale "trigger" keyword.
_FRAGMENT_TRIGGER_RE = re.compile(r"\btrigger\b", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.!?]")


def _looks_like_internal_skill_config_blob(text: str) -> bool:
    """True if a triggerCondition.prompt looks like a pasted JSON/YAML form schema."""
    s = text.strip()
    if len(s) < 80:
        return False
    lower = s.lower()
    if '"fields"' in s or "'fields'" in s or "form.fields" in lower:
        return True
    if '"type"' in s and '"label"' in s and ("required" in lower or "mapsTo" in lower):
        return True
    if s.startswith(("{", "[")) and ("customskill" in lower or "custom_skill" in lower):
        return True
    return False


def _sanitize_user_skill_prompt(text: str) -> str:
    """Strip invisible chars, drop too-short / too-blob-y prompts, length-cap the rest.

    "Fragmentary trigger" prompts (e.g. ``"when greet - trigger"``,
    ``"trigger on refund"``) are dropped — they're shorter than a single
    sentence and contain the keyword ``trigger`` without any actual rule. These
    appeared in real workspace configs and would otherwise replace the canonical
    skill defaults.
    """
    if not text:
        return ""
    cleaned = _INVISIBLE_CHARS_RE.sub("", text).strip()
    if not cleaned:
        return ""
    if _looks_like_internal_skill_config_blob(cleaned):
        return ""
    if (
        _FRAGMENT_TRIGGER_RE.search(cleaned)
        and not _SENTENCE_END_RE.search(cleaned)
        and len(cleaned) < 60
    ):
        return ""
    if len(cleaned) > _SKILL_PROMPT_MAX_LEN:
        cleaned = cleaned[: _SKILL_PROMPT_MAX_LEN - 1].rstrip() + "…"
    return cleaned


_MAX_SKILL_EXAMPLES = 6


def _render_skill_examples(trigger: object) -> list[str]:
    """Indented guidance lines from ``triggerCondition.examples``.

    Examples are workspace-curated boundary cases: ``shouldInvoke: true``
    entries show messages that must trigger the skill, ``false`` entries are
    near-misses that must NOT (they guard against over-triggering). Rendered
    as two compact sub-bullets under the skill line.
    """
    if not isinstance(trigger, dict):
        return []
    raw = trigger.get("examples")
    if not isinstance(raw, list):
        return []
    positives: list[str] = []
    negatives: list[str] = []
    for ex in raw[:_MAX_SKILL_EXAMPLES]:
        if not isinstance(ex, dict):
            continue
        said = " ".join(str(ex.get("userSays") or "").split())[:160]
        if not said:
            continue
        note = " ".join(str(ex.get("note") or "").split())[:120]
        entry = f'"{said}"' + (f" ({note})" if note else "")
        (positives if ex.get("shouldInvoke") else negatives).append(entry)
    lines: list[str] = []
    if positives:
        lines.append("  - Invoke for messages like: " + "; ".join(positives) + ".")
    if negatives:
        lines.append("  - Do NOT invoke for: " + "; ".join(negatives) + ".")
    return lines


# ---------------------------------------------------------------------------
# Skill defaults & required-fields hints (channel-correct names)
# ---------------------------------------------------------------------------

_SKILL_DEFAULTS: Dict[str, str] = {
    "human_handoff_trigger_channel": (
        "Transfer the conversation to a human agent. Call when the user explicitly "
        "asks for a human, expresses sustained frustration, or the issue cannot be "
        "resolved automatically."
    ),
    "create_ticket_aide_channel": (
        "Create a support ticket. Call when the user reports an order issue, "
        "complaint, refund/return request, product defect, or service failure. "
        "The `reason` argument is REQUIRED and must be a concrete phrase "
        "derived from the conversation (e.g. 'damaged item on arrival', "
        "'refund requested for order #1242', 'wrong size delivered'). Never "
        "pass placeholders like 'support', 'inquiry', or 'help' — rejected."
    ),
    "create_lead_aide_channel": (
        "Register the user as a sales lead. Call when the user expresses purchase "
        "intent, requests a callback or quote, or asks to be contacted by sales."
    ),
    "web_search": (
        "Search the web for current information. Call when the user asks for online "
        "info you don't have."
    ),
    "get_products": (
        "Fetch live product listings from the connected store. Call for any shopping "
        "intent — browse, recommendations, pricing, or availability queries."
    ),
    "get_order_status": (
        "Look up the status of a customer order. Call when the user asks about "
        "their order, shipping, or delivery."
    ),
    "send_email": (
        "Send an email on behalf of the user. Call only when the user explicitly "
        "asks to send an email."
    ),
    "search_emails": (
        "Search the user's inbox when they ask to find an email."
    ),
    "create_event": (
        "Create a calendar event when the user wants to schedule a meeting."
    ),
    "list_events": (
        "List upcoming calendar events when the user asks about their schedule."
    ),
    "send_slack_message": (
        "Send a Slack message when the user wants to notify someone on Slack."
    ),
}

# Required fields the LLM must supply itself, since channels have no interrupt form.
# These match the actual tool signatures in
# packages/shared/common/tools/providers/aide/tools.py and
# packages/shared/common/tools/providers/human_handoff/tools.py — keep in sync
# if the tool args change. The LLM is responsible for asking the user for any
# field it doesn't already have from the conversation.
# Tools whose required/optional field list is built dynamically per-skill from
# the workspace `form.fields` config. Their entries are intentionally NOT in
# `_SKILL_REQUIRED_FIELDS` below — `_build_skills_section` emits a per-skill
# field listing for them instead (see `_render_dynamic_skill_fields`).
_DYNAMIC_FIELD_TOOLS: set[str] = {
    "create_ticket_aide_channel",
    "create_lead_aide_channel",
    "human_handoff_trigger_channel",
}


_SKILL_REQUIRED_FIELDS: Dict[str, str] = {
    # Reserved for tools whose required args are STATIC (i.e. they're hardcoded
    # in the function signature and don't vary per skill config). Tools in
    # `_DYNAMIC_FIELD_TOOLS` are handled by `_render_dynamic_skill_fields` and
    # must NOT appear here.
}


def _resolve_channel_tool_name(skill: dict) -> str:
    """Map an interrupt skill toolName to its channel-safe variant if one exists."""
    name = (skill.get("toolName") or "").strip()
    return CHANNEL_SKILL_MAP.get(name, name)


def _is_skill_enabled(skill: dict) -> bool:
    cfg = skill.get("config") or {}
    if not isinstance(cfg, dict):
        return True
    return cfg.get("_enabled") is not False


def _skill_requires_confirmation(skill: dict) -> bool:
    """Whether a write-skill must read back & confirm fields before firing on a channel.

    Per-skill flag `config.requireConfirmation`; defaults to True (confirm) when unset.
    Set to False on the AgentSkills row to let the model call the tool directly.
    Customer/lead creation always skips the read-back step on channels.
    """
    if _resolve_channel_tool_name(skill) == "create_lead_aide_channel":
        return False
    cfg = skill.get("config") or {}
    if not isinstance(cfg, dict):
        return True
    return cfg.get("requireConfirmation") is not False


def _visible_channel_skills(prompt_params: dict) -> list[dict]:
    """All enabled skills. Handoff is kept because the channel variant exists."""
    skills = prompt_params.get("skills") or []
    visible: list[dict] = []
    for s in skills:
        if not isinstance(s, dict):
            continue
        if not _is_skill_enabled(s):
            continue
        visible.append(s)
    visible.sort(key=lambda s: (str(s.get("toolName") or ""), str(s.get("id") or "")))
    return visible


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _persona_fallback(prompt_params: dict) -> tuple[str, str]:
    """Resolve (name, description) with a sensible fallback chain so the block is never blank."""
    persona = prompt_params.get("persona") or {}
    if not isinstance(persona, dict):
        persona = {}
    name = (persona.get("personaName") or "").strip()
    desc = (persona.get("personaDescription") or "").strip()

    if not name:
        for key in ("brandName", "workspaceName", "companyName"):
            candidate = (prompt_params.get(key) or "").strip()
            if candidate:
                name = f"{candidate} Support"
                break
    if not name:
        name = "Support assistant"

    if not desc:
        desc = (
            "Friendly, concise, and solution-driven. Helps customers quickly and "
            "answers from facts you can verify; never invents product details or "
            "policies."
        )
    return name, desc


_CHANNEL_LABELS = {
    "whatsapp": "WhatsApp",
    "messenger": "Facebook Messenger",
    "instagram": "Instagram DM",
    "gmail": "email",
}


def _build_role_section(prompt_params: dict, customer_ctx: dict, override: str) -> str:
    if override and override.strip():
        return override.strip() + "\n"
    name, desc = _persona_fallback(prompt_params)
    channel_label = _CHANNEL_LABELS.get(customer_ctx.get("channel", ""), "this channel")
    return (
        f"You are **{name}**, replying via {channel_label}.\n"
        f"Personality: {desc}\n"
    )


def _build_customer_section(customer_ctx: dict) -> str:
    name = customer_ctx.get("display_name") or "unknown"
    contact = customer_ctx.get("phone") or customer_ctx.get("email") or "—"
    subject = customer_ctx.get("subject")
    lines = [
        "\n# Customer",
        f"- Name: {name}",
        f"- Channel: {customer_ctx.get('channel', 'unknown')}",
        f"- Contact: {contact}",
    ]
    if subject:
        lines.append(f"- Email subject: {subject}")
    lines.append("")
    return "\n".join(lines)


_MARKDOWN_GUIDANCE = {
    "full": "Use markdown: **bold** for emphasis, bullet lists, numbered steps.",
    "whatsapp": "Use WhatsApp formatting only — *bold*, _italic_. No `#` headings, no `**bold**`, no bullet symbols.",
    "none": "Plain text only. No markdown, no `**bold**`, no bullet points, no headings.",
}

_EMOJI_GUIDANCE = {
    "sparingly": (
        "Use emoji rarely — at most one per reply, and only when it adds genuine "
        "warmth (e.g. an apology). Do not use emoji as decoration or sales energy."
    ),
    "natural": (
        "Match the user's emoji use — if they use emoji you may reply in kind, "
        "otherwise stay plain. Never sprinkle emoji to seem upbeat."
    ),
    "none": "Do not use any emoji.",
}

_LINK_GUIDANCE = {
    "allow": "Links are fine when they help.",
    "avoid": "Avoid deep links unless strictly necessary — most users won't tap them.",
    "deny": "Do not include URLs or links in the reply.",
}


def _build_format_section(format_rules: dict) -> str:
    if not format_rules:
        return ""
    parts: list[str] = ["\n# Channel format rules"]
    tone = (format_rules.get("tone") or "").strip()
    if tone:
        parts.append(f"- Tone: {tone}")
    max_chars = int(format_rules.get("max_chars") or 0)
    if max_chars > 0:
        parts.append(
            f"- Keep the entire reply under {max_chars} characters. If more detail is "
            "needed, give the most important point first and offer to elaborate."
        )
    md = format_rules.get("markdown")
    if md in _MARKDOWN_GUIDANCE:
        parts.append(f"- {_MARKDOWN_GUIDANCE[md]}")
    greeting = (format_rules.get("greeting") or "").strip()
    if greeting:
        parts.append(f"- Greeting: {greeting}")
    sign_off = (format_rules.get("sign_off") or "").strip()
    if sign_off:
        parts.append(f"- Sign-off: {sign_off}")
    emoji = format_rules.get("emoji")
    if emoji in _EMOJI_GUIDANCE:
        parts.append(f"- {_EMOJI_GUIDANCE[emoji]}")
    links = format_rules.get("links")
    if links in _LINK_GUIDANCE:
        parts.append(f"- {_LINK_GUIDANCE[links]}")
    parts.append("")
    return "\n".join(parts)


# Skills that WRITE/PERSIST data — these require explicit user confirmation of
# the key fields before the tool fires, since channels have no interrupt form to
# review/correct values before submission.
_WRITE_SKILLS: set[str] = {
    "create_lead_aide_channel",
    "create_ticket_aide_channel",
}


def _render_dynamic_skill_fields(skill: dict) -> list[str]:
    """Per-skill prompt lines listing the workspace-configured form fields by
    id, label, and type. Read-back guidance is gated on `_skill_requires_confirmation`."""
    cfg = skill.get("config") if isinstance(skill.get("config"), dict) else {}
    form = cfg.get("form") if isinstance(cfg, dict) and isinstance(cfg.get("form"), dict) else {}
    raw_fields = form.get("fields") if isinstance(form.get("fields"), list) else []

    required_tokens: list[str] = []
    optional_tokens: list[str] = []
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or f.get("name") or "").strip()
        if not fid:
            continue
        label = (str(f.get("label") or fid).strip()) or fid
        ftype = (str(f.get("type") or "text").strip()) or "text"
        token = f"`{fid}` — {label} ({ftype})"
        (required_tokens if f.get("required") else optional_tokens).append(token)

    extra: list[str] = []
    if not required_tokens and not optional_tokens:
        extra.append(
            "  - This skill has no workspace-configured fields beyond the "
            "derived `summary`. Do not ask the user for additional details "
            "(name, email, phone, etc.) — they are not collected."
        )
        return extra

    if required_tokens:
        extra.append(
            f"  - **Required fields to collect from the user**: "
            f"{', '.join(required_tokens)}."
        )
    if optional_tokens:
        extra.append(
            f"  - Optional fields (only include if the user volunteers them; "
            f"do not ask proactively): {', '.join(optional_tokens)}."
        )
    extra.append(
        "  - `summary` is always derived by the assistant from the conversation "
        "— do not ask the user for it."
    )
    if required_tokens and _skill_requires_confirmation(skill):
        extra.append(
            "  - Read each collected required value back to the user and wait "
            "for a yes before calling the tool. There is no form on this "
            "channel to correct values after submission."
        )
    elif required_tokens:
        extra.append(
            "  - Confirmation is disabled for this skill — call the tool as "
            "soon as you have every required value."
        )
    return extra


def _render_use_skill_channel_block(skill: dict) -> tuple[str, list[str]]:
    """Build ``(description, extra_lines)`` for a single ``use_skill_channel`` entry.

    AI-kind skills get a minimal "call with id" line — no field collection.
    Flow-kind skills enumerate the configured form fields so the LLM knows
    exactly what to ask the user for; the read-back rule is gated on the per-row
    ``config.requireConfirmation`` flag (same pattern as the aide tools).
    """
    sid = str(skill.get("id") or "").strip()
    cfg = skill.get("config") if isinstance(skill.get("config"), dict) else {}
    trigger = cfg.get("triggerCondition") if isinstance(cfg, dict) else None
    user_prompt = (
        _sanitize_user_skill_prompt((trigger or {}).get("prompt") or "")
        if isinstance(trigger, dict)
        else ""
    )
    base = user_prompt or "Use when the user needs this workspace-defined custom skill."

    cs_raw = cfg.get("customSkill") if isinstance(cfg, dict) else None
    cs: dict = cs_raw if isinstance(cs_raw, dict) else {}
    kind = str(cs.get("kind") or "ai").strip().lower()

    # AI kind — behavioral instructions are returned by the tool itself; the LLM
    # just needs the skill id.
    if kind != "flow":
        if not sid:
            return base, []
        return (
            f"{base} Call `use_skill_channel` with `agent_skill_id`=\"{sid}\". "
            "No field collection is needed for this skill.",
            [],
        )

    # Flow kind — list the configured fields so the LLM knows what to collect.
    raw_form = cs.get("form") if isinstance(cs.get("form"), dict) else {}
    raw_fields = raw_form.get("fields") if isinstance(raw_form.get("fields"), list) else []
    required_tokens: list[str] = []
    optional_tokens: list[str] = []
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or f.get("name") or "").strip()
        if not fid:
            continue
        label = (str(f.get("label") or fid).strip()) or fid
        ftype = (str(f.get("type") or "text").strip()) or "text"
        token = f"`{fid}` — {label} ({ftype})"
        (required_tokens if f.get("required") else optional_tokens).append(token)

    if not sid:
        return f"{base} (Skill is misconfigured: missing agent_skill_id.)", []

    desc = (
        f"{base} Collect the field values from the user in chat with one or two "
        f"friendly questions (never a numbered list), then call "
        f"`use_skill_channel` with `agent_skill_id`=\"{sid}\" and "
        "`fields`={<id>: <value>, …}."
    )

    extra: list[str] = []
    if required_tokens:
        extra.append(
            f"  - Required `fields` to collect from the user: "
            f"{', '.join(required_tokens)}."
        )
    if optional_tokens:
        extra.append(
            f"  - Optional `fields` (include only when the user volunteers them): "
            f"{', '.join(optional_tokens)}."
        )
    if required_tokens:
        if _skill_requires_confirmation(skill):
            extra.append(
                "  - **Read each collected value back to the user and wait for a "
                "yes** before calling the tool. There is no form on this channel "
                "to review or correct values after submission."
            )
        else:
            extra.append(
                "  - Confirmation is disabled for this skill — call the tool as "
                "soon as you have every required value."
            )

    return desc, extra


def _build_skills_section(prompt_params: dict) -> str:
    visible = _visible_channel_skills(prompt_params)
    if not visible:
        return ""

    lines: list[str] = [
        "\n# Available skills",
        "Use these tools proactively when intent matches. Call the tool immediately "
        "with arguments inferred from the conversation and customer context — do "
        "**not** ask the user for fields you can supply yourself.",
        "",
    ]

    confirm_tool_names = sorted({
        _resolve_channel_tool_name(s)
        for s in visible
        if _resolve_channel_tool_name(s) in _WRITE_SKILLS
        and _skill_requires_confirmation(s)
    })
    if confirm_tool_names:
        names_str = ", ".join(f"`{n}`" for n in confirm_tool_names)
        lines.extend([
            f"**Confirmation rule for write-skills** ({names_str}):",
            "- These tools persist a record to the workspace database. There is no "
            "form on this channel for the user to review or correct the values, "
            "so **you** are the safeguard.",
            "- The args_schema of each tool above is the single source of truth for "
            "which fields the LLM must collect. Focus on **required** args in the "
            "schema — derive any you can from the conversation (e.g. `summary`), and "
            "ask the user for the rest. Do **not** ask for optional args unless the "
            "user volunteers them. Never ask for fields that aren't in the schema "
            "(such as a generic 'name', 'email', or 'phone') just because they're "
            "typical for ticket creation — the workspace controls the field list.",
            "- Before calling the tool, read back each collected required value to "
            "the user and ask \"is that right?\" so they can correct typos or stale "
            "info. Only call the tool after the user has confirmed.",
            "- For `human_handoff_trigger_channel` you do **not** need the read-back "
            "step — escalate immediately.",
            "",
        ])

    for skill in visible:
        tool_name = _resolve_channel_tool_name(skill)
        if not tool_name:
            continue
        display = (skill.get("name") or tool_name).strip()
        cfg = skill.get("config") or {}
        trigger = cfg.get("triggerCondition") if isinstance(cfg, dict) else None
        user_prompt = (
            _sanitize_user_skill_prompt((trigger or {}).get("prompt") or "")
            if isinstance(trigger, dict)
            else ""
        )

        extra_lines: list[str] = []
        if tool_name == "use_skill_channel":
            desc, extra_lines = _render_use_skill_channel_block(skill)
        else:
            default = _SKILL_DEFAULTS.get(
                tool_name, "Use this tool when relevant to the user's request."
            )
            desc = default
            if user_prompt:
                desc += f" Workspace note: {user_prompt}"
            if tool_name in _DYNAMIC_FIELD_TOOLS:
                extra_lines = _render_dynamic_skill_fields(skill)

        lines.append(f"- **{display}** (`{tool_name}`): {desc}")
        lines.extend(_render_skill_examples(trigger))
        lines.extend(extra_lines)
        required = _SKILL_REQUIRED_FIELDS.get(tool_name)
        if required:
            if tool_name in _WRITE_SKILLS:
                lines.append(
                    f"  - You must supply: {required}. **Confirm each of these "
                    "explicitly with the user** before calling — read them back "
                    "and wait for a yes, even if the values already appeared in "
                    "the chat. There is no form on this channel to correct values "
                    "after the call."
                )
            else:
                lines.append(
                    f"  - You must supply: {required}. There is no confirmation "
                    "form on this channel — collect missing info in chat first if "
                    "absolutely necessary, then call the tool."
                )

    lines.append("")
    return "\n".join(lines)


# Channel-aware app tool inventory. Each entry: (tool_name, when/what + channel caveat).
_APP_TOOLS_BY_SLUG: Dict[str, list[tuple[str, str]]] = {
    "shopify": [
        ("get_products", "Browse the live product catalog (price, availability, recommendations)."),
        ("get_order_status", "Look up order status, tracking, and shipping updates."),
    ],
    "woocommerce": [
        ("get_products", "Browse the live product catalog (price, availability, recommendations)."),
        ("get_order_status", "Look up order status, tracking, and shipping updates."),
    ],
    "hubspot": [
        ("search_contacts", "Search CRM contacts by name, email, or keyword."),
        ("get_contact", "Get full details of a specific contact."),
        ("search_tickets", "Search support tickets by subject or status."),
        ("create_ticket", "Create a new support ticket in HubSpot."),
    ],
    "zendesk": [
        ("create_ticket", "Create a new support ticket in Zendesk."),
    ],
    "gmail": [
        ("list_messages", "List recent emails in the inbox."),
        ("search_messages", "Search inbox by keyword, sender, or date."),
        ("get_message", "Read the full content of a specific email."),
    ],
    "outlook": [
        ("list_outlook_messages", "List recent emails in the inbox."),
        ("search_outlook_messages", "Search inbox by keyword or sender."),
        ("get_outlook_message", "Read the full content of a specific email."),
    ],
    "gcalendar": [
        ("list_events", "List upcoming calendar events."),
        ("get_event_details", "Get full details of a specific event."),
        ("search_events", "Search events by keyword or attendee."),
    ],
    "calendly": [
        ("list_event_types_calendly", "List available meeting types and slots."),
        ("book_meeting_calendly", "Book a meeting slot — confirm email, date, and time first."),
        ("cancel_event_calendly", "Cancel a scheduled event."),
    ],
    "calcom": [
        ("list_event_types_calcom", "List available meeting types."),
        ("book_meeting_calcom", "Book a meeting slot — confirm email, date, and time first."),
        ("cancel_booking_calcom", "Cancel an existing booking."),
    ],
    "notion": [
        ("search_notion", "Search across the Notion workspace."),
        ("query_notion_database", "Query a Notion database with filters."),
    ],
    "slack": [
        ("slack_send_message", "Send a message to a Slack channel or user."),
        ("slack_search_messages", "Search Slack message history."),
    ],
    "airtable": [
        ("list_airtable_records", "List records in an Airtable base/table."),
        ("get_airtable_record", "Get a specific record by ID."),
    ],
    "gdrive": [
        ("search_drive_files", "Search Drive files by name or content."),
        ("get_drive_file_content", "Read the content of a specific file."),
    ],
}


def _connected_app_slugs(prompt_params: dict) -> list[str]:
    apps = prompt_params.get("apps") or []
    out: list[str] = []
    if isinstance(apps, list):
        for a in apps:
            if isinstance(a, dict):
                sl = str(a.get("slug") or a.get("name") or "").strip().lower()
                if sl:
                    out.append(sl)
    return sorted(set(out))


def _build_apps_section(prompt_params: dict, format_rules: dict) -> str:
    slugs = _connected_app_slugs(prompt_params)
    if not slugs:
        return ""
    md = format_rules.get("markdown", "full") if format_rules else "full"
    plain = md == "none"

    lines: list[str] = ["\n# Connected app tools"]
    has_shopify_like = bool(set(slugs) & {"shopify", "woocommerce"})

    for slug in slugs:
        tools = _APP_TOOLS_BY_SLUG.get(slug)
        if not tools:
            continue
        lines.append(f"\n**{slug.capitalize()}**")
        for tool_name, description in tools:
            lines.append(f"- `{tool_name}`: {description}")

    if has_shopify_like:
        if plain:
            catalog_line = (
                "Call `get_products` for any product or shopping query (browse, "
                "recommend, price, availability, specs), then describe the top "
                "1–3 results in plain text (name, price, one-line hook). Do not "
                "paste long lists. This channel has no product-card widget."
            )
        elif md == "whatsapp":
            catalog_line = (
                "Call `get_products` for any product or shopping query, then "
                "describe the top 1–3 results conversationally. If a catalog "
                "template is configured for this workspace it will carry the "
                "cards alongside your reply."
            )
        else:
            catalog_line = (
                "Call `get_products` for any product or shopping query, then "
                "summarise the top 1–3 results. Keep prose short."
            )
        lines.append("")
        lines.append(f"- {catalog_line}")
        lines.append(
            "- `get_products` and `get_order_status` are the **only** sources of "
            "truth for products and orders. Never invent details and never fall "
            "back to `search_knowledgebase` for product or order questions."
        )

    lines.append("")
    return "\n".join(lines)


def _build_kb_section(prompt_params: dict) -> str:
    has_kb = bool(prompt_params.get("entityIds")) or bool(prompt_params.get("folderIds"))
    if not has_kb:
        return ""
    slugs = set(_connected_app_slugs(prompt_params))
    has_shopify_like = bool(slugs & {"shopify", "woocommerce"})

    lines = ["\n# Knowledge base"]
    if has_shopify_like:
        # Tool routing is ambiguous if both KB and ecommerce are connected and
        # KB happens to contain product info. Force the model to prefer the
        # live catalog for anything product/order-shaped.
        lines.append(
            "- Use `search_knowledgebase` only for **non-product** questions: "
            "policies (returns, shipping, warranty), brand info, store hours, "
            "FAQs, and general guidance."
        )
        lines.append(
            "- For product questions (name, price, stock, specs, recommendations) "
            "and order questions, call `get_products` / `get_order_status` "
            "instead — never `search_knowledgebase`."
        )
    else:
        lines.append(
            "- Call `search_knowledgebase` for any user question that could "
            "plausibly be answered from your knowledge base."
        )
    lines.append(
        "- For greetings, thanks, and small talk, reply naturally without searching."
    )
    lines.append(
        "- Stay faithful to retrieved content; do not invent details that aren't there."
    )
    lines.append("")
    return "\n".join(lines)


def _build_crm_fields_section(prompt_params: dict) -> str:
    """Tell the model which CRM keys it must supply via tools and allowed option values."""
    fields = prompt_params.get("crmAiPopulatedFields") or []
    if not isinstance(fields, list) or len(fields) == 0:
        return ""

    lines: list[str] = [
        "\n# CRM fields you must populate",
        "These fields are not collected from the user — set them via tool arguments. "
        "Use the exact storage key and, for dropdowns, one of the allowed value codes.",
        "",
    ]
    by_entity: dict[str, list[dict]] = {}
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        ent = str(raw.get("entity") or "UNKNOWN").strip()
        by_entity.setdefault(ent, []).append(raw)
    for entity in sorted(by_entity.keys()):
        lines.append(f"**{entity}**")
        for f in sorted(by_entity[entity], key=lambda x: str(x.get("key") or "")):
            key = str(f.get("key") or "").strip()
            if not key:
                continue
            label = str(f.get("label") or key).strip()
            typ = str(f.get("type") or "").strip()
            req = "required" if f.get("required") else "optional"
            line = f"- `{key}` ({label}, {typ}, {req})"
            opts = f.get("options")
            if isinstance(opts, list) and opts:
                parts: list[str] = []
                for o in sorted(opts, key=lambda x: str(x.get("value") or "")):
                    if isinstance(o, dict):
                        v = str(o.get("value") or "").strip()
                        lab = str(o.get("label") or v).strip()
                        if v:
                            parts.append(f"`{v}`={lab}")
                if parts:
                    line += f" — allowed values: {', '.join(parts)}"
            lines.append(line)
        lines.append("")
    return "\n".join(lines)


def _build_domain_section(prompt_params: dict) -> str:
    persona = prompt_params.get("persona") or {}
    if not isinstance(persona, dict):
        return ""
    scope = (persona.get("scopeDescription") or persona.get("domainScope") or "").strip()
    if not scope:
        return ""
    return (
        "\n# Domain restriction\n"
        f"- Only answer questions within scope: **{scope}**.\n"
        "- For out-of-scope questions, politely decline in one sentence and redirect.\n"
    )


def _build_hard_rules_section() -> str:
    return (
        "\n# Hard rules\n"
        "- Never output image URLs or raw links to images, even if they appear in tool results.\n"
        "- Never invent product details, prices, policies, or order status — answer only from tool results or knowledge base.\n"
        "- Never mention tools, internal IDs, or that you are an AI by name.\n"
        "- Act, don't ask: when you have enough context to call a tool, call it. Only ask the user when information is truly missing.\n"
    )


# ---------------------------------------------------------------------------
# build_channel_base_prompt — the main entry point
# ---------------------------------------------------------------------------


def build_channel_base_prompt(
    prompt_params: dict,
    *,
    customer_ctx: dict | None = None,
    format_rules: dict | None = None,
    few_shots: str = "",
    role_block: str = "",
    channel_instructions: str = "",
) -> str:
    """Assemble the channel system prompt in channel-first order.

    Order: role → customer → format rules → skills → apps → KB → CRM fields →
    domain → few-shots → hard rules.
    """
    prompt_params = prompt_params or {}
    customer_ctx = customer_ctx or {}
    format_rules = format_rules or {}

    sections = [
        _build_role_section(prompt_params, customer_ctx, role_block),
        _build_customer_section(customer_ctx),
        _build_format_section(format_rules),
        channel_instructions.strip() + "\n" if channel_instructions.strip() else "",
        _build_skills_section(prompt_params),
        _build_apps_section(prompt_params, format_rules),
        _build_kb_section(prompt_params),
        _build_crm_fields_section(prompt_params),
        _build_domain_section(prompt_params),
    ]
    if few_shots and few_shots.strip():
        sections.append("\n# Examples\n" + few_shots.strip() + "\n")
    sections.append(_build_hard_rules_section())

    return "".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# Back-compat shim — some callers / tests still pass a raw overlay string.
# ---------------------------------------------------------------------------


def build_channel_system_text(prompt_params: dict, channel_overlay: str) -> str:
    """Deprecated: prefer ``build_channel_base_prompt`` with provider-supplied rules.

    Renders the new channel-first prompt using whatever ``prompt_params`` contain,
    then appends ``channel_overlay`` for tests / callers that haven't migrated.
    """
    base = build_channel_base_prompt(prompt_params)
    overlay = (channel_overlay or "").strip()
    if overlay:
        return base + "\n" + overlay + "\n"
    return base
