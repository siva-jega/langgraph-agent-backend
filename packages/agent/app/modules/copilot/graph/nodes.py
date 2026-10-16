import logging
from typing import Any, Dict, List
from langchain_core.messages import (
    SystemMessage,
    AIMessage,
    ToolMessage,
    BaseMessage,
    HumanMessage,
)
from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.prebuilt import ToolNode
from langgraph.types import Command
from typing_extensions import TypedDict
from typing import Annotated

from app.core.config import app_settings
from common.tools.providers.knowledgebase import KnowledgeBaseTools
from common.tools.providers.web import WebTools
from common.tools.registry import registry
from langgraph.graph.message import add_messages

logger = logging.getLogger(__name__)


class GraphState(TypedDict):
    """State for the chat graph: messages and system_prompt."""

    messages: Annotated[list[BaseMessage], add_messages]
    system_prompt: str


KEEP_RECENT = 5


def trim_messages_state(state: GraphState) -> GraphState:
    messages = state["messages"]

    if len(messages) <= KEEP_RECENT:
        return state

    start_index = len(messages) - KEEP_RECENT

    while start_index > 1 and isinstance(messages[start_index], ToolMessage):
        start_index -= 1

    system_messages = [
        msg for msg in messages[:start_index] if isinstance(msg, SystemMessage)
    ]
    recent_messages = messages[start_index:]

    return {
        **state,
        "messages": system_messages + recent_messages,
    }


def _get_tools(config: RunnableConfig) -> List:
    """Helper to get tools based on config."""
    configurable = config.get("configurable", {})
    apps: List[dict] = configurable.get("apps", [])
    entity_ids: List[str] = configurable.get("entity_ids", [])
    folder_ids: List[str] = configurable.get("folder_ids", [])
    web_search: bool = configurable.get("web_search", False)

    tools = []
    if apps:
        for app in apps:
            provider = registry.get_provider(app["slug"])
            if provider:
                tools.extend(provider.get_all())

    if web_search:
        tools.extend(WebTools.get_all())

    if entity_ids or folder_ids:
        tools.extend(KnowledgeBaseTools.get_all())
    return tools


async def llm_node(state: GraphState, config: RunnableConfig) -> Any:
    # Coerce to list; with add_messages/checkpointer state can be tuple or other
    raw = state.get("messages") or []
    messages = list(raw) if not isinstance(raw, list) else raw

    system_prompt_str = state.get("system_prompt") or ""

    if system_prompt_str:
        has_system = any(isinstance(msg, SystemMessage) for msg in messages)
        if not has_system:
            messages = [SystemMessage(content=system_prompt_str)] + messages

    trimmed_state = trim_messages_state({**state, "messages": messages})
    trimmed_messages = list(trimmed_state.get("messages") or [])
    logger.debug("llm_node trimmed messages: %s", [f"[{m.type}]: {m.content}" for m in trimmed_messages])
    if not trimmed_messages:
        logger.warning(
            "llm_node received empty messages (state had %s), using fallback",
            len(raw),
        )
        trimmed_messages = [HumanMessage(content="(No previous message)")]

    logger.debug("llm_node invoking model with %s messages", len(trimmed_messages))

    tools = _get_tools(config)
    callbacks = config.get("callbacks", [])

    model_kwargs = {"stream_options": {"include_usage": True}}

    if not tools:
        model_kwargs["tool_choice"] = "none"

    model = ChatOpenAI(
        model="gemini/gemini-2.5-flash-lite",
        api_key=app_settings.OPENAI_API_KEY,
        base_url=app_settings.OPENAI_API_BASE,
        callbacks=callbacks,
        model_kwargs=model_kwargs,
    )

    if tools:
        model_with_tools = model.bind_tools(tools)
        response = await model_with_tools.ainvoke(trimmed_messages)
    else:
        # trimmed_messages.append(SystemMessage(
        # content="IMPORTANT: You do not have access to any tools..."))
        response = await model.ainvoke(trimmed_messages)

    if hasattr(response, "tool_calls") and response.tool_calls:
        return Command(update={"messages": [response]}, goto="tools")
    else:
        return Command(update={"messages": [response]}, goto=END)


async def tools_node(state: GraphState, config: RunnableConfig) -> Command[str]:
    tools = _get_tools(config)
    tool_executor = ToolNode(tools)
    result = await tool_executor.ainvoke(state, config)
    return Command(update=result, goto="llm")


NODE_REGISTRY = {
    "llm": llm_node,
    "tools": tools_node,
}

LINEAR_EDGES = []
CONDITIONAL_EDGES = []


def get_graph_config() -> Dict[str, Any]:
    return {
        "nodes": NODE_REGISTRY,
        "linear_edges": LINEAR_EDGES,
        "conditional_edges": CONDITIONAL_EDGES,
    }
