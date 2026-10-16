from typing import AsyncGenerator, Any, List, Optional, Union, cast
from app.modules.agents.graph.nodes import (
    GraphState,
    llm_node,
    tools_node,
    trim_messages_node,
)
from app.modules.agents.graph.headroom_compress import compress_tool_messages_node
from app.modules.common.handoff_client import check_active_handoff_api
from common.modules.interrupt_client import create_interrupt
from app.modules.agents.graph.escalation import escalation_check_node
from app.modules.agents.graph.prompt import (
    PROMPT_BUILDER_VERSION,
    get_system_prompt,
    get_system_prompt_with_meta,
)
from app.core.config import app_settings
from common.llm_utils import cache_read_tokens
import logging
from app.otel_utils import no_trace
from app.core.types import AgentStreamResponse, ConversationMessage
from langgraph.types import Command
from langchain_core.messages import ToolMessage, AIMessageChunk, HumanMessage

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.runnables import RunnableConfig

from app.modules.agents.graph.tracing import build_trace_metadata
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import tools_condition

logger = logging.getLogger(__name__)

def _resolve_human_handoff_enabled(
    *,
    db_handoff_on: bool,
    has_handoff_skill: bool,
) -> bool:
    return db_handoff_on and has_handoff_skill


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


class Graph:
    """Compiled LangGraph chat graph: LLM + tools, with optional checkpointer."""

    def __init__(self, checkpointer=None):
        self.streaming = True
        self.checkpointer = checkpointer or None
        self.graph = self._build_graph()

    def _build_graph(self):
        """Build the LangGraph ReAct agent graph.

        The escalation_check node sits between the final LLM response and END.
        When tools_condition would normally route to END, it routes to
        escalation_check instead. Tool-calling loops are unaffected.
        """
        builder = StateGraph(GraphState)

        builder.add_node("trim", trim_messages_node)
        builder.add_node("llm", llm_node)
        builder.add_node("tools", tools_node)
        builder.add_node("escalation_check", escalation_check_node)

        builder.add_edge(START, "trim")
        builder.add_edge("trim", "llm")
        builder.add_conditional_edges(
            "llm",
            tools_condition,
            {"tools": "tools", "__end__": "escalation_check"},
        )
        # Headroom (Phase 1, flag-gated): compress large tool outputs between tools -> trim.
        # When disabled the graph is byte-identical to before (node is never added).
        if app_settings.HEADROOM_ENABLED:
            builder.add_node("compress_tools", compress_tool_messages_node)
            builder.add_edge("tools", "compress_tools")
            builder.add_edge("compress_tools", "trim")
        else:
            builder.add_edge("tools", "trim")
        builder.add_edge("escalation_check", END)

        return builder.compile(checkpointer=self.checkpointer)

    def _to_dict(self, obj):
        """Convert Pydantic objects and primitives to JSON-serializable dicts."""
        try:
            from pydantic import BaseModel

            if isinstance(obj, BaseModel):
                return obj.model_dump()
        except ImportError:
            pass
        if isinstance(obj, list):
            return [self._to_dict(item) for item in obj]
        if isinstance(obj, dict):
            return {k: self._to_dict(v) for k, v in obj.items()}
        obj_dict = getattr(obj, "__dict__", None)
        if isinstance(obj_dict, dict):
            return {k: self._to_dict(v) for k, v in obj_dict.items()}
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        return str(obj)

    async def run(
        self,
        chat_id: str,
        session_id: str,
        input_text: str,
        additional_params: dict[str, Any] = {},
        resume_data: bool = False,
        hitl: bool = False,
        customer_email: Optional[str] = None,
        customer_name: Optional[str] = None,
        interrupt_payload: Any = None,
        resume_user_fields: Optional[dict[str, Any]] = None,
        is_preview: bool = False,
        model_override: Optional[str] = None,
    ) -> AsyncGenerator[AgentStreamResponse, None]:
        """Run the agent and stream responses."""
        callbacks = [UsageMetadataCallbackHandler()]

        try:
            additional_params = additional_params or {}
            config_dict = additional_params.get("config", {})
            if not isinstance(config_dict, dict):
                config_dict = {}

            entity_ids = config_dict.get("entityIds", [])
            folder_ids = config_dict.get("folderIds", [])
            apps_config = config_dict.get("appsConfig", [])
            owner_email = config_dict.get("ownerEmail", "")
            persona = config_dict.get("persona", {}) or {}
            model = persona.get("model", "") if isinstance(persona, dict) else ""
            apps = config_dict.get("apps", [])
            skills = config_dict.get("skills", [])

            # prompt_params = DB config only — no per-request dynamic values
            prompt_params = dict(config_dict)

            resolved_customer_email = (
                customer_email
                if customer_email is not None
                else additional_params.get("customer_email")
            )
            resolved_customer_name = (
                customer_name
                if customer_name is not None
                else additional_params.get("customer_name")
            )
            resolved_interrupt_payload = (
                interrupt_payload
                if interrupt_payload is not None
                else additional_params.get("interrupt_payload")
            )

            handoff_skill = next(
                (
                    s
                    for s in skills
                    if isinstance(s, dict)
                    and (s.get("toolName") or "") == "human_handoff_trigger"
                ),
                None,
            )
            escalation_cfg = (
                handoff_skill.get("config", {}) if handoff_skill else {}
            )
            if not isinstance(escalation_cfg, dict):
                escalation_cfg = {}

            # Build per-tool skill config lookup for interrupt form injection.
            # skill_config  → the human_handoff_trigger skill config (singular, backward compat)
            # skill_configs → dict of toolName → config for all skills (used by aide tools)
            skill_config = handoff_skill.get("config", {}) if handoff_skill else {}
            skill_configs: dict = {}
            for s in skills:
                if not isinstance(s, dict):
                    continue
                tool_name_key = (s.get("toolName") or "").strip()
                if not tool_name_key or not isinstance(s.get("config"), dict):
                    continue
                if tool_name_key == "use_skill":
                    sid = str(s.get("id") or "").strip()
                    if sid:
                        skill_configs[f"use_skill::{sid}"] = s["config"]
                else:
                    skill_configs[tool_name_key] = s["config"]

            db_handoff_on = bool(
                config_dict.get("human_handoff_enabled", False)
                or config_dict.get("humanHandoffEnabled", False)
            )
            human_handoff_enabled = _resolve_human_handoff_enabled(
                db_handoff_on=db_handoff_on,
                has_handoff_skill=handoff_skill is not None,
            )
            prompt_params["human_handoff_enabled"] = human_handoff_enabled
            active_collection_id = additional_params.get("active_collection_id")
            prompt_params["active_collection_id"] = active_collection_id
            auto_escalate_flag = bool(
                escalation_cfg.get("autoEscalate")
                or escalation_cfg.get("auto_escalate")
                or escalation_cfg.get("enabled")
            )

            crm_ai_populated = config_dict.get("crmAiPopulatedFields") or []
            if not isinstance(crm_ai_populated, list):
                crm_ai_populated = []

            logger.info(
                "agent run: chat_id=%s human_handoff_enabled=%s "
                "(db_handoff=%s has_handoff_skill=%s auto_escalate_in_skill_config=%s)",
                chat_id,
                human_handoff_enabled,
                db_handoff_on,
                handoff_skill is not None,
                auto_escalate_flag,
            )
            # Build graph config
            if chat_id and not hitl and not is_preview:
                if await check_active_handoff_api(chat_id):
                    static_msg = (
                        "A human agent is currently handling this conversation."
                    )
                    yield AgentStreamResponse(
                        event_type="chat_message",
                        text=static_msg,
                    )
                    yield AgentStreamResponse(
                        event_type="end_of_chat",
                        text=static_msg,
                        final_message=ConversationMessage(
                            role="assistant", content=static_msg
                        ),
                        data=[],
                        token_usage=0,
                        extra_info={},
                    )
                    return

            # Build the system prompt + its version/hash. The metadata is only
            # consumed by tracers — production traffic (no LangSmith callback)
            # ignores it. During eval runs the LangSmith experiment carries
            # these fields so prompt versions are diffable in the UI.
            system_prompt, prompt_version, prompt_hash = get_system_prompt_with_meta(
                additional_params=prompt_params
            )

            # Prefetch store catalog context from ingested ShopifyProducts (no live API call)
            shopify_catalog_context = ""
            if not hitl and not is_preview:
                try:
                    from common.tools.providers.shopify.tools import _get_shopify_connector_id
                    from common.tools.providers.shopify.catalog import fetch_shopify_catalog_context

                    connector_id = _get_shopify_connector_id(apps)
                    if connector_id:
                        logger.info("[catalog] resolved connector_id=%s for chat_id=%s", connector_id, chat_id)
                        shopify_catalog_context = await fetch_shopify_catalog_context(connector_id)
                    else:
                        logger.info("[catalog] no Shopify connector found in apps list for chat_id=%s", chat_id)
                except Exception as _catalog_err:
                    logger.warning("[catalog] prefetch failed for chat_id=%s: %s", chat_id, _catalog_err)

            config = {
                "configurable": {
                    "thread_id": chat_id,
                    "chat_id": chat_id,
                    "collection_name": "b2c",
                    "entity_ids": entity_ids,
                    "folder_ids": folder_ids,
                    "apps_config": apps_config,
                    "user_id": additional_params.get("user_id"),
                    "apps": apps,
                    "skills": skills,
                    "agent_id": additional_params.get("agent_id"),
                    "session_id": session_id,
                    "customer_email": resolved_customer_email,
                    "customer_name": resolved_customer_name,
                    "interrupt_payload": resolved_interrupt_payload,
                    "persona": persona if isinstance(persona, dict) else {},
                    "model": model_override or model,
                    "owner_email": owner_email,
                    "human_handoff_enabled": human_handoff_enabled,
                    "escalation": escalation_cfg,
                    "skill_config": skill_config,
                    "skill_configs": skill_configs,
                    "crmAiPopulatedFields": crm_ai_populated,
                    "is_preview": is_preview,
                    "product_faq_context": additional_params.get("product_faq_context"),
                    "active_collection_id": active_collection_id,
                    "shopify_catalog_context": shopify_catalog_context,
                },
                "metadata": build_trace_metadata(
                    agent_id=additional_params.get("agent_id"),
                    chat_id=chat_id,
                    session_id=session_id,
                    model=model_override or model,
                    is_preview=is_preview,
                    extra={
                        "prompt_builder_version": prompt_version,
                        "prompt_hash": prompt_hash,
                    },
                ),
                "callbacks": callbacks,
            }

            # Build initial state
            initial_state: GraphState = {
                "messages": [HumanMessage(content=input_text)],
                "system_prompt": system_prompt,
                "current_model_index": None,
                "active_skill_group": None,
            }

            # Stream agent response
            stream_input: Union[GraphState, Command]
            if hitl:
                decision_type = "approve" if resume_data else "reject"
                resume_payload: dict[str, Any] = {
                    "decisions": [{"type": decision_type}],
                }
                if resume_user_fields:
                    # Backward-compat top-level name/email keys.
                    for key in ("name", "email"):
                        val = resume_user_fields.get(key)
                        if val is not None and str(val).strip():
                            resume_payload[key] = str(val).strip()
                    # Full fields map for dynamic interrupt forms (phone, custom fields, etc.)
                    resume_payload["fields"] = resume_user_fields
                stream_input = Command(resume=resume_payload)
            else:
                stream_input = initial_state

            sources = []
            seen_tool_ids = set()
            final_text = ""

            runnable_config = cast(RunnableConfig, config)
            async for chunk in self.graph.astream(
                stream_input,
                runnable_config,
                stream_mode=["messages", "updates", "custom"],
                version="v2",
            ):
                chunk_type = chunk.get("type")

                if chunk_type == "messages":
                    # LLM token stream. `escalation_check` runs a classifier LLM whose JSON
                    # must not be forwarded as chat tokens (it leaked into the widget UI).
                    msg, metadata = chunk["data"]
                    if not isinstance(msg, AIMessageChunk) or not msg.content:
                        continue
                    meta = metadata if isinstance(metadata, dict) else {}
                    if meta.get("langgraph_node") == "escalation_check":
                        continue
                    piece = msg.content
                    if isinstance(piece, list):
                        blocks: list[str] = []
                        for b in piece:
                            if isinstance(b, str):
                                blocks.append(b)
                            elif isinstance(b, dict) and b.get("type") == "text":
                                blocks.append(str(b.get("text", "")))
                            else:
                                blocks.append(str(b))
                        text_piece = "".join(blocks)
                    else:
                        text_piece = piece
                    final_text += text_piece
                    yield AgentStreamResponse(
                        event_type="chat_message",
                        text=text_piece,
                    )

                elif chunk_type == "custom":
                    # Custom events from nodes
                    data = chunk["data"]
                    if not isinstance(data, dict):
                        continue
                    if data.get("event") == "tool_call":
                        tool_name = data.get("tool_name", "")
                        tool_args = data.get("tool_args", {})

                        if tool_name == "search_knowledgebase" and isinstance(
                            tool_args, dict
                        ):
                            tool_args = tool_args.get("query", tool_args)
                        elif isinstance(tool_args, dict) and len(tool_args) == 1:
                            tool_args = list(tool_args.values())[0]

                        yield AgentStreamResponse(
                            event_type="tool_call",
                            text=f"Triggering {tool_name}",
                            tool_name=tool_name,
                            data=tool_args,
                        )
                    elif data.get("event") == "status_update":
                        status_label = data.get("status", "")
                        if status_label:
                            yield AgentStreamResponse(
                                event_type="status_update",
                                text=status_label,
                            )
                    elif data.get("event") == "escalation_detected":
                        yield AgentStreamResponse(
                            event_type="human_handoff",
                            text="I'm connecting you with a human agent who can better assist you.",
                            data={
                                "escalation_detected": True,
                                "confidence": data.get("confidence"),
                                "category": data.get("category"),
                                "reason": data.get("reason"),
                                "handoff_triggered": data.get("handoff_triggered"),
                            },
                        )

                elif chunk_type == "updates":
                    # State updates; check for interrupts and tool results
                    raw_updates = chunk.get("data")
                    if not isinstance(raw_updates, dict):
                        continue
                    node_updates = cast(dict[str, Any], raw_updates)

                    # Interrupt handling
                    if "__interrupt__" in node_updates:
                        interrupt_tuple = node_updates["__interrupt__"]
                        if interrupt_tuple and len(interrupt_tuple) > 0:
                            interrupt_value = interrupt_tuple[0].value
                            if isinstance(interrupt_value, dict):
                                text = (
                                    interrupt_value.get("tool_display_name")
                                    or interrupt_value.get("type")
                                    or "Approval required"
                                )
                            else:
                                text = (
                                    str(interrupt_value)
                                    if interrupt_value
                                    else "Approval required"
                                )
                            yield AgentStreamResponse(
                                event_type="interrupt",
                                text=text,
                                interrupt_data=(
                                    interrupt_value
                                    if isinstance(interrupt_value, dict)
                                    else None
                                ),
                            )
                            if chat_id and isinstance(interrupt_value, dict):
                                try:
                                    tool_name = (
                                        interrupt_value.get("tool_name") or ""
                                    )
                                    if tool_name == "create_ticket_aide":
                                        i_type = "ticket"
                                    else:
                                        i_type = "human_handoff"
                                    await create_interrupt(
                                        agent_chat_id=chat_id,
                                        interrupt_type=i_type,
                                        tool_display_name=text,
                                        tool_name=tool_name or None,
                                        tool_args=interrupt_value.get("tool_args"),
                                        status="pending",
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "persist interrupt failed for chat_id=%s: %s",
                                        chat_id,
                                        e,
                                    )

                    # Tool result artifacts
                    if "tools" in node_updates:
                        tools_update = node_updates["tools"]
                        if isinstance(tools_update, dict):
                            for msg in tools_update.get("messages") or []:
                                if isinstance(msg, ToolMessage):
                                    artifact = getattr(msg, "artifact", None)
                                    tool_call_id = getattr(msg, "tool_call_id", None)
                                    if tool_call_id not in seen_tool_ids:
                                        seen_tool_ids.add(tool_call_id)
                                        if artifact:
                                            artifact_data = self._to_dict(artifact)
                                            if isinstance(artifact_data, list):
                                                sources.extend(artifact_data)
                                            else:
                                                sources.append(artifact_data)
                                        else:
                                            sources.append({"content": msg.content})

            # Extract token usage
            token_usage = {}
            usage_cb = callbacks[0]
            if hasattr(usage_cb, "usage_metadata") and usage_cb.usage_metadata:
                model_key = next(iter(usage_cb.usage_metadata))
                raw_usage = usage_cb.usage_metadata[model_key]
                input_details = raw_usage.get("input_token_details") or {}
                token_usage = {
                    "model": model_key,
                    "total_tokens": raw_usage.get("total_tokens", 0),
                    "input_tokens": raw_usage.get("input_tokens", 0),
                    "output_tokens": raw_usage.get("output_tokens", 0),
                    "cache_read_input_tokens": cache_read_tokens(input_details),
                }

            # Emit end_of_chat
            if final_text:
                yield AgentStreamResponse(
                    event_type="end_of_chat",
                    text=final_text,
                    final_message=ConversationMessage(
                        role="assistant", content=final_text
                    ),
                    data=sources,
                    token_usage=token_usage.get("total_tokens", 0),
                    extra_info=token_usage,
                )

        except Exception as e:
            logger.error("Error processing request: %s", e, exc_info=True)
            yield AgentStreamResponse(
                event_type="chat_error",
                text=f"Error processing request: {str(e)}",
            )

    async def run_non_streaming(
        self,
        input_text: str,
        additional_params: dict[str, Any] = {},
        resume_data: bool = False,
        hitl: bool = False,
        resume_user_fields: Optional[dict[str, Any]] = None,
        is_preview: bool = False,
    ) -> dict[str, Any]:
        """Run the graph and return a single result dict (no streaming)."""
        content = ""
        sources: List[Any] = []
        token_usage = 0
        extra_info = None
        is_interrupted = False
        tool_calls: List[Any] = []

        # Derive chat_id and session_id from additional_params (used in streaming path)
        chat_id = ""
        session_id = ""
        if isinstance(additional_params, dict):
            chat_id = additional_params.get("chat_id", "") or additional_params.get(
                "agent_id", ""
            )
            session_id = additional_params.get("session_id", "")

        cust_email = (
            additional_params.get("customer_email")
            if isinstance(additional_params, dict)
            else None
        )
        cust_name = (
            additional_params.get("customer_name")
            if isinstance(additional_params, dict)
            else None
        )
        cust_interrupt = (
            additional_params.get("interrupt_payload")
            if isinstance(additional_params, dict)
            else None
        )

        async for response in self.run(
            chat_id,
            session_id,
            input_text,
            additional_params,
            resume_data=resume_data,
            hitl=hitl,
            customer_email=cust_email,
            customer_name=cust_name,
            interrupt_payload=cust_interrupt,
            resume_user_fields=resume_user_fields,
            is_preview=is_preview,
        ):
            if response.event_type == "chat_message":
                content += response.text or ""
            elif response.event_type == "tool_call":
                tool_calls.append(
                    {
                        "name": response.tool_name,
                        "text": response.text,
                        "data": response.data,
                    }
                )
            elif response.event_type == "interrupt":
                is_interrupted = True
                content = response.text or ""
            elif response.event_type == "chat_error":
                raise RuntimeError(response.text or "Chat error")
            elif response.event_type == "human_handoff":
                return {
                    "content": response.text or "",
                    "sources": sources,
                    "token_usage": 0,
                    "extra_info": None,
                    "is_interrupted": False,
                    "tool_calls": tool_calls,
                    "human_handoff": True,
                    "handoff_data": response.data,
                }
            elif response.event_type == "end_of_chat":
                content = response.text or (
                    response.final_message.content if response.final_message else ""
                )
                if response.data is not None:
                    sources = response.data
                if response.token_usage is not None:
                    token_usage = response.token_usage
                if response.extra_info is not None:
                    extra_info = response.extra_info

        return {
            "content": content,
            "sources": sources,
            "token_usage": token_usage,
            "extra_info": extra_info,
            "is_interrupted": is_interrupted,
            "tool_calls": tool_calls,
        }
