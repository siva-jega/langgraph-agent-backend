"""Compiled LangGraph + non-streaming runner for external channels only.

No escalation node, no active-handoff API gate, and ``human_handoff_enabled``
is forced off in configurable (see ``nodes._get_tools``).
"""

from __future__ import annotations

import logging
from typing import Any, List, cast

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition

from app.modules.channels.ai.nodes import (
    ChannelGraphState,
    llm_node,
    tools_node,
    trim_messages_node,
)

logger = logging.getLogger(__name__)


def _to_dict(obj: Any) -> Any:
    try:
        from pydantic import BaseModel

        if isinstance(obj, BaseModel):
            return obj.model_dump()
    except ImportError:
        pass
    if isinstance(obj, list):
        return [_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    obj_dict = getattr(obj, "__dict__", None)
    if isinstance(obj_dict, dict):
        return {k: _to_dict(v) for k, v in obj_dict.items()}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _build_channel_runtime_config(
    *,
    chat_id: str,
    session_id: str,
    input_text: str,
    additional_params: dict[str, Any],
    callbacks: list,
    is_preview: bool = False,
) -> tuple[dict[str, Any], ChannelGraphState]:
    """Mirror web ``Graph.run`` configurable wiring; omit handoff / live-session checks."""
    additional_params = additional_params or {}
    config_dict = additional_params.get("config", {})
    if not isinstance(config_dict, dict):
        config_dict = {}

    entity_ids = config_dict.get("entityIds", [])
    folder_ids = config_dict.get("folderIds", [])
    apps_config = config_dict.get("appsConfig", [])
    owner_email = config_dict.get("ownerEmail", "")
    model = config_dict.get("persona", {}).get("model", "")
    apps = config_dict.get("apps", [])
    skills = config_dict.get("skills", [])

    handoff_skill = next(
        (
            s
            for s in skills
            if isinstance(s, dict) and (s.get("toolName") or "") == "human_handoff_trigger"
        ),
        None,
    )
    escalation_cfg = handoff_skill.get("config", {}) if handoff_skill else {}
    if not isinstance(escalation_cfg, dict):
        escalation_cfg = {}

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

    channel_val = additional_params.get("channel") or "web"

    crm_ai_populated = config_dict.get("crmAiPopulatedFields") or []
    if not isinstance(crm_ai_populated, list):
        crm_ai_populated = []

    logger.info(
        "channel graph run: chat_id=%s channel=%s (configurable human_handoff forced off)",
        chat_id,
        channel_val,
    )

    system_prompt = additional_params.get("channel_system_prompt")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ValueError("channel_system_prompt is required for channel graph runs")

    config: dict[str, Any] = {
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
            "customer_email": additional_params.get("customer_email"),
            "customer_name": additional_params.get("customer_name"),
            "interrupt_payload": additional_params.get("interrupt_payload"),
            "model": model,
            "owner_email": owner_email,
            "human_handoff_enabled": False,
            "persona": config_dict.get("persona", {}) or {},
            "escalation": escalation_cfg,
            "skill_config": skill_config,
            "skill_configs": skill_configs,
            "crmAiPopulatedFields": crm_ai_populated,
            "channel": channel_val,
            "sender_display_name": additional_params.get("sender_display_name"),
            "sender_phone": additional_params.get("sender_phone"),
            "whatsapp_phone_number_id": additional_params.get("whatsapp_phone_number_id"),
            "whatsappFlowContext": config_dict.get("whatsappFlowContext"),
            "is_preview": is_preview,
        },
        "metadata": {},
        "callbacks": callbacks,
    }

    initial_state: ChannelGraphState = {
        "messages": [HumanMessage(content=input_text)],
        "system_prompt": system_prompt.strip(),
        "current_model_index": None,
        "active_skill_group": None,
    }

    return config, initial_state


class ChannelGraph:
    """Channel-only ReAct graph (trim → LLM ↔ tools → END)."""

    def __init__(self, checkpointer=None):
        self.streaming = True
        self.checkpointer = checkpointer or None
        self.graph = self._compile()

    def _compile(self):
        builder = StateGraph(ChannelGraphState)
        builder.add_node("trim", trim_messages_node)
        builder.add_node("llm", llm_node)
        builder.add_node("tools", tools_node)
        builder.add_edge(START, "trim")
        builder.add_edge("trim", "llm")
        builder.add_conditional_edges(
            "llm",
            tools_condition,
            {"tools": "tools", "__end__": END},
        )
        builder.add_edge("tools", "trim")
        return builder.compile(checkpointer=self.checkpointer)

    def _to_dict(self, obj: Any) -> Any:
        return _to_dict(obj)

    async def run_non_streaming(
        self,
        input_text: str,
        additional_params: dict[str, Any] | None = None,
        *,
        is_preview: bool = False,
    ) -> dict[str, Any]:
        """Run the channel graph once and return the same shape as web ``Graph.run_non_streaming``."""
        additional_params = additional_params or {}
        chat_id = additional_params.get("chat_id", "") or additional_params.get("agent_id", "")
        session_id = additional_params.get("session_id", "")

        callbacks: list = [UsageMetadataCallbackHandler()]

        config, initial_state = _build_channel_runtime_config(
            chat_id=chat_id,
            session_id=session_id,
            input_text=input_text,
            additional_params=additional_params,
            callbacks=callbacks,
            is_preview=is_preview,
        )

        content = ""
        sources: List[Any] = []
        token_usage = 0
        extra_info: Any = None
        is_interrupted = False
        tool_calls: List[Any] = []
        seen_tool_ids: set[str] = set()
        final_text = ""

        runnable_config = cast(RunnableConfig, config)
        async for chunk in self.graph.astream(
            initial_state,
            runnable_config,
            stream_mode=["messages", "updates", "custom"],
            durability="exit",
            version="v2",
        ):
            chunk_type = chunk.get("type")

            if chunk_type == "messages":
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

            elif chunk_type == "custom":
                data = chunk["data"]
                if not isinstance(data, dict):
                    continue
                if data.get("event") == "tool_call":
                    tool_calls.append(
                        {
                            "name": data.get("tool_name", ""),
                            "text": f"Triggering {data.get('tool_name', '')}",
                            "data": data.get("tool_args"),
                        }
                    )

            elif chunk_type == "updates":
                raw_updates = chunk.get("data")
                if not isinstance(raw_updates, dict):
                    continue
                node_updates = cast(dict[str, Any], raw_updates)

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
                            text = str(interrupt_value) if interrupt_value else "Approval required"
                        is_interrupted = True
                        content = text

                if "tools" in node_updates:
                    tools_update = node_updates["tools"]
                    if isinstance(tools_update, dict):
                        for msg in tools_update.get("messages") or []:
                            if isinstance(msg, ToolMessage):
                                artifact = getattr(msg, "artifact", None)
                                tool_call_id = getattr(msg, "tool_call_id", None)
                                if tool_call_id and tool_call_id not in seen_tool_ids:
                                    seen_tool_ids.add(tool_call_id)
                                    if artifact:
                                        artifact_data = self._to_dict(artifact)
                                        if isinstance(artifact_data, list):
                                            sources.extend(artifact_data)
                                        else:
                                            sources.append(artifact_data)
                                    else:
                                        sources.append({"content": msg.content})

        usage_cb = callbacks[0]
        token_usage_dict: dict[str, Any] = {}
        if hasattr(usage_cb, "usage_metadata") and usage_cb.usage_metadata:
            model_key = next(iter(usage_cb.usage_metadata))
            raw_usage = usage_cb.usage_metadata[model_key]
            token_usage_dict = {
                "model": model_key,
                "total_tokens": raw_usage.get("total_tokens", 0),
                "input_tokens": raw_usage.get("input_tokens", 0),
                "output_tokens": raw_usage.get("output_tokens", 0),
            }
            token_usage = token_usage_dict.get("total_tokens", 0)
            extra_info = token_usage_dict

        if final_text:
            content = final_text

        return {
            "content": content,
            "sources": sources,
            "token_usage": token_usage,
            "extra_info": extra_info,
            "is_interrupted": is_interrupted,
            "tool_calls": tool_calls,
        }
