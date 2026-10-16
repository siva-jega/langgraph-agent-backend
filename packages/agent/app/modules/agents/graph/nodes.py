import logging
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from pydantic import SecretStr
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
from common.llm_utils import openai_service_tier
from common.tools.registry import registry
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
logger = logging.getLogger(__name__)


def _encode_tool_output_for_llm(processed_data: Any) -> str:
    return json.dumps(processed_data, ensure_ascii=False, default=str)


_KNOWLEDGEBASE_LLM_FIELDS = frozenset({"text", "metadata"})


def _strip_knowledgebase_results_for_llm(artifact: Any) -> Any:
    if not isinstance(artifact, list):
        return artifact
    result = []
    for item in artifact:
        if isinstance(item, dict) and item.get("name") == "Knowledge Base":
            stripped_data = [
                {k: v for k, v in chunk.items() if k in _KNOWLEDGEBASE_LLM_FIELDS}
                for chunk in (item.get("data") or [])
                if isinstance(chunk, dict)
            ]
            result.append({**item, "data": stripped_data})
        else:
            result.append(item)
    return result


_MODEL_CACHE: Dict[str, ChatOpenAI] = {}


def _get_model(
    model_name: str,
    api_key: str | None,
    base_url: str | None,
    model_kwargs: dict,
    reasoning_effort: str | None = None,
) -> ChatOpenAI:
    """Get or create a cached ChatOpenAI instance for the given configuration."""
    service_tier = openai_service_tier()
    config_dump = json.dumps(model_kwargs, sort_keys=True)
    key = f"{model_name}:{api_key or ''}:{base_url or ''}:{config_dump}:{reasoning_effort}:{service_tier or ''}"

    if key not in _MODEL_CACHE:
        logger.info("Initializing new ChatOpenAI instance for model: %s", model_name)
        llm_kwargs: dict[str, Any] = {
            "model": model_name,
            "model_kwargs": model_kwargs,
        }
        if api_key:
            llm_kwargs["api_key"] = SecretStr(api_key)
        if base_url:
            llm_kwargs["base_url"] = base_url
        if reasoning_effort is not None:
            llm_kwargs["reasoning_effort"] = reasoning_effort
        if service_tier:
            llm_kwargs["service_tier"] = service_tier
        _MODEL_CACHE[key] = ChatOpenAI(**llm_kwargs)
    return _MODEL_CACHE[key]




class GraphState(TypedDict):
    """State for the chat graph: messages, system_prompt, and scaffolding for future multi-agent routing."""

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

# Human-readable status labels emitted as status_update SSE events so the UI
# can show live phase indicators (e.g. "Fetching products...") while tools run.
TOOL_STATUS_LABELS: dict[str, str] = {
    # Knowledge / web
    "search_knowledgebase": "Looking that up...",
    "web_search": "Searching the web...",
    # Memory
    "add_memory": "Noting that for next time...",
    "get_memories": "Remembering our past chats...",
    # Ecommerce — Shopify / WooCommerce
    "get_products": "Finding the right products...",
    "get_order_status": "Checking your order...",
    # CRM — HubSpot
    "list_contacts": "Loading your contacts...",
    "search_contacts": "Searching your contacts...",
    "search_contacts_by_company": "Finding contacts at that company...",
    "get_contact": "Opening that contact...",
    "list_companies": "Loading your companies...",
    "search_companies": "Searching for that company...",
    "list_deals": "Loading your deals...",
    "search_deals": "Searching your deals...",
    "list_tickets": "Loading your tickets...",
    "search_tickets": "Searching your tickets...",
    "create_ticket": "Creating your support ticket...",
    "get_analytics_summary": "Pulling together your analytics...",
    # CRM — Zendesk
    "create_zendesk_ticket": "Creating your support ticket...",
    # Handoff / escalation
    "human_handoff_trigger": "Connecting you with our team...",
    "create_ticket_aide": "Creating your support ticket...",
    "create_lead_aide": "Saving your details...",
    # Productivity — Gmail / Outlook
    "send_email": "Sending your email...",
    "search_emails": "Searching your inbox...",
    "list_emails": "Loading your emails...",
    # Calendar
    "create_event": "Adding that to your calendar...",
    "list_events": "Checking your calendar...",
    "get_events": "Checking your calendar...",
    # Notion
    "search_notion": "Searching your Notion...",
    "create_notion_page": "Creating that page...",
    # Slack
    "send_slack_message": "Sending your message...",
    "search_slack_messages": "Searching Slack...",
    # Airtable
    "list_airtable_records": "Loading your records...",
    "create_airtable_record": "Adding that record...",
    # Calendly / Cal.com
    "list_event_types": "Checking available times...",
    "schedule_event": "Booking your appointment...",
    # Shipping carriers
    "track_shipment": "Tracking your shipment...",
    "get_tracking_info": "Tracking your shipment...",
}

# Skill group mapping for better tool organization and future sub-agent routing
SKILL_GROUPS = {
    "crm": ["hubspot", "zendesk"],
    "ecommerce": ["shopify", "woocommerce"],
    "productivity": ["gmail", "gdrive", "gcalendar", "notion", "airtable", "outlook", "slack", "calendly", "calcom"],
    "knowledge": ["knowledgebase", "web"],
    "memory": ["memory"],
    "handoff": ["human_handoff", "aide"],
}


def trim_messages_node(state: GraphState) -> GraphState:
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
    human_handoff_enabled = configurable.get("human_handoff_enabled", False)
    allow_human_handoff = configurable.get("allow_human_handoff", True)

    active_groups: List[str] = []

    logger.info(
        "Tools config: apps=%s skills=%s (toolNames: %s) human_handoff=%s",
        [a.get("slug") for a in apps],
        len(skills),
        [s.get("toolName") for s in skills],
        human_handoff_enabled,
    )

    tools: List[Any] = []

    # Knowledge base tools (knowledge group)
    if entity_ids or folder_ids:
        tools.extend(registry.get_tools_by_provider("knowledgebase"))
        active_groups.append("knowledge")

    # NOTE: human_handoff_trigger is added by the generic skill loop below
    # when the agent has an AgentSkill with toolName="human_handoff_trigger".
    # No special-case block needed here.

    # App providers (map to skill groups)
    app_slugs: List[str] = []
    for app in apps:
        slug = (app.get("slug") or app.get("name") or "").strip()
        if slug:
            app_slugs.append(slug)
            provider = registry.get_provider(slug)
            if provider:
                tools.extend(provider.get_all())

            # Categorize app into skill group
            for group_name, group_slugs in SKILL_GROUPS.items():
                if slug in group_slugs and group_name not in active_groups:
                    active_groups.append(group_name)

    # Individual skill tools
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
        if tool_name == "human_handoff_trigger" and not allow_human_handoff:
            logger.info(
                "Skipping human_handoff_trigger for channel=%s",
                configurable.get("channel"),
            )
            continue
        if tool_name in seen_tool_names:
            continue
        t = registry.get_tool_by_name(tool_name)
        if t:
            tools.append(t)
            seen_tool_names.add(tool_name)
            logger.info("Skill adds single tool: %s", tool_name)
        else:
            logger.warning("Skill toolName '%s' not found in Registry", tool_name)

    return tools


async def llm_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    configurable = config.get("configurable", {})
    persona = configurable.get("persona") or {}
    model_name = configurable.get("model") or persona.get("model") or app_settings.DEFAULT_AGENT_MODEL

    logger.debug("Using model: %s", model_name)

    final_messages = []
    if "system_prompt" in state and state["system_prompt"]:
        final_messages.append(SystemMessage(content=state["system_prompt"]))
        final_messages.append(SystemMessage(content=f"Current date/time: {datetime.now(timezone.utc).isoformat()}"))
        pfc = configurable.get("product_faq_context")
        if isinstance(pfc, dict) and pfc.get("question") and pfc.get("answer"):
            q = pfc["question"].strip()
            a = pfc["answer"].strip()
            pt = str(pfc.get("product_title") or "").strip()
            title_line = f"Product: {pt}\n" if pt else ""
            final_messages.append(SystemMessage(content=(
                f"# Approved product FAQ (ground truth) — **highest priority for this message**\n"
                f"The shopper tapped this suggested question on a product page. Treat the following "
                f"as **canonical** store-approved facts. You may paraphrase warmly, but **do not** "
                f"contradict this content or invent different specs, materials, or policies.\n\n"
                f"{title_line}**Question:** {q}\n**Approved answer:** {a}\n\n"
                f"**You must** answer in line with the approved answer above (this turn is in scope). "
                f"Do **not** reply with only a generic greeting like \"How can I help?\" without "
                f"addressing the question. Respond in a natural, helpful tone—do not say you are quoting an FAQ."
            )))

        scc = configurable.get("shopify_catalog_context")
        if scc:
            final_messages.append(SystemMessage(content=(
                "# Store Catalog Context\n"
                "High-level overview of this store's collections and price ranges. "
                "Use this to answer questions about what's available and at what price points "
                "without calling get_products.\n\n"
                f"{scc}"
            )))

    messages_for_llm = _repair_incomplete_tool_call_chains(list(state["messages"]))
    for msg in messages_for_llm:
        if isinstance(msg, ToolMessage):
            try:
                # Use msg.content for the LLM — it's the compact tool summary (no images/variants).
                # Artifact is UI-only (widget cards); exclude it from LLM context to reduce tokens.
                content_to_encode = msg.content
                if isinstance(content_to_encode, str):
                    try:
                        content_to_encode = json.loads(content_to_encode)
                    except (json.JSONDecodeError, TypeError):
                        pass

                if msg.name == "search_knowledgebase":
                    content_to_encode = _strip_knowledgebase_results_for_llm(content_to_encode)

                encoded_content = _encode_tool_output_for_llm(content_to_encode)

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
    
    # TODO(#392): After terminal tools (e.g. get_products), skip bind_tools when has_tool_results
    # to save ~1.4k input tokens on the final reply turn. Reverted in cf63c6d: `if tools and not
    # has_tool_results` blocks sequential ReAct (turn 2 cannot call another tool to fix a wrong
    # turn 1). Revisit with terminal-tool-only unbind or compact payload without unbind.
    if tools:
        model = model.bind_tools(tools)

    # Stream tokens so LangGraph's stream_mode="messages" forwards them as SSE
    # chat_message chunks. Non-streamed ainvoke blocked the post-tool reply for
    # ~5s before any token reached the client (see post_ainvoke logs pre-fix).
    response: Any = None
    async for _chunk in model.astream(final_messages):
        response = _chunk if response is None else response + _chunk

    # Safety: if the provider yielded no chunks, fall back to a regular ainvoke
    # so we never return a None message to graph state.
    if response is None:
        response = await model.ainvoke(final_messages)

    # Emit custom events for tool calls so graph streaming doesn't need to parse them
    writer = get_stream_writer()
    if hasattr(response, "tool_calls") and response.tool_calls:
        emitted_statuses: set[str] = set()
        for tool_call in response.tool_calls:
            tool_name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", "")
            tool_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
            if tool_name:
                writer({"event": "tool_call", "tool_name": tool_name, "tool_args": tool_args})
                # Emit a deduplicated status label for UI phase indicators
                label = TOOL_STATUS_LABELS.get(tool_name)
                if label and label not in emitted_statuses:
                    writer({"event": "status_update", "status": label})
                    emitted_statuses.add(label)

    return {"messages": [response]}


async def tools_node(state: GraphState, config: RunnableConfig) -> GraphState:
    """Execute tools from the LLM's tool calls. Tools are resolved from config."""
    return await ToolNode(_get_tools(config)).ainvoke(state, config)
