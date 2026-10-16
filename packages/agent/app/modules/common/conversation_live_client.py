"""Bump Redis-backed live-conversation activity for an AgentChat (same semantics as user-backend ConversationLiveService)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from common.infrastructure import get_pg, get_redis

logger = logging.getLogger(__name__)

# Matches user-backend conversation-live.service.ts
REDIS_KEY_PREFIX = "conv:live:"
WORKSPACE_INDEX_MAX_IDLE_SEC = 604800  # 7d


def _ttl_ms() -> int:
    raw = os.getenv("CONVERSATION_LIVE_TTL_SECONDS", "180").strip()
    try:
        sec = int(raw)
    except ValueError:
        sec = 180
    return max(sec, 1) * 1000


def _redis_key(workspace_id: str) -> str:
    return f"{REDIS_KEY_PREFIX}{workspace_id}"


async def _workspace_id_for_agent_chat(agent_chat_id: str) -> Optional[str]:
    """Resolve workspace via AgentChats → Agent (same as Nest recordActivityForAgentChat)."""
    try:
        pg = get_pg()
    except RuntimeError:
        logger.warning(
            "conversation_live: Postgres unavailable; skipped chat_id=%s",
            agent_chat_id,
        )
        return None
    try:
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT ag."workspaceId" AS wid
                FROM "AgentChats" c
                INNER JOIN "Agent" ag ON ag.id = c."agentId"
                WHERE c.id = $1
                LIMIT 1
                """,
                agent_chat_id,
            )
        if not row or not row["wid"]:
            logger.warning(
                "conversation_live: no workspace for chat_id=%s (missing chat/agent?)",
                agent_chat_id,
            )
            return None
        return str(row["wid"]).strip() or None
    except Exception:
        logger.warning(
            "conversation_live: workspace lookup failed chat_id=%s",
            agent_chat_id,
            exc_info=True,
        )
        return None


async def _record_live_activity(agent_chat_id: str) -> None:
    aid = agent_chat_id.strip()
    workspace_id = await _workspace_id_for_agent_chat(aid)
    if not workspace_id:
        return

    try:
        redis_wrap = get_redis()
    except RuntimeError:
        logger.warning(
            "conversation_live: Redis unavailable; skipped chat_id=%s workspace=%s",
            aid,
            workspace_id,
        )
        return

    key = _redis_key(workspace_id)
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - _ttl_ms()
    client = redis_wrap.client

    try:
        await client.zremrangebyscore(key, 0, cutoff)
        await client.zadd(key, {aid: now_ms})
        await client.expire(key, WORKSPACE_INDEX_MAX_IDLE_SEC)
        logger.info(
            "conversation_live: OK chat_id=%s workspace=%s",
            aid,
            workspace_id,
        )
    except Exception as e:
        logger.warning(
            "conversation_live: Redis failed chat_id=%s workspace=%s: %s",
            aid,
            workspace_id,
            e,
            exc_info=True,
        )


def schedule_conversation_live_activity(agent_chat_id: Optional[str]) -> None:
    """Fire-and-forget: update Redis live index for this chat (async; requires running event loop)."""
    if not agent_chat_id or not str(agent_chat_id).strip():
        logger.warning(
            "conversation_live: skipped (empty agent_chat_id) — inbox may not show live"
        )
        return
    cid = str(agent_chat_id).strip()

    async def _run() -> None:
        await _record_live_activity(cid)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
        logger.debug("conversation_live: background task queued for chat_id=%s", cid)
    except RuntimeError:
        logger.warning(
            "conversation_live: no running event loop; skipped chat_id=%s (tests only?)",
            cid,
        )
