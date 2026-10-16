"""HTTP client for NestJS handoff internal APIs (no direct DB writes)."""

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


async def check_active_handoff_api(agent_chat_id: str) -> bool:
    """True if PENDING or IN_PROGRESS handoff exists for this agent chat."""
    base = _base_url()
    secret = _secret()
    if not base or not secret or not agent_chat_id:
        logger.debug(
            "handoff active check skipped: missing base_url, secret, or agent_chat_id"
        )
        return False
    url = f"{base}/internal/handoffs/active"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                url,
                params={"agentChatId": agent_chat_id},
                headers={"x-internal-secret": secret},
                timeout=5.0,
            )
        if r.status_code != 200:
            logger.warning(
                "handoff active check: HTTP %s for chat_id=%s body=%s",
                r.status_code,
                agent_chat_id,
                (r.text or "")[:300],
            )
            return False
        data = r.json()
        inner = data.get("result") if isinstance(data, dict) else data
        if not isinstance(inner, dict):
            logger.warning(
                "handoff active check: unexpected JSON for chat_id=%s: %s",
                agent_chat_id,
                data,
            )
            return False
        active = bool(inner.get("active"))
        if active:
            logger.info(
                "handoff active check: chat_id=%s has active human handoff — agent run short-circuits",
                agent_chat_id,
            )
        else:
            logger.debug("handoff active check: chat_id=%s active=False", agent_chat_id)
        return active
    except Exception as e:
        logger.warning(
            "handoff active check failed for chat_id=%s: %s", agent_chat_id, e
        )
        return False


async def trigger_handoff_api(
    agent_chat_id: str,
    session_id: str,
    agent_id: str,
    priority: str,
    summary: str,
    fields: Optional[Dict[str, Any]] = None,
    end_user_id: Optional[str] = None,
) -> bool:
    """POST NestJS internal handoff creation. Visitor data is only `fields` (CrmFieldDefinition keys)."""
    base = _base_url()
    secret = _secret()
    if not base or not secret:
        logger.error("trigger_handoff_api: NESTJS_URL or NESTJS_INTERNAL_SECRET not set")
        return False
    url = f"{base}/internal/chats/{agent_chat_id}/handoff"
    headers = {"x-internal-secret": secret, "Content-Type": "application/json"}
    clean_fields: Dict[str, Any] = {}
    if isinstance(fields, dict):
        for k, v in fields.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)):
                if v:  # keep non-empty media arrays/objects as-is
                    clean_fields[str(k)] = v
            else:
                s = str(v).strip()
                if s:
                    clean_fields[str(k)] = s
    payload: dict = {
        "priority": (priority or "MEDIUM").upper(),
        "reason": summary or "",
        "source": "widget",
        "sessionId": session_id,
        "agentId": agent_id,
        "fields": clean_fields,
    }
    if end_user_id:
        payload["userId"] = end_user_id
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers, timeout=15.0)
        if r.status_code in (200, 201):
            logger.info(
                "handoff widget: NestJS OK chat_id=%s agent_id=%s status=%s",
                agent_chat_id,
                agent_id,
                r.status_code,
            )
            return True
        logger.error(
            "handoff widget: NestJS HTTP %s for chat_id=%s agent_id=%s body=%s",
            r.status_code,
            agent_chat_id,
            agent_id,
            (r.text or "")[:500],
        )
        return False
    except Exception as e:
        logger.error("trigger_handoff_api failed: %s", e, exc_info=True)
        return False


async def trigger_escalation_handoff_api(
    agent_chat_id: str,
    session_id: str,
    agent_id: str,
    reason: str,
    user_id: Optional[str] = None,
) -> bool:
    """POST internal handoff for auto-escalation (Nest creates AgentChat if missing)."""
    base = _base_url()
    secret = _secret()
    if not base or not secret:
        logger.error(
            "trigger_escalation_handoff_api: NESTJS_URL or NESTJS_INTERNAL_SECRET not set"
        )
        return False
    if not agent_chat_id or not session_id or not agent_id:
        logger.warning(
            "trigger_escalation_handoff_api skipped: missing chat_id, session_id, or agent_id"
        )
        return False
    url = f"{base}/internal/chats/{agent_chat_id}/handoff"
    headers = {"x-internal-secret": secret, "Content-Type": "application/json"}
    payload: dict = {
        "reason": reason or "",
        "source": "ai_escalation",
        "sessionId": session_id,
        "agentId": agent_id,
    }
    if user_id:
        payload["userId"] = user_id
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(url, json=payload, headers=headers, timeout=15.0)
        if r.status_code in (200, 201):
            logger.info(
                "handoff escalation: NestJS OK chat_id=%s agent_id=%s status=%s",
                agent_chat_id,
                agent_id,
                r.status_code,
            )
            return True
        logger.error(
            "handoff escalation: NestJS HTTP %s for chat_id=%s agent_id=%s body=%s",
            r.status_code,
            agent_chat_id,
            agent_id,
            (r.text or "")[:500],
        )
        return False
    except Exception as e:
        logger.error("trigger_escalation_handoff_api failed: %s", e, exc_info=True)
        return False
