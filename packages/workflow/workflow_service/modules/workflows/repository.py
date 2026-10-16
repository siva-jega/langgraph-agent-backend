import json
import logging
from typing import Any
from uuid import uuid4

from common.infrastructure.postgres import get_pg

logger = logging.getLogger(__name__)


class WorkflowRepository:

    async def get_by_id(self, workflow_id: str) -> dict | None:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM public."Workflows" WHERE id = $1',
                workflow_id,
            )
            return dict(row) if row else None

    async def list_by_workspace(
        self,
        workspace_id: str,
        status: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict]:
        pg = get_pg()
        offset = (page - 1) * limit
        async with pg.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    """
                    SELECT * FROM public."Workflows"
                    WHERE "workspaceId" = $1 AND status = $2::"WorkflowStatus"
                    ORDER BY "createdAt" DESC
                    LIMIT $3 OFFSET $4
                    """,
                    workspace_id, status, limit, offset,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM public."Workflows"
                    WHERE "workspaceId" = $1
                    ORDER BY "createdAt" DESC
                    LIMIT $2 OFFSET $3
                    """,
                    workspace_id, limit, offset,
                )
            return [dict(r) for r in rows]

    async def count_by_workspace(
        self, workspace_id: str, status: str | None = None
    ) -> int:
        pg = get_pg()
        async with pg.acquire() as conn:
            if status:
                return await conn.fetchval(
                    """
                    SELECT count(*) FROM public."Workflows"
                    WHERE "workspaceId" = $1 AND status = $2::"WorkflowStatus"
                    """,
                    workspace_id, status,
                ) or 0
            return await conn.fetchval(
                'SELECT count(*) FROM public."Workflows" WHERE "workspaceId" = $1',
                workspace_id,
            ) or 0

    async def verify_ownership(self, workflow_id: str, workspace_id: str) -> bool:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT 1 FROM public."Workflows" WHERE id = $1 AND "workspaceId" = $2',
                workflow_id, workspace_id,
            )
            return row is not None

    async def publish(self, workflow_id: str) -> dict | None:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public."Workflows"
                SET status = 'ACTIVE'::"WorkflowStatus",
                    version = version + 1,
                    "updatedAt" = now()
                WHERE id = $1
                RETURNING *
                """,
                workflow_id,
            )
            return dict(row) if row else None

    async def deactivate(self, workflow_id: str) -> dict | None:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public."Workflows"
                SET status = 'INACTIVE'::"WorkflowStatus", "updatedAt" = now()
                WHERE id = $1
                RETURNING *
                """,
                workflow_id,
            )
            return dict(row) if row else None

    # ── Nodes ──────────────────────────────────────────────────────────────

    async def get_nodes(self, workflow_id: str) -> list[dict]:
        pg = get_pg()
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM public."WorkflowNodes"
                WHERE "workflowId" = $1
                ORDER BY "createdAt" ASC
                """,
                workflow_id,
            )
            return [dict(r) for r in rows]

    # ── Edges ──────────────────────────────────────────────────────────────

    async def get_edges(self, workflow_id: str) -> list[dict]:
        pg = get_pg()
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                'SELECT * FROM public."WorkflowEdges" WHERE "workflowId" = $1',
                workflow_id,
            )
            return [dict(r) for r in rows]
