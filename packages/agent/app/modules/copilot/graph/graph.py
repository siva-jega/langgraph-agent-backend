from dataclasses import replace
from typing import Annotated, AsyncGenerator, Any, List, TypedDict
from app.modules.copilot.graph.nodes import get_graph_config
from app.modules.copilot.graph.prompt import get_system_prompt
from app.core.types import AgentStreamResponse, ConversationMessage
from langchain_core.messages import ToolMessage, AIMessageChunk, HumanMessage, AIMessage
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langgraph.graph import StateGraph, START
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
import time
import logging
from app.otel_utils import no_trace
from common.llm_utils import cache_read_tokens

logger = logging.getLogger(__name__)


def to_dict(obj):
    try:
        from pydantic import BaseModel

        if isinstance(obj, BaseModel):
            return obj.model_dump()
    except ImportError:
        pass
    if isinstance(obj, list):
        return [to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    obj_dict = getattr(obj, "__dict__", None)
    if isinstance(obj_dict, dict):
        return {k: to_dict(v) for k, v in obj_dict.items()}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


class GraphState(TypedDict):
    """State for the chat graph: messages and system_prompt."""

    messages: Annotated[list[BaseMessage], add_messages]
    system_prompt: str
    current_model_index: int | None


def _extract_token_usage(callbacks: List[Any]) -> dict | None:
    """Build token usage dict from LangChain usage callbacks."""
    if (
        not callbacks
        or not hasattr(callbacks[0], "usage_metadata")
        or not callbacks[0].usage_metadata
    ):
        return None

    usage_metadata = callbacks[0].usage_metadata
    model_name = next(iter(usage_metadata))
    model_usage = usage_metadata[model_name]
    input_details = model_usage.get("input_token_details") or {}
    return {
        "model": model_name,
        "total_tokens": model_usage.get("total_tokens", 0),
        "input_tokens": model_usage.get("input_tokens", 0),
        "output_tokens": model_usage.get("output_tokens", 0),
        "cache_read_input_tokens": cache_read_tokens(input_details),
    }


class Graph:
    def __init__(self):
        self.streaming = True
        self.graph = self._build_graph()

    def _build_graph(self):
        config_dict = get_graph_config()
        graph = StateGraph(GraphState)

        for name, func in config_dict["nodes"].items():
            graph.add_node(name, func)

        graph.add_edge(START, "llm")

        for source, target in config_dict["linear_edges"]:
            graph.add_edge(source, target)

        for source, condition_func, routes in config_dict["conditional_edges"]:
            graph.add_conditional_edges(source, condition_func, routes)

        return graph.compile()

    @no_trace
    def _process_chunk(
        self,
        chunk,
        sources: List[Any],
        seen_tool_ids: set[str],
        callbacks: List[any],
        last_ai_message: Any = None,
    ) -> tuple[AgentStreamResponse | None, Any]:
        """Process a streaming chunk and yield appropriate responses.

        Returns:
            Tuple of (response, last_ai_message) where last_ai_message is tracked for final response
        """
        if isinstance(chunk, tuple) and len(chunk) == 2:
            stream_type, stream_data = chunk

            if stream_type == "messages":
                if isinstance(stream_data, tuple) and len(stream_data) >= 1:
                    message = stream_data[0]

                    if isinstance(message, ToolMessage):
                        artifact = getattr(message, "artifact", None)
                        tool_call_id = getattr(message, "tool_call_id", None)
                        if tool_call_id not in seen_tool_ids:
                            seen_tool_ids.add(tool_call_id)
                            if artifact:
                                artifact_data = to_dict(artifact)
                                if isinstance(artifact_data, list):
                                    sources.extend(artifact_data)
                                else:
                                    sources.append(artifact_data)
                            else:
                                sources.append({"content": message.content})

                    if isinstance(message, AIMessage):
                        last_ai_message = message

                        if hasattr(message, "tool_calls") and message.tool_calls:
                            for tool_call in message.tool_calls:
                                tool_name = (
                                    tool_call.get("name")
                                    if isinstance(tool_call, dict)
                                    else getattr(tool_call, "name", "")
                                )
                                return (
                                    AgentStreamResponse(
                                        event_type="tool_call",
                                        text=tool_name,
                                        tool_name=tool_name,
                                    ),
                                    last_ai_message,
                                )

                        if hasattr(message, "content") and message.content:
                            content = message.content
                            if isinstance(content, str):
                                return (
                                    AgentStreamResponse(
                                        event_type="chat_message",
                                        text=content.replace("\n", "\\n"),
                                    ),
                                    last_ai_message,
                                )

                    elif isinstance(message, AIMessageChunk):
                        last_ai_message = message
                        if hasattr(message, "content") and message.content:
                            return (
                                AgentStreamResponse(
                                    event_type="chat_message",
                                    text=message.content.replace("\n", "\\n"),
                                ),
                                last_ai_message,
                            )

            elif stream_type == "updates":
                if isinstance(stream_data, dict):
                    if "__interrupt__" in stream_data:
                        interrupt_tuple = stream_data["__interrupt__"]
                        if interrupt_tuple and len(interrupt_tuple) > 0:
                            interrupt_obj = interrupt_tuple[0]
                            return (
                                AgentStreamResponse(
                                    event_type="interrupt",
                                    text=getattr(interrupt_obj, "value", ""),
                                ),
                                last_ai_message,
                            )

                    if "llm" in stream_data:
                        llm_update = stream_data["llm"]
                        if isinstance(llm_update, dict) and "messages" in llm_update:
                            messages = llm_update["messages"]
                            if messages:
                                final_message = messages[-1]
                                if isinstance(final_message, AIMessage):
                                    last_ai_message = final_message

                                    if (
                                        hasattr(final_message, "content")
                                        and final_message.content
                                    ):
                                        response_metadata = getattr(
                                            final_message, "response_metadata", {}
                                        )
                                        finish_reason = response_metadata.get(
                                            "finish_reason"
                                        )

                                        is_final = (
                                            finish_reason == "stop"
                                            or not hasattr(final_message, "tool_calls")
                                            or not final_message.tool_calls
                                        )

                                        if is_final:
                                            return (
                                                AgentStreamResponse(
                                                    event_type="end_of_chat",
                                                    text=final_message.content,
                                                    final_message=ConversationMessage(
                                                        role="assistant",
                                                        content=final_message.content,
                                                    ),
                                                    data=sources,
                                                ),
                                                last_ai_message,
                                            )

                    if "tools" in stream_data:
                        tools_update = stream_data["tools"]
                        if (
                            isinstance(tools_update, dict)
                            and "messages" in tools_update
                        ):
                            messages = tools_update["messages"]
                            for msg in messages:
                                if isinstance(msg, ToolMessage):
                                    artifact = getattr(msg, "artifact", None)
                                    tool_call_id = getattr(msg, "tool_call_id", None)
                                    if tool_call_id not in seen_tool_ids:
                                        seen_tool_ids.add(tool_call_id)
                                        if artifact:
                                            artifact_data = to_dict(artifact)
                                            if isinstance(artifact_data, list):
                                                sources.extend(artifact_data)
                                            else:
                                                sources.append(artifact_data)
                                        else:
                                            sources.append({"content": msg.content})

        return None, last_ai_message

    def _attach_token_usage(
        self, response: AgentStreamResponse, callbacks: List[Any]
    ) -> AgentStreamResponse:
        if response.event_type != "end_of_chat":
            return response
        token_info = _extract_token_usage(callbacks)
        if not token_info:
            return response
        return replace(
            response,
            token_usage=token_info["total_tokens"],
            extra_info=token_info,
        )

    async def run(
        self,
        chat_id: str,
        input_text: str,
        additional_params: dict[str, Any] = {},
    ) -> AsyncGenerator[AgentStreamResponse, None]:
        try:
            sources = []
            seen_tool_ids = set()
            apps: List[dict] = additional_params.get("apps", [])

            callbacks = [UsageMetadataCallbackHandler()]

            request_start_time = time.time()
            first_chat_token_sent = False

            try:
                file_ids = additional_params.get("fileIds", [])
                kids = additional_params.get("kids", [])
                entity_ids = file_ids
                folder_ids = kids
                web_search = additional_params.get("webSearch", False)
                user_id = additional_params.get("userId", None)

                config = {
                    "configurable": {
                        "thread_id": chat_id,
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "apps": apps,
                        "collection_name": "b2c",
                        "entity_ids": entity_ids,
                        "folder_ids": folder_ids,
                        "web_search": web_search,
                        "kids": kids,
                        "request_start_time": request_start_time,
                    },
                    "metadata": {},
                    "callbacks": callbacks,
                }
                


                system_prompt = get_system_prompt(additional_params=additional_params)
                initial_state = {
                    "messages": [HumanMessage(content=input_text)],
                    "system_prompt": system_prompt,
                    "current_model_index": additional_params.get("current_model_index"),
                }

                last_ai_message = None
                final_response_sent = False

                async for chunk in self.graph.astream(
                    initial_state,
                    config,
                    stream_mode=["updates", "messages"],
                ):
                    response, last_ai_message = self._process_chunk(
                        chunk, sources, seen_tool_ids, callbacks, last_ai_message
                    )
                    if response:
                        yield self._attach_token_usage(response, callbacks)

                if not final_response_sent and last_ai_message:
                    if hasattr(last_ai_message, "content") and last_ai_message.content:
                        yield self._attach_token_usage(
                            AgentStreamResponse(
                                event_type="end_of_chat",
                                text=last_ai_message.content,
                                final_message=ConversationMessage(
                                    role="assistant",
                                    content=last_ai_message.content,
                                ),
                                data=sources,
                            ),
                            callbacks,
                        )
            except Exception as e:
                logger.error("Error processing request: %s", e, exc_info=True)
                yield AgentStreamResponse(
                    event_type="chat_error",
                    text=f"Error processing request: {str(e)}",
                )

        except Exception as error:
            logger.error("Error processing request: %s", error, exc_info=True)
            yield AgentStreamResponse(
                event_type="chat_error", text=f"Error processing request: {str(error)}"
            )
