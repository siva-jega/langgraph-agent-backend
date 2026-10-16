from typing import Annotated, Any, List, Optional

from langchain_core.tools import BaseTool, tool
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import Field
import logging
from common.tools.interrupt_payload import merge_interrupt_value
from common.tools.dynamic_args_schema import build_args_schema, split_kwargs_by_base
from .utils import (
    create_widget_ticket,
    create_widget_customer,
)
from .notify import send_ticket_notifications
from .field_mapping import (
    map_submitted_to_customer,
    map_submitted_to_ticket,
    normalize_customer_status,
    normalize_ticket_priority,
    normalize_ticket_status,
    validate_form_field_values,
    validate_required_form_fields,
)
from common.tools.crm_ai_form_fields import (
    crm_ai_keys_for_entity,
    strip_ai_populated_form_fields,
)

logger = logging.getLogger(__name__)

AideToolReturn = tuple[str, dict[str, Any]]

_TOOL_ENTITY_FOR_CRM_AI: dict[str, str] = {
    "create_ticket_aide": "TICKET",
    "create_lead_aide": "CUSTOMER",
}

_TICKET_REASON_DESCRIPTION = (
    "Why this ticket is being created — a specific, conversation-derived phrase "
    "describing the customer's actual problem or request. Examples: "
    "'damaged item on arrival', 'wrong size received for order #1242', "
    "'refund requested — late delivery', 'subscription cancellation request'. "
    "REQUIRED. Must be concrete and grounded in what the user said. Do NOT pass "
    "generic placeholders like 'support', 'inquiry', 'help needed', 'issue', "
    "or empty strings — those are rejected."
)


def _validate_reason(value: Any) -> str:
    """Reject empty / placeholder reasons. The tool description tells the model
    to derive a concrete phrase from the conversation; this enforces it at
    runtime so a sloppy call doesn't slip a generic string into the ticket."""
    text = str(value or "").strip()
    if not text:
        raise ValueError(
            "reason is required: pass a concrete phrase derived from the "
            "conversation, e.g. 'damaged item on arrival'."
        )
    placeholder = text.lower().strip(".!? ")
    if placeholder in {
        "support",
        "inquiry",
        "help",
        "help needed",
        "issue",
        "problem",
        "ticket",
        "n/a",
        "na",
        "none",
        "other",
        "general",
    }:
        raise ValueError(
            f"reason {value!r} is too generic — pass a concrete phrase describing "
            "the customer's actual problem (e.g. 'damaged item on arrival', "
            "'wrong size for order #1242', 'refund requested — late delivery')."
        )
    return text


def _configurable(config: RunnableConfig) -> dict[str, Any]:
    c = config.get("configurable", {}) or {}
    return c if isinstance(c, dict) else {}


def _normalize_channel(value: Any) -> str:
    return str(value or "web").strip().lower() or "web"


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _get_skill_form_fields(config: RunnableConfig, tool_name: str) -> list:
    """Return normalized form.fields for this tool from configurable.skill_configs.

    Omits CRM keys marked aiPopulated for this entity (model fills them via tool args),
    even if stale AgentSkills.config still lists them.
    """
    c = config.get("configurable", {}) or {}
    skill_configs = c.get("skill_configs") or {}
    skill_config = skill_configs.get(tool_name) or {}
    form = skill_config.get("form") or {}
    fields = form.get("fields")
    if isinstance(fields, list) and len(fields) > 0:
        normalized = []
        for f in fields:
            if not isinstance(f, dict):
                continue
            field = dict(f)
            field.setdefault("name", field.get("id", ""))
            field.setdefault("id", field.get("name", ""))
            normalized.append(field)
        entity = _TOOL_ENTITY_FOR_CRM_AI.get(tool_name)
        ai_keys = crm_ai_keys_for_entity(config, entity) if entity else set()
        return strip_ai_populated_form_fields(normalized, ai_keys)
    return []


@tool(response_format="content_and_artifact")
async def create_ticket_aide(
    config: RunnableConfig,
    summary: str,
    description: str,
    reason: Annotated[str, Field(description=_TICKET_REASON_DESCRIPTION)],
    priority: Optional[str] = None,
    status: Optional[str] = None,
    widget_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> AideToolReturn:
    """Create a support ticket. Subject, description, priority, status, and reason come from
    the model (aligned with workspace CRM keys). Visitor/contact fields come only from skill
    `form.fields` (mapsTo), not from hard-coded prompts.

    `reason` is REQUIRED and must be a concrete, conversation-derived phrase
    explaining why the ticket exists (e.g. 'damaged item on arrival',
    'refund requested for order #1242'). Generic placeholders are rejected.
    """
    reason = _validate_reason(reason)
    form_fields = _get_skill_form_fields(config, "create_ticket_aide")
    pri = normalize_ticket_priority(priority)
    stat = normalize_ticket_status(status, "OPEN")

    tool_args: dict = {}
    if form_fields:
        tool_args["form_schema"] = {"fields": form_fields}

    interrupt_value = merge_interrupt_value(
        config,
        tool_name="create_ticket_aide",
        tool_display_name="Create Ticket",
        tool_args=tool_args,
    )

    response = interrupt(value=interrupt_value)
    try:
        decision_type = response.get("decisions", [])[0].get("type")
        user_approved = decision_type == "approve"
    except (IndexError, AttributeError, TypeError):
        user_approved = False

    if not user_approved:
        return "User declined ticket creation", {
            "name": "Aide",
            "tool": "Create Ticket",
            "data": {"success": False, "message": "User declined ticket creation"},
        }

    try:
        configurable = config.get("configurable", {}) or {}
        user_id = configurable.get("user_id")

        if not widget_id:
            widget_id = configurable.get("agent_id")
        if not chat_id:
            chat_id = configurable.get("thread_id") or configurable.get("chat_id")

        if not widget_id or not chat_id:
            error_msg = "widget_id and chat_id are required."
            return error_msg, {
                "name": "Aide",
                "tool": "Create Ticket",
                "data": {"success": False, "message": error_msg},
            }

        submitted: dict = response.get("fields") or {}
        if form_fields:
            ok, err = validate_required_form_fields(form_fields, submitted)
            if not ok:
                return err, {
                    "name": "Aide",
                    "tool": "Create Ticket",
                    "data": {"success": False, "message": err},
                }
            ok, err = validate_form_field_values(form_fields, submitted)
            if not ok:
                return err, {
                    "name": "Aide",
                    "tool": "Create Ticket",
                    "data": {"success": False, "message": err},
                }

        mapped = map_submitted_to_ticket(form_fields, submitted)
        ticket_data = {
            **mapped.data,
            "subject": summary,
            "description": description,
            "reason": reason,
            "priority": pri,
            "status": stat,
        }

        logger.info(
            "create_ticket_aide: approved | agent_id=%s chat_id=%s data_keys=%s",
            widget_id,
            chat_id,
            sorted(ticket_data.keys()),
        )

        result = await create_widget_ticket(
            widget_id=widget_id,
            chat_id=chat_id,
            platform="aide",
            data=ticket_data,
            metadata=metadata,
            user_id=user_id,
        )
        if result.success and result.ticket_id:
            try:
                await send_ticket_notifications(
                    config=config,
                    tool_name="create_ticket_aide",
                    ticket_id=result.ticket_id,
                    ticket_data=ticket_data,
                )
            except Exception as notify_err:  # noqa: BLE001
                logger.warning(
                    "create_ticket_aide: notification dispatch failed: %s",
                    notify_err,
                )
        return result.message, {
            "name": "Aide",
            "tool": "Create Ticket",
            "data": result.model_dump(),
        }
    except Exception as e:
        logger.error("Error creating ticket: %s", e, exc_info=True)
        error_msg = f"Error creating ticket: {str(e)}"
        return error_msg, {
            "name": "Aide",
            "tool": "Create Ticket",
            "data": {"success": False, "message": error_msg},
        }


@tool(response_format="content_and_artifact")
async def create_lead_aide(
    config: RunnableConfig,
    intent: str,
    status: Optional[str] = None,
    widget_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> AideToolReturn:
    """Create a customer. Intent and status come from the model (CRM keys `intent`, `status`);
    contact/company fields only from skill `form.fields` (mapsTo).
    """
    form_fields = _get_skill_form_fields(config, "create_lead_aide")
    cust_status = normalize_customer_status(status, "NEW")

    merged_args: dict[str, Any] = {}
    if form_fields:
        merged_args["form_schema"] = {"fields": form_fields}

    interrupt_value = merge_interrupt_value(
        config,
        tool_name="create_lead_aide",
        tool_display_name="Create Customer",
        tool_args=merged_args,
    )

    response = interrupt(value=interrupt_value)
    try:
        decision_type = response.get("decisions", [])[0].get("type")
        user_approved = decision_type == "approve"
    except (IndexError, AttributeError, TypeError):
        user_approved = False

    if not user_approved:
        return "User declined customer creation", {
            "name": "Aide",
            "tool": "Create Customer",
            "data": {"success": False, "message": "User declined customer creation"},
        }

    try:
        configurable = config.get("configurable", {}) or {}
        user_id = configurable.get("user_id")

        if not widget_id:
            widget_id = configurable.get("agent_id")
        if not chat_id:
            chat_id = configurable.get("thread_id") or configurable.get("chat_id")

        if not widget_id or not chat_id:
            error_msg = "widget_id and chat_id are required."
            return error_msg, {
                "name": "Aide",
                "tool": "Create Customer",
                "data": {"success": False, "message": error_msg},
            }

        submitted: dict = response.get("fields") or {}
        if form_fields:
            ok, err = validate_required_form_fields(form_fields, submitted)
            if not ok:
                return err, {
                    "name": "Aide",
                    "tool": "Create Customer",
                    "data": {"success": False, "message": err},
                }
            ok, err = validate_form_field_values(form_fields, submitted)
            if not ok:
                return err, {
                    "name": "Aide",
                    "tool": "Create Customer",
                    "data": {"success": False, "message": err},
                }

        mapped = map_submitted_to_customer(form_fields, submitted)
        customer_data = {
            **mapped.data,
            "intent": intent,
            "status": cust_status,
        }

        logger.info(
            "create_lead_aide: approved | agent_id=%s chat_id=%s data_keys=%s",
            widget_id,
            chat_id,
            sorted(customer_data.keys()),
        )

        result = await create_widget_customer(
            widget_id=widget_id,
            chat_id=chat_id,
            platform="aide",
            data=customer_data,
            metadata=metadata,
            user_id=None,
        )
        return result.message, {
            "name": "Aide",
            "tool": "Create Customer",
            "data": result.model_dump(),
        }
    except Exception as e:
        logger.error("Error creating customer: %s", e, exc_info=True)
        error_msg = f"Error creating customer: {str(e)}"
        return error_msg, {
            "name": "Aide",
            "tool": "Create Customer",
            "data": {"success": False, "message": error_msg},
        }


# ── Channel-runtime tools ──────────────────────────────────────────────────────
#
# Channels can't show an interrupt form, so the LLM must collect every field
# value from the user in chat. Workspace-configured `form.fields` are exposed
# as TYPED tool args via the factories below — the LangChain tool-calling
# layer then refuses any invocation missing a required field, so the model
# has no choice but to ask the user first.
#
# Only `summary` stays as a constant base arg (the LLM derives it from the
# conversation; it never makes sense to ask the user "what should the subject
# be?"). Every other field — requester name/email/phone, custom workspace
# fields, etc. — comes from the workspace skill row's `form.fields`. The
# workspace UI is the single source of truth for the shape of what gets asked.
#
# Bound on the channel runtime by ``channels/ai/nodes._get_tools`` using
# ``build_*_channel_tool(form_fields)``.


_CREATE_TICKET_CHANNEL_BASE_FIELDS: dict[str, tuple[Any, Any]] = {
    "summary": (str, Field(
        ..., description=(
            "Short subject/title of the ticket — derived by the assistant from "
            "the conversation. Not asked of the user directly."
        ),
    )),
    "reason": (str, Field(..., description=_TICKET_REASON_DESCRIPTION)),
}


async def _create_ticket_aide_channel_impl(
    *,
    config: RunnableConfig,
    base: dict[str, Any],
    submitted: dict[str, str],
    form_fields: list[dict],
) -> AideToolReturn:
    """Channel ticket creation. Subject comes from `base["summary"]`; everything
    else (requester contact info, custom fields, etc.) flows in via the
    workspace-configured form fields in `submitted`, routed through
    ``map_submitted_to_ticket`` into the ticket payload.
    """
    configurable = _configurable(config)
    user_id = configurable.get("user_id")
    channel = _normalize_channel(configurable.get("channel"))
    widget_id = configurable.get("agent_id")
    chat_id = configurable.get("thread_id") or configurable.get("chat_id")

    if not widget_id or not chat_id:
        error_msg = "Missing session context (agent_id / chat_id)."
        return error_msg, {
            "name": "Aide", "tool": "Create Ticket",
            "data": {"success": False, "message": error_msg},
        }

    summary = str(base.get("summary") or "").strip()
    reason = _validate_reason(base.get("reason"))

    if form_fields:
        ok, err = validate_required_form_fields(form_fields, submitted)
        if not ok:
            return err, {
                "name": "Aide", "tool": "Create Ticket",
                "data": {"success": False, "message": err},
            }
        ok, err = validate_form_field_values(form_fields, submitted)
        if not ok:
            return err, {
                "name": "Aide", "tool": "Create Ticket",
                "data": {"success": False, "message": err},
            }

    mapped = map_submitted_to_ticket(form_fields, submitted) if form_fields else None

    ticket_data: dict[str, Any] = {
        **(mapped.data if mapped else {}),
        "subject": summary,
        "reason": reason,
        "channel": channel,
        "chatId": chat_id,
        "sessionId": configurable.get("session_id"),
    }
    # Apply sensible defaults only when the workspace mapping didn't supply them.
    ticket_data.setdefault("description", summary)
    ticket_data.setdefault("priority", normalize_ticket_priority(None))
    ticket_data.setdefault("status", normalize_ticket_status(None, "OPEN"))

    # Session-context phone fallback — the conversation knows the sender's
    # phone (WhatsApp) even when the workspace didn't ask the LLM for one.
    if "requesterPhone" not in ticket_data and configurable.get("sender_phone"):
        ticket_data["requesterPhone"] = configurable.get("sender_phone")

    logger.info(
        "create_ticket_aide_channel: channel=%s agent_id=%s chat_id=%s "
        "data_keys=%s configured_form_field_count=%d",
        channel, widget_id, chat_id, sorted(ticket_data.keys()), len(form_fields),
    )

    try:
        result = await create_widget_ticket(
            widget_id=widget_id,
            chat_id=chat_id,
            platform="aide",
            data=ticket_data,
            user_id=user_id,
        )
        if result.success and result.ticket_id:
            try:
                await send_ticket_notifications(
                    config=config,
                    tool_name="create_ticket_aide",
                    ticket_id=result.ticket_id,
                    ticket_data=ticket_data,
                    channel=channel,
                )
            except Exception as notify_err:  # noqa: BLE001
                logger.warning(
                    "create_ticket_aide_channel: notification dispatch failed: %s",
                    notify_err,
                )
        return result.message, {
            "name": "Aide", "tool": "Create Ticket",
            "data": result.model_dump(),
        }
    except Exception as e:
        logger.error("Error creating channel ticket: %s", e, exc_info=True)
        error_msg = f"Error creating ticket: {str(e)}"
        return error_msg, {
            "name": "Aide", "tool": "Create Ticket",
            "data": {"success": False, "message": error_msg},
        }


def build_create_ticket_aide_channel_tool(form_fields: list[dict] | None) -> BaseTool:
    """Build a per-skill channel ticket tool. The args_schema is `summary` plus
    whatever the workspace configured in `form.fields`. Bound by
    ``channels/ai/nodes._get_tools`` per turn so config changes propagate
    immediately to the next conversation turn.
    """
    base_fields = _CREATE_TICKET_CHANNEL_BASE_FIELDS
    base_keys = set(base_fields.keys())
    schema = build_args_schema(
        name="create_ticket_aide_channel",
        base_fields=base_fields,
        configured_form_fields=form_fields,
    )

    @tool(
        "create_ticket_aide_channel",
        args_schema=schema,
        response_format="content_and_artifact",
    )
    async def create_ticket_aide_channel(
        config: RunnableConfig, **kwargs: Any
    ) -> AideToolReturn:
        """Create a support ticket on a messaging channel (WhatsApp, Instagram,
        Messenger, Gmail). No interrupt form is shown — you MUST collect every
        required argument shown in this tool's schema from the user in chat
        before calling. The workspace configures which fields are required;
        ask for any value you don't already have from the conversation."""
        base, submitted = split_kwargs_by_base(kwargs, base_keys)
        return await _create_ticket_aide_channel_impl(
            config=config,
            base=base,
            submitted=submitted,
            form_fields=form_fields or [],
        )

    return create_ticket_aide_channel


_CREATE_LEAD_CHANNEL_BASE_FIELDS: dict[str, tuple[Any, Any]] = {
    "summary": (str, Field(
        ..., description=(
            "Short summary of why the customer is reaching out (intent) — "
            "derived by the assistant from the conversation. Not asked of "
            "the user directly."
        ),
    )),
}


async def _create_lead_aide_channel_impl(
    *,
    config: RunnableConfig,
    base: dict[str, Any],
    submitted: dict[str, str],
    form_fields: list[dict],
) -> AideToolReturn:
    """Channel lead/customer creation. `summary` becomes the lead's `intent`;
    name/email/phone/custom fields come from workspace `form.fields`."""
    configurable = _configurable(config)
    user_id = configurable.get("user_id")
    channel = _normalize_channel(configurable.get("channel"))
    widget_id = configurable.get("agent_id")
    chat_id = configurable.get("thread_id") or configurable.get("chat_id")

    if not widget_id or not chat_id:
        error_msg = "Missing session context (agent_id / chat_id)."
        return error_msg, {
            "name": "Aide", "tool": "Create Customer",
            "data": {"success": False, "message": error_msg},
        }

    summary = str(base.get("summary") or "").strip()

    if form_fields:
        ok, err = validate_required_form_fields(form_fields, submitted)
        if not ok:
            return err, {
                "name": "Aide", "tool": "Create Customer",
                "data": {"success": False, "message": err},
            }
        ok, err = validate_form_field_values(form_fields, submitted)
        if not ok:
            return err, {
                "name": "Aide", "tool": "Create Customer",
                "data": {"success": False, "message": err},
            }

    mapped = map_submitted_to_customer(form_fields, submitted) if form_fields else None

    customer_data: dict[str, Any] = {
        **(mapped.data if mapped else {}),
        "intent": summary,
        "channel": channel,
        "externalUserId": _first_non_empty(
            configurable.get("sender_id"),
            configurable.get("user_id"),
        ),
    }
    customer_data.setdefault("status", normalize_customer_status(None, "NEW"))

    # Session-context phone fallback.
    if "phone" not in customer_data and configurable.get("sender_phone"):
        customer_data["phone"] = configurable.get("sender_phone")

    logger.info(
        "create_lead_aide_channel: channel=%s agent_id=%s chat_id=%s "
        "data_keys=%s configured_form_field_count=%d",
        channel, widget_id, chat_id, sorted(customer_data.keys()), len(form_fields),
    )

    try:
        result = await create_widget_customer(
            widget_id=widget_id,
            chat_id=chat_id,
            platform="aide",
            data=customer_data,
            user_id=user_id,
        )
        return result.message, {
            "name": "Aide", "tool": "Create Customer",
            "data": result.model_dump(),
        }
    except Exception as e:
        logger.error("Error creating channel customer: %s", e, exc_info=True)
        error_msg = f"Error creating customer: {str(e)}"
        return error_msg, {
            "name": "Aide", "tool": "Create Customer",
            "data": {"success": False, "message": error_msg},
        }


def build_create_lead_aide_channel_tool(form_fields: list[dict] | None) -> BaseTool:
    """Build a per-skill channel lead tool. `summary` plus workspace `form.fields`."""
    base_fields = _CREATE_LEAD_CHANNEL_BASE_FIELDS
    base_keys = set(base_fields.keys())
    schema = build_args_schema(
        name="create_lead_aide_channel",
        base_fields=base_fields,
        configured_form_fields=form_fields,
    )

    @tool(
        "create_lead_aide_channel",
        args_schema=schema,
        response_format="content_and_artifact",
    )
    async def create_lead_aide_channel(
        config: RunnableConfig, **kwargs: Any
    ) -> AideToolReturn:
        """Register a sales lead / customer on a messaging channel. No interrupt
        form is shown — you MUST collect every required argument shown in this
        tool's schema from the user in chat. The workspace configures which
        fields are required; ask for any value you don't already have."""
        base, submitted = split_kwargs_by_base(kwargs, base_keys)
        return await _create_lead_aide_channel_impl(
            config=config,
            base=base,
            submitted=submitted,
            form_fields=form_fields or [],
        )

    return create_lead_aide_channel


def get_aide_tools() -> List[BaseTool]:
    """Web/widget Aide tools (interrupt-based). Channel variants are built per
    turn by ``build_create_ticket_aide_channel_tool`` /
    ``build_create_lead_aide_channel_tool`` directly from skill config — they
    are intentionally NOT registered here."""
    return [create_ticket_aide, create_lead_aide]
