"""Agents service: process agent chat (streaming and non-streaming), save conversation via repo."""

import asyncio
import json
import logging
import ipaddress
from typing import Any, Dict, List, Optional, AsyncGenerator, Tuple
import time
from app.modules.agents.graph import get_agent
from app.modules.agents.models import ResumeConfig
from app.modules.agents.repository import AgentsRepository
from app.modules.common import get_usage
from app.modules.tag_classification import schedule_tag_classification
from common.entitlement import check_ai_entitlement
from common.modules.interrupt_client import update_latest_pending_interrupt

logger = logging.getLogger(__name__)

_repo = AgentsRepository()

# Customer-facing copy shown in the widget when AI is gated. Neutral on purpose —
# the chatting customer is not the merchant, so we don't surface billing details.
_BLOCKED_MESSAGES = {
    "TRIAL_ENDED": "This assistant is currently unavailable. Please try again later.",
    "LIMIT_REACHED": "This assistant is currently unavailable. Please try again later.",
}


def _sse_escape_text_for_data_line(text: str) -> str:
    """Escape newlines so each chat_message fits one SSE data line (EventSource ignores
    continuation lines without a ``data:`` prefix). Matches pre–LangGraph v2 agent streaming.
    """
    return (text or "").replace("\n", "\\n").replace("\r", "\\r")



async def _persist_conversation_messages(
    repo: AgentsRepository,
    *,
    chat_id: str,
    agent_id: str,
    session_id: str,
    user_message: str,
    assistant_content: str,
    sources: List[Any],
    token_data: int,
    extra_info: Optional[dict],
    response_time: float,
    additional_params: Dict[str, Any],
    location: Optional[
        Tuple[Optional[float], Optional[float], Optional[str], Optional[str]]
    ],
    is_preview: bool,
) -> None:
    """Append USER and/or ASSISTANT rows to AgentChatMessages (either side may be empty)."""
    if is_preview:
        return
    user_id = (
        additional_params.get("user_id") if isinstance(additional_params, dict) else None
    )
    if not user_id:
        logger.warning(
            "user_id not found in config for agent %s, skipping save",
            agent_id,
        )
        return

    try:
        db_success = await repo.save_conversation(
            chat_id,
            user_id,
            agent_id,
            session_id,
            user_message,
            assistant_content,
            sources,
            token_data,
            extra_info,
            response_time,
            latitude=location[0] if location else None,
            longitude=location[1] if location else None,
            country=location[2] if location else None,
            city=location[3] if location else None,
            channel="WEB",
            chat_title=None,
            integration_id=None,
        )
        if db_success:
            logger.info("Successfully saved messages for chat %s", chat_id)
            schedule_tag_classification(chat_id, agent_id)
            if token_data:
                usage_svc = get_usage()
                await usage_svc.update_usage_consumed(
                    user_id=user_id,
                    token_info=extra_info if isinstance(extra_info, dict) else {},
                )
        else:
            logger.warning(
                "Partial failure saving messages for chat %s - DB: %s",
                chat_id,
                db_success,
            )
    except Exception as e:
        logger.error("Failed to save messages: %s", e)


async def _resolve_location(
    repo: AgentsRepository,
    chat_id: str,
    client_ip: Optional[str],
    geoip_reader: Any,
) -> Optional[Tuple[Optional[float], Optional[float], Optional[str], Optional[str]]]:
    """DB check + GeoIP lookup — runs as a background task, never blocks the LLM path."""
    try:
        needs_location = await repo.chat_needs_location(chat_id)
    except Exception:
        return None
    if not needs_location or geoip_reader is None or not client_ip:
        return None
    try:
        ip_obj = ipaddress.ip_address(client_ip)
        if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_reserved or ip_obj.is_link_local:
            return None
        # geoip_reader.city() is a synchronous mmdb file read — run in thread pool.
        resp = await asyncio.to_thread(geoip_reader.city, client_ip)
        return (resp.location.latitude, resp.location.longitude, resp.country.iso_code, resp.city.name)
    except Exception:
        logger.debug("GeoIP lookup failed for ip=%s", client_ip, exc_info=True)
        return None


async def process_chat_request(
    message: str,
    agent_id: str,
    chat_id: str,
    session_id: str,
    is_preview: bool = False,
    resume_config: Optional[ResumeConfig] = None,
    customer_email: Optional[str] = None,
    customer_name: Optional[str] = None,
    interrupt_payload: Any = None,
    repo: Optional[AgentsRepository] = None,
    client_ip: Optional[str] = None,
    geoip_reader: Any = None,
    product_faq_context: Optional[Dict[str, Any]] = None,
    collection_id: Optional[str] = None,
    prefetched_config: Optional[Dict[str, Any]] = None,
    model_override: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Process an agent chat request and yield SSE chunks."""
    sources: List[Any] = []
    assistant_content = ""
    token_data = 0
    start_time = time.time()
    response_time = 0
    location: Optional[Tuple[Optional[float], Optional[float], Optional[str], Optional[str]]] = None
    repo = repo or _repo

    try:
        agent = get_agent()
        extra_info = None
        max_retries = 3
        retry_count = 0
        last_error = None

        base_config = prefetched_config if prefetched_config is not None else (await repo.get_config(agent_id) or {})
        additional_params: Dict[str, Any] = {
            **(base_config if isinstance(base_config, dict) else {}),
            "chat_id": chat_id,
            "session_id": session_id,
        }
        if product_faq_context and isinstance(product_faq_context, dict):
            additional_params["product_faq_context"] = product_faq_context
        # Skip on HITL resume — that continues an existing thread.
        if collection_id and not (resume_config is not None and resume_config.interrupt):
            additional_params["active_collection_id"] = collection_id

        # Pre-flight usage gate: block the AI when the owner's trial has ended or
        # their message_credits are spent. Fails open (see check_ai_entitlement).
        entitlement = await check_ai_entitlement(additional_params.get("user_id"))
        if not entitlement.get("allowed", True):
            reason = entitlement.get("blockedReason") or "LIMIT_REACHED"
            logger.info(
                "AI blocked for user=%s agent=%s reason=%s",
                additional_params.get("user_id"),
                agent_id,
                reason,
            )
            blocked_payload = json.dumps(
                {
                    "reason": reason,
                    "message": _BLOCKED_MESSAGES.get(
                        reason, _BLOCKED_MESSAGES["LIMIT_REACHED"]
                    ),
                }
            )
            yield f"event: blocked\ndata: {blocked_payload}\n\n"
            yield "event: end_of_chat\ndata: []\n\n"
            return

        _location_task: Optional[asyncio.Task] = None
        if not is_preview and chat_id:
            _location_task = asyncio.create_task(
                _resolve_location(repo, chat_id, client_ip, geoip_reader)
            )

        resume_user_fields: Optional[Dict[str, Any]] = None
        if resume_config is not None:
            ru: Dict[str, Any] = {}
            if resume_config.name and str(resume_config.name).strip():
                ru["name"] = str(resume_config.name).strip()
            if resume_config.email and str(resume_config.email).strip():
                ru["email"] = str(resume_config.email).strip()
            if resume_config.fields:
                for k, v in resume_config.fields.items():
                    if not k or v is None:
                        continue
                    # Keep media_upload MediaRef[] lists structured; coerce scalars to str.
                    if isinstance(v, str):
                        if v.strip():
                            ru.setdefault(k, v.strip())
                    elif isinstance(v, (list, dict)):
                        if v:
                            ru.setdefault(k, v)
                    else:
                        s = str(v).strip()
                        if s:
                            ru.setdefault(k, s)
            if ru:
                resume_user_fields = ru

        is_hitl = False
        while retry_count <= max_retries:
            error_occurred = False
            sources = []
            is_hitl = False

            async for chunk in agent.run(
                chat_id,
                session_id,
                message,
                additional_params,
                resume_data=resume_config.choice if resume_config else False,
                hitl=resume_config.interrupt if resume_config else False,
                customer_email=customer_email,
                customer_name=customer_name,
                interrupt_payload=interrupt_payload,
                resume_user_fields=resume_user_fields,
                is_preview=is_preview,
                model_override=model_override,
            ):
                if chunk.event_type == "chat_message":
                    response_time = (
                        response_time
                        if response_time != 0 and response_time < (time.time() - start_time)
                        else (time.time() - start_time)
                    )
                    safe = _sse_escape_text_for_data_line(chunk.text or "")
                    yield f"event: chat_message\ndata: {safe}\n\n"
                elif chunk.event_type == "tool_call":
                    response_time = (
                        response_time
                        if response_time != 0 and response_time < (time.time() - start_time)
                        else (time.time() - start_time)
                    )
                    payload = json.dumps(
                        {
                            "text": chunk.text,
                            "data": chunk.data,
                            "tool": chunk.tool_name,
                        }
                    )
                    yield f"event: tool_call\ndata: {payload}\n\n"
                elif chunk.event_type == "status_update":
                    yield f"event: status_update\ndata: {json.dumps({'status': chunk.text})}\n\n"
                elif chunk.event_type == "interrupt":
                    response_time = (
                        response_time
                        if response_time != 0 and response_time < (time.time() - start_time)
                        else (time.time() - start_time)
                    )
                    is_hitl = True
                    interrupt_payload = json.dumps(
                        {
                            "text": chunk.text,
                            "interrupt_data": (
                                chunk.interrupt_data
                                if hasattr(chunk, "interrupt_data")
                                else None
                            ),
                        }
                    )
                    yield f"event: interrupt\ndata: {interrupt_payload}\n\n"
                    assistant_content = chunk.text
                elif chunk.event_type == "chat_error":
                    response_time = (
                        response_time
                        if response_time != 0 and response_time < (time.time() - start_time)
                        else (time.time() - start_time)
                    )
                    error_occurred = True
                    last_error = chunk.text
                    logger.error(
                        "Chat error (attempt %s/%s): %s",
                        retry_count + 1,
                        max_retries + 1,
                        chunk.text,
                    )
                    break
                elif chunk.event_type == "human_handoff":
                    response_time = (
                        response_time
                        if response_time != 0 and response_time < (time.time() - start_time)
                        else (time.time() - start_time)
                    )
                    hd = chunk.data if chunk.data else {}
                    logger.info(
                        "SSE human_handoff: chat_id=%s handoff_triggered=%s keys=%s",
                        chat_id,
                        hd.get("handoff_triggered"),
                        list(hd.keys()) if isinstance(hd, dict) else type(hd).__name__,
                    )
                    handoff_payload = json.dumps(
                        {
                            "text": chunk.text,
                            "handoff_triggered": True,
                            "data": hd,
                        }
                    )
                    yield f"event: human_handoff\ndata: {handoff_payload}\n\n"
                    # Auto-escalation ends the stream early; persist the visitor turn only.
                    if (
                        not is_preview
                        and message.strip()
                        and not (resume_config and resume_config.interrupt)
                    ):
                        rt = (
                            response_time
                            if response_time
                            else (time.time() - start_time)
                        )
                        if _location_task is not None:
                            location = await _location_task
                            _location_task = None
                        await _persist_conversation_messages(
                            repo,
                            chat_id=chat_id,
                            agent_id=agent_id,
                            session_id=session_id,
                            user_message=message,
                            assistant_content="",
                            sources=[],
                            token_data=0,
                            extra_info=None,
                            response_time=rt,
                            additional_params=additional_params,
                            location=location,
                            is_preview=is_preview,
                        )
                    return
                elif chunk.event_type == "end_of_chat":
                    response_time = (
                        response_time
                        if response_time != 0 and response_time < (time.time() - start_time)
                        else (time.time() - start_time)
                    )
                    assistant_content = (
                        chunk.final_message.content
                        if chunk.final_message
                        else chunk.text
                    )
                    sources = chunk.data or []
                    extra_info = chunk.extra_info
                    if chunk.token_usage is not None:
                        token_data = chunk.token_usage
                        logger.info(
                            "Saving message for chat %s with tokens: %s",
                            chat_id,
                            token_data,
                        )
                    yield f"event: end_of_chat\ndata: {json.dumps(chunk.data or [])}\n\n"

            if not error_occurred:
                break
            if error_occurred and retry_count < max_retries:
                retry_count += 1
                logger.info(
                    "Retrying chat request (attempt %s/%s)",
                    retry_count + 1,
                    max_retries + 1,
                )
                continue
            if error_occurred and retry_count >= max_retries:
                logger.error(
                    "Chat error after %s attempts: %s", max_retries + 1, last_error
                )
                yield f"event: chat_error\ndata: {_sse_escape_text_for_data_line(last_error or '')}\n\n"
                break

        # Await the background location task once — result is used for all persist calls below.
        if _location_task is not None:
            location = await _location_task

        # Do not persist interrupt prompt / placeholder as a USER message on HITL resume.
        user_message_for_db = (
            ""
            if (resume_config is not None and resume_config.interrupt)
            else message
        )

        if assistant_content and not is_hitl:
            await _persist_conversation_messages(
                repo,
                chat_id=chat_id,
                agent_id=agent_id,
                session_id=session_id,
                user_message=user_message_for_db,
                assistant_content=assistant_content,
                sources=sources,
                token_data=token_data,
                extra_info=extra_info,
                response_time=response_time,
                additional_params=additional_params,
                location=location,
                is_preview=is_preview,
            )
        elif is_hitl and user_message_for_db.strip():
            # Graph paused for HITL: store the real visitor message (not the later resume payload).
            rt = response_time if response_time else (time.time() - start_time)
            await _persist_conversation_messages(
                repo,
                chat_id=chat_id,
                agent_id=agent_id,
                session_id=session_id,
                user_message=user_message_for_db,
                assistant_content="",
                sources=[],
                token_data=0,
                extra_info=None,
                response_time=rt,
                additional_params=additional_params,
                location=location,
                is_preview=is_preview,
            )

        if (
            chat_id
            and resume_config is not None
            and resume_config.interrupt
        ):
            try:
                await update_latest_pending_interrupt(
                    agent_chat_id=chat_id,
                    status="approved" if resume_config.choice else "rejected",
                    resume_fields=resume_user_fields,
                )
            except Exception as e:
                logger.warning(
                    "update interrupt (resume) failed chat_id=%s: %s",
                    chat_id,
                    e,
                )

    except Exception as e:
        logger.error("Error in process_chat_request: %s", e, exc_info=True)
        raise


async def process_chat_request_non_streaming(
    message: str,
    agent_id: str,
    is_preview: bool = True,
    resume_config: Optional[ResumeConfig] = None,
    additional_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Process agent chat and return full response (no streaming)."""
    try:
        agent = get_agent()

        if not additional_params:
            additional_params = await _repo.get_config(agent_id) or {}
        else:
            additional_params = dict(additional_params)

        additional_params.setdefault("agent_id", agent_id)

        resume_user_fields: Optional[Dict[str, Any]] = None
        if resume_config is not None:
            ru: Dict[str, Any] = {}
            if resume_config.name and str(resume_config.name).strip():
                ru["name"] = str(resume_config.name).strip()
            if resume_config.email and str(resume_config.email).strip():
                ru["email"] = str(resume_config.email).strip()
            if ru:
                resume_user_fields = ru

        result = await agent.run_non_streaming(
            message,
            additional_params,
            resume_data=resume_config.choice if resume_config else False,
            hitl=resume_config.interrupt if resume_config else False,
            resume_user_fields=resume_user_fields,
            is_preview=is_preview,
        )

        out: Dict[str, Any] = {
            "message": result.get("content") or result.get("message") or "",
            "sources": result.get("sources", []),
            "token_usage": result.get("token_usage", 0),
            "extra_info": result.get("extra_info"),
            "is_interrupted": result.get("is_interrupted", False),
            "tool_calls": result.get("tool_calls", []),
        }
        if result.get("human_handoff"):
            hd = result.get("handoff_data", {})
            logger.info(
                "non-streaming human_handoff: agent_id=%s chat_id=%s handoff_triggered=%s",
                agent_id,
                (additional_params or {}).get("chat_id"),
                isinstance(hd, dict) and hd.get("handoff_triggered"),
            )
            out["human_handoff"] = True
            out["handoff_data"] = hd

        return {"success": True, "data": out}
    except Exception as e:
        logger.error("Error in process_chat_request_non_streaming: %s", e, exc_info=True)
        return {"success": False, "error": str(e), "data": None}
