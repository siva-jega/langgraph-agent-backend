"""LangGraph nodes for the channels runtime (separate from web/widget graph).

Interrupt-based tools (human_handoff_trigger, create_ticket_aide, create_lead_aide) are
replaced with their ``_channel`` variants which execute directly without an interrupt.
"""

import logging
import json
from typing import Any, List, Dict
from langchain_core.messages import (
    BaseMessage,
    trim_messages,
    ToolMessage,
    SystemMessage,
    AIMessage,
    RemoveMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.graph import END
from langgraph.config import get_stream_writer
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict
from typing import Annotated
from app.core.config import app_settings
from app.modules.channels.ai.skill_map import CHANNEL_SKILL_MAP
from common.llm_utils import openai_service_tier
from common.tools.providers.aide.tools import (
    build_create_lead_aide_channel_tool,
    build_create_ticket_aide_channel_tool,
)
from common.tools.providers.human_handoff.tools import (
    build_human_handoff_trigger_channel_tool,
)
from common.tools.providers.knowledgebase import KnowledgeBaseTools
from common.tools.registry import registry

# Channel-variant tool names whose args_schema must merge in workspace-configured
# `form.fields` at runtime. Each entry maps the resolved channel tool name to the
# factory that builds the per-skill tool from the skill row's form config.
_DYNAMIC_CHANNEL_TOOL_FACTORIES = {
    "create_ticket_aide_channel": build_create_ticket_aide_channel_tool,
    "create_lead_aide_channel": build_create_lead_aide_channel_tool,
    "human_handoff_trigger_channel": build_human_handoff_trigger_channel_tool,
}


def _skill_form_fields(skill_config: dict) -> list[dict]:
    """Return the workspace-configured `form.fields` list for a skill, or []."""
    if not isinstance(skill_config, dict):
        return []
    form = skill_config.get("form")
    if not isinstance(form, dict):
        return []
    fields = form.get("fields")
    return fields if isinstance(fields, list) else []
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from toon_format import encode

logger = logging.getLogger(__name__)


def _encode_tool_output_for_llm(processed_data: Any) -> str:
    """Prefer TOON when implemented; the published toon-format package still raises NotImplementedError."""
    try:
        return encode(processed_data)
    except NotImplementedError:
        return json.dumps(processed_data, ensure_ascii=False, default=str)
    except Exception as e:
        logger.warning("TOON encode failed (%s); using JSON fallback", e)
        return json.dumps(processed_data, ensure_ascii=False, default=str)

_MODEL_CACHE: Dict[str, ChatOpenAI] = {}


def _get_model(model_name: str, api_key: str, base_url: str, model_kwargs: dict, reasoning_effort: str | None = None) -> ChatOpenAI:
    """Get or create a cached ChatOpenAI instance for the given configuration."""
    service_tier = openai_service_tier()
    config_dump = json.dumps(model_kwargs, sort_keys=True)
    key = f"{model_name}:{api_key}:{base_url}:{config_dump}:{reasoning_effort}:{service_tier or ''}"

    if key not in _MODEL_CACHE:
        logger.info("Initializing new ChatOpenAI instance for model: %s", model_name)
        llm_kwargs: dict = {
            "model": model_name,
            "api_key": api_key,
            "base_url": base_url,
            "model_kwargs": model_kwargs,
        }
        if reasoning_effort is not None:
            llm_kwargs["reasoning_effort"] = reasoning_effort
        if service_tier:
            llm_kwargs["service_tier"] = service_tier
        _MODEL_CACHE[key] = ChatOpenAI(**llm_kwargs)
    return _MODEL_CACHE[key]



def _prepare_for_toon(data: Any) -> Any:
    """Standardize data for TOON encoding (e.g. dump Pydantic models)."""
    if isinstance(data, list):
        return [(_prepare_for_toon(item)) for item in data]
    elif hasattr(data, "model_dump"):
        return data.model_dump()
    elif isinstance(data, dict):
        return {k: _prepare_for_toon(v) for k, v in data.items()}
    return data


class ChannelGraphState(TypedDict):
    """State for the channel LangGraph."""

    messages: Annotated[list[BaseMessage], add_messages]
    system_prompt: str
    current_model_index: int | None
    active_skill_group: str | None


# Message trimming configuration
KEEP_RECENT = 10

_SYNTHETIC_TOOL_ERROR = (
    '{"error": "Tool execution did not complete or response was missing."}'
)


def _extract_tool_call_ids(msg: BaseMessage) -> List[str]:
    """OpenAI tool_call ids from an AI message (including additional_kwargs after checkpoint load)."""
    tcs = getattr(msg, "tool_calls", None)
    if not tcs:
        ak = getattr(msg, "additional_kwargs", None) or {}
        tcs = ak.get("tool_calls") or []
    ids: List[str] = []
    for tc in tcs or []:
        if isinstance(tc, dict):
            tid = tc.get("id")
        else:
            tid = getattr(tc, "id", None)
        if tid:
            ids.append(tid)
    return ids


def _is_tool_role_message(msg: BaseMessage) -> bool:
    if isinstance(msg, ToolMessage):
        return True
    return getattr(msg, "type", None) == "tool"


def _repair_incomplete_tool_call_chains(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Ensure AIMessage.tool_calls are followed by a ToolMessage per tool_call_id (OpenAI/Azure contract).

    LangGraph checkpoints can persist an assistant turn with tool_calls after a tool crash (e.g. NameError)
    with no ToolMessages. trim_messages can also drop tool responses while keeping the assistant turn.
    """
    if not messages:
        return messages
    out: List[BaseMessage] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        ids_needed = _extract_tool_call_ids(msg)
        is_ai = isinstance(msg, AIMessage) or getattr(msg, "type", None) == "ai"
        if is_ai and ids_needed:
            out.append(msg)
            seen: set[str] = set()
            k = i + 1
            while k < len(messages) and _is_tool_role_message(messages[k]):
                tm = messages[k]
                out.append(tm)
                tid = getattr(tm, "tool_call_id", None)
                if tid:
                    seen.add(tid)
                k += 1
            missing = [tid for tid in ids_needed if tid not in seen]
            if missing:
                logger.warning(
                    "Repairing incomplete tool_calls: injecting synthetic ToolMessages for ids=%s",
                    missing,
                )
                for tid in missing:
                    out.append(
                        ToolMessage(
                            content=_SYNTHETIC_TOOL_ERROR,
                            tool_call_id=tid,
                        )
                    )
            i = k
            continue
        out.append(msg)
        i += 1
    return out

# Skill group mapping for better tool organization and future sub-agent routing
SKILL_GROUPS = {
    "crm": ["hubspot", "zendesk"],
    "ecommerce": ["shopify", "woocommerce"],
    "productivity": ["gmail", "gdrive", "gcalendar", "notion", "airtable", "outlook", "slack", "calendly", "calcom"],
    "knowledge": ["knowledgebase", "web"],
    "memory": ["memory"],
    "handoff": ["human_handoff", "aide"],
}

# Interrupt-based skill tools → channel-safe equivalents (no interrupt, direct execution).
# Canonical map lives in ``channels.ai.skill_map`` so the prompt builder and runtime stay in sync.
_CHANNEL_SKILL_MAP = CHANNEL_SKILL_MAP


def trim_messages_node(state: ChannelGraphState) -> ChannelGraphState:
    """Trim message history using LangChain's native trim_messages utility.

    Keeps the most recent KEEP_RECENT messages, always preserving SystemMessages,
    and ensures the first retained non-system message is a HumanMessage.
    """
    if not state["messages"]:
        return state

    trimmed = trim_messages(
        state["messages"],
        strategy="last",
        token_counter=len,  # count by number of messages
        max_tokens=KEEP_RECENT,
        start_on="human",  # ensures first retained msg is HumanMessage
        include_system=True,  # always keep SystemMessages
        allow_partial=False,
    )

    repaired = _repair_incomplete_tool_call_chains(trimmed)
    # add_messages merges by message id and does NOT drop messages missing from this update,
    # so a trimmed window alone would leave older checkpoint messages (e.g. orphaned tool_calls)
    # in state. Remove all, then set exactly the trimmed+repaired list.
    return {
        **state,
        "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired],
    }


def _get_tools(config: RunnableConfig) -> List:
    """Get tools from config: apps = full provider; skills = single tool per skill via skill.toolName.

    Also logs which skill groups are active to enable sub-agent routing if needed.
    """
    configurable = config.get("configurable", {})
    folder_ids: List[str] = configurable.get("folder_ids", [])
    entity_ids = configurable.get("entity_ids", [])
    apps: List[dict] = configurable.get("apps", [])
    skills: List[dict] = configurable.get("skills", [])

    active_groups: List[str] = []

    logger.info(
        "Channel graph tools: apps=%s skills=%s (toolNames: %s)",
        [a.get("slug") for a in apps],
        len(skills),
        [s.get("toolName") for s in skills],
    )

    tools: List[Any] = []

    # Knowledge base tools
    if entity_ids or folder_ids:
        tools.extend(KnowledgeBaseTools.get_all())
        active_groups.append("knowledge")

    # App providers — skip human_handoff entirely (interrupt-based, not usable on channels)
    for app in apps:
        slug = (app.get("slug") or app.get("name") or "").strip()
        if not slug or slug.lower() == "human_handoff":
            continue
        provider = registry.get_provider(slug)
        if provider:
            tools.extend(provider.get_all())
        for group_name, group_slugs in SKILL_GROUPS.items():
            if slug in group_slugs and group_name not in active_groups:
                active_groups.append(group_name)

    # Individual skill tools — remap interrupt-based tools to their channel-safe variants
    seen_tool_names: set = set()
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        skill_config = skill.get("config") or {}
        if skill_config.get("_enabled") is False:
            continue
        tool_name = (skill.get("toolName") or skill_config.get("toolName") or "").strip()
        if not tool_name:
            continue
        channel_tool_name = _CHANNEL_SKILL_MAP.get(tool_name, tool_name)
        if channel_tool_name in seen_tool_names:
            continue

        factory = _DYNAMIC_CHANNEL_TOOL_FACTORIES.get(channel_tool_name)
        if factory is not None:
            form_fields = _skill_form_fields(skill_config)
            t = factory(form_fields)
            tools.append(t)
            seen_tool_names.add(channel_tool_name)
            logger.info(
                "Skill adds tool: %s -> %s (configured_form_field_count=%d)",
                tool_name, channel_tool_name, len(form_fields),
            )
            continue

        t = registry.get_tool_by_name(channel_tool_name)
        if t:
            tools.append(t)
            seen_tool_names.add(channel_tool_name)
            logger.info("Skill adds tool: %s -> %s", tool_name, channel_tool_name)
        else:
            logger.warning("Skill toolName '%s' not found in Registry", channel_tool_name)

    return tools


async def llm_node(state: ChannelGraphState, config: RunnableConfig) -> ChannelGraphState:
    configurable = config.get("configurable", {})
    persona = configurable.get("persona") or {}
    model_name = persona.get("model") or app_settings.DEFAULT_AGENT_MODEL

    logger.debug("Using model: %s", model_name)

    final_messages = []
    if "system_prompt" in state and state["system_prompt"]:
        final_messages.append(SystemMessage(content=state["system_prompt"]))

    messages_for_llm = _repair_incomplete_tool_call_chains(list(state["messages"]))
    for msg in messages_for_llm:
        if isinstance(msg, ToolMessage):
            try:
                # Use msg.content for the LLM — compact tool summary (no images/variants).
                # Artifact is UI-only; exclude it from LLM context to reduce tokens.
                content_to_encode = msg.content
                if isinstance(content_to_encode, str):
                    try:
                        content_to_encode = json.loads(content_to_encode)
                    except (json.JSONDecodeError, TypeError):
                        pass

                processed_data = _prepare_for_toon(content_to_encode)
                encoded_content = _encode_tool_output_for_llm(processed_data)

                # Create a new message to avoid mutating the core state; no artifact — LLM only
                new_msg = ToolMessage(
                    content=encoded_content,
                    name=msg.name,
                    tool_call_id=msg.tool_call_id,
                    status=msg.status if hasattr(msg, "status") else "success",
                )
                final_messages.append(new_msg)

                logger.debug(
                    "Prepared tool output for LLM tool=%s content_len=%s",
                    msg.name,
                    len(encoded_content),
                )
            except Exception as e:
                logger.error("Failed to prepare tool output before LLM: %s", e)
                final_messages.append(msg)
        else:
            final_messages.append(msg)

    tools = _get_tools(config)

    model = _get_model(
        model_name=model_name,
        api_key=app_settings.OPENAI_API_KEY,
        base_url=app_settings.OPENAI_API_BASE,
        model_kwargs={"stream_options": {"include_usage": True}},
        reasoning_effort=None,
    )

    if tools:
        model = model.bind_tools(tools)

    response: Any = None
    async for _chunk in model.astream(final_messages):
        response = _chunk if response is None else response + _chunk
    if response is None:
        response = await model.ainvoke(final_messages)

    writer = get_stream_writer()
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tool_call in response.tool_calls:
            tool_name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", "")
            tool_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
            if tool_name:
                writer({"event": "tool_call", "tool_name": tool_name, "tool_args": tool_args})

    return {"messages": [response]}


async def tools_node(state: ChannelGraphState, config: RunnableConfig) -> ChannelGraphState:
    """Execute tools from the LLM's tool calls. Tools are resolved from config."""
    return await ToolNode(_get_tools(config)).ainvoke(state, config)
