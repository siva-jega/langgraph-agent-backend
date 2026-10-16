import logging

from common.infrastructure.postgres import get_pg

logger = logging.getLogger(__name__)


class WorkflowNodeRepository:

    async def get_active_conversation_event_triggers(
        self, workspace_id: str
    ) -> list[dict]:
        """Return CONVERSATION_EVENT trigger nodes for active workflows in a workspace."""
        pg = get_pg()
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT wn.*, w."workspaceId"
                FROM public."WorkflowNodes" wn
                JOIN public."Workflows" w ON w.id = wn."workflowId"
                WHERE wn.type = 'CONVERSATION_EVENT'
                  AND wn.category = 'TRIGGER'
                  AND w.status = 'ACTIVE'
                  AND w."workspaceId" = $1
                """,
                workspace_id,
            )
            return [dict(r) for r in rows]

    async def get_all_active_conversation_event_triggers(self) -> list[dict]:
        """Return all CONVERSATION_EVENT trigger nodes across all active workflows."""
        pg = get_pg()
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT wn.*, w."workspaceId"
                FROM public."WorkflowNodes" wn
                JOIN public."Workflows" w ON w.id = wn."workflowId"
                WHERE wn.type = 'CONVERSATION_EVENT'
                  AND wn.category = 'TRIGGER'
                  AND w.status = 'ACTIVE'
                """
            )
            return [dict(r) for r in rows]
