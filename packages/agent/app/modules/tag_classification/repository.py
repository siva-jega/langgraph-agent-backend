import logging
from typing import List, Tuple

from common.infrastructure import get_pg

from app.modules.tag_classification.models import AgentTag

logger = logging.getLogger(__name__)


async def fetch_agent_tags(agent_id: str) -> List[AgentTag]:
    pg = get_pg()
    async with pg.acquire() as conn:
        rows = await conn.fetch(
            'SELECT id, title, description, rules, category FROM "Tag" WHERE "agentId" = $1',
            agent_id,
        )
    return [
        AgentTag(
            id=str(r["id"]),
            title=r["title"],
            description=r["description"],
            rules=r["rules"],
            category=r["category"],
        )
        for r in rows
    ]


async def fetch_recent_messages(chat_id: str, limit: int) -> List[Tuple[str, str]]:
    pg = get_pg()
    async with pg.acquire() as conn:
        rows = await conn.fetch(
            'SELECT role, message FROM "AgentChatMessages" '
            'WHERE "agentChatId" = $1 ORDER BY "createdAt" DESC LIMIT $2',
            chat_id,
            limit,
        )
    # newest-first from SQL; reverse to chronological for the prompt
    return [(str(r["role"]), r["message"] or "") for r in reversed(rows)]


async def attach_tags_to_chat(chat_id: str, tag_ids: List[str]) -> int:
    if not tag_ids:
        return 0
    pg = get_pg()
    # Untyped $1/$2 so Postgres infers the column type (uuid) and asyncpg encodes the str to match.
    async with pg.acquire() as conn:
        await conn.executemany(
            'INSERT INTO "_AgentChatUserTags" ("A", "B") VALUES ($1, $2) '
            'ON CONFLICT ("A", "B") DO NOTHING',
            [(chat_id, tid) for tid in tag_ids],
        )
    return len(tag_ids)
