"""HTTP client for NestJS interrupt persistence internal APIs.

Interrupts (human handoff, ticket creation, escalation, channel handoff) are
persisted as AgentChatMessages rows via NestJS so the agent Conversations tab
can render them inline with regular messages.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from common.core.nestjs_staging_defaults import (
    resolved_nestjs_base_url,
    resolved_nestjs_internal_secret,
)

logger = logging.getLogger(__name__)


def _base_url() -> str:
    return resolved_nestjs_base_url()


def _secret() -> str:
    return resolved_nestjs_internal_secret()


async def create_interrupt(
    agent_chat_id: str,
    interrupt_type: str,
    tool_display_name: str,
    tool_name: Optional[str] = None,
    tool_args: Optional[Dict[str, Any]] = None,
    status: str = "pending",
    escalation: Optional[Dict[str, Any]] = None,
    channel: Optional[str] = None,
    handoff_request_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
) -> Optional[str]:
    """POST an interrupt row. Returns the AgentChatMessages id on success."""
    base = _base_url()
    secret = _secret()
    if not base or not secret or not agent_chat_id:
        logger.debug(
            "create_interrupt skipped: missing base_url, secret, or agent_chat_id"
        )
        return None
    url = f"{base}/internal/chats/{agent_chat_id}/interrupts"
    headers = {"x-internal-secret": secret, "Content-Type": "application/json"}
    payload: Dict[str, Any] = {
        "type": interrupt_type,
        "toolDisplayName": tool_display_name or interrupt_type,
        "status": status,
    }
    if tool_name:
        payload["toolName"] = tool_name
    if isinstance(tool_args, dict) and tool_args:
        payload["toolArgs"] = tool_args
    if isinstance(escalation, dict) and escalation:
        payload["escalation"] = escalation
    if channel:
        payload["channel"] = channel
    if handoff_request_id:
        payload["handoffRequestId"] = handoff_request_id
    if ticket_id:
        payload["ticketId"] = ticket_id
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers, timeout=10.0)
        if r.status_code in (200, 201):
            data = r.json() if r.content else {}
            inner = data.get("result") if isinstance(data, dict) else None
            body = inner if isinstance(inner, dict) else data
            msg_id = body.get("id") if isinstance(body, dict) else None
            logger.info(
                "interrupt persisted: chat_id=%s type=%s id=%s",
                agent_chat_id,
                interrupt_type,
                msg_id,
            )
            return msg_id
        logger.error(
            "create_interrupt: HTTP %s chat_id=%s type=%s body=%s",
            r.status_code,
            agent_chat_id,
            interrupt_type,
            (r.text or "")[:500],
        )
        return None
    except Exception as e:
        logger.error("create_interrupt failed: %s", e, exc_info=True)
        return None


async def update_latest_pending_interrupt(
    agent_chat_id: str,
    status: str,
    resume_fields: Optional[Dict[str, Any]] = None,
    handoff_request_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
) -> bool:
    """PATCH the most recent pending interrupt row for a chat."""
    base = _base_url()
    secret = _secret()
    if not base or not secret or not agent_chat_id:
        logger.debug(
            "update_latest_pending_interrupt skipped: missing base_url, secret, or chat id"
        )
        return False
    url = f"{base}/internal/chats/{agent_chat_id}/interrupts/latest"
    headers = {"x-internal-secret": secret, "Content-Type": "application/json"}
    payload: Dict[str, Any] = {"status": status}
    if isinstance(resume_fields, dict) and resume_fields:
        payload["resumeFields"] = resume_fields
    if handoff_request_id:
        payload["handoffRequestId"] = handoff_request_id
    if ticket_id:
        payload["ticketId"] = ticket_id
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(url, json=payload, headers=headers, timeout=10.0)
        if r.status_code in (200, 201, 204):
            logger.info(
                "interrupt (latest) updated: chat_id=%s status=%s",
                agent_chat_id,
                status,
            )
            return True
        logger.error(
            "update_latest_pending_interrupt: HTTP %s chat_id=%s body=%s",
            r.status_code,
            agent_chat_id,
            (r.text or "")[:500],
        )
        return False
    except Exception as e:
        logger.error("update_latest_pending_interrupt failed: %s", e, exc_info=True)
        return False


async def update_interrupt(
    agent_chat_id: str,
    message_id: str,
    status: str,
    resume_fields: Optional[Dict[str, Any]] = None,
    handoff_request_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
) -> bool:
    """PATCH resume outcome (approved / rejected / expired) on an interrupt row."""
    base = _base_url()
    secret = _secret()
    if not base or not secret or not agent_chat_id or not message_id:
        logger.debug(
            "update_interrupt skipped: missing base_url, secret, chat, or message id"
        )
        return False
    url = f"{base}/internal/chats/{agent_chat_id}/interrupts/{message_id}"
    headers = {"x-internal-secret": secret, "Content-Type": "application/json"}
    payload: Dict[str, Any] = {"status": status}
    if isinstance(resume_fields, dict) and resume_fields:
        payload["resumeFields"] = resume_fields
    if handoff_request_id:
        payload["handoffRequestId"] = handoff_request_id
    if ticket_id:
        payload["ticketId"] = ticket_id
    try:
        async with httpx.AsyncClient() as client:
            r = await client.patch(url, json=payload, headers=headers, timeout=10.0)
        if r.status_code in (200, 201, 204):
            logger.info(
                "interrupt updated: chat_id=%s message_id=%s status=%s",
                agent_chat_id,
                message_id,
                status,
            )
            return True
        logger.error(
            "update_interrupt: HTTP %s chat_id=%s message_id=%s body=%s",
            r.status_code,
            agent_chat_id,
            message_id,
            (r.text or "")[:500],
        )
        return False
    except Exception as e:
        logger.error("update_interrupt failed: %s", e, exc_info=True)
        return False
