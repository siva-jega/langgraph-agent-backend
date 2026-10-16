import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from common.infrastructure.postgres import get_pg

logger = logging.getLogger(__name__)


class RunRepository:

    async def create(
        self,
        workflow_id: str,
        trigger_node_id: str | None = None,
        input_data: dict | None = None,
        correlation_id: str | None = None,
    ) -> dict | None:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public."WorkflowRuns"
                    (id, "workflowId", "triggerNodeId", "inputData", "correlationId")
                VALUES ($1, $2, $3, $4::jsonb, $5)
                RETURNING *
                """,
                str(uuid4()),
                workflow_id,
                trigger_node_id if trigger_node_id else None,
                json.dumps(input_data) if input_data else None,
                correlation_id,
            )
            return dict(row) if row else None

    async def get_by_id(self, run_id: str) -> dict | None:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM public."WorkflowRuns" WHERE id = $1',
                run_id,
            )
            return dict(row) if row else None

    async def list_by_workflow(
        self,
        workflow_id: str,
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
                    SELECT * FROM public."WorkflowRuns"
                    WHERE "workflowId" = $1
                      AND status = $2::"RunStatus"
                    ORDER BY "createdAt" DESC
                    LIMIT $3 OFFSET $4
                    """,
                    workflow_id,
                    status,
                    limit,
                    offset,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM public."WorkflowRuns"
                    WHERE "workflowId" = $1
                    ORDER BY "createdAt" DESC
                    LIMIT $2 OFFSET $3
                    """,
                    workflow_id,
                    limit,
                    offset,
                )
            return [dict(r) for r in rows]

    async def count_by_workflow(
        self, workflow_id: str, status: str | None = None
    ) -> int:
        pg = get_pg()
        async with pg.acquire() as conn:
            if status:
                return await conn.fetchval(
                    """
                    SELECT count(*) FROM public."WorkflowRuns"
                    WHERE "workflowId" = $1
                      AND status = $2::"RunStatus"
                    """,
                    workflow_id,
                    status,
                ) or 0
            else:
                return await conn.fetchval(
                    """
                    SELECT count(*) FROM public."WorkflowRuns"
                    WHERE "workflowId" = $1
                    """,
                    workflow_id,
                ) or 0

    async def update_status(
        self,
        run_id: str,
        status: str,
        error_message: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> dict | None:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE public."WorkflowRuns"
                SET status = $2::"RunStatus",
                    "errorMessage" = COALESCE($3, "errorMessage"),
                    "startedAt" = COALESCE($4, "startedAt"),
                    "completedAt" = COALESCE($5, "completedAt")
                WHERE id = $1
                RETURNING *
                """,
                run_id,
                status,
                error_message,
                started_at,
                completed_at,
            )
            return dict(row) if row else None

    # ── Run Nodes (was Run Steps) ──────────────────────────────────────────

    async def create_run_step(self, run_id: str, step_id: str) -> dict | None:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public."WorkflowNodeRuns" (id, "workflowRunId", "nodeId")
                VALUES ($1, $2, $3)
                RETURNING *
                """,
                str(uuid4()),
                run_id,
                step_id,
            )
            return dict(row) if row else None

    async def update_run_step(
        self,
        run_step_id: str,
        status: str | None = None,
        input_data: Any | None = None,
        output_data: Any | None = None,
        error_message: str | None = None,
        retry_count: int | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> dict | None:
        set_clauses = []
        params: list[Any] = []
        idx = 1

        if status is not None:
            set_clauses.append(f'status = ${idx}::"StepRunStatus"')
            params.append(status)
            idx += 1
        if input_data is not None:
            set_clauses.append(f'"inputData" = ${idx}::jsonb')
            params.append(json.dumps(input_data) if not isinstance(input_data, str) else input_data)
            idx += 1
        if output_data is not None:
            set_clauses.append(f'"outputData" = ${idx}::jsonb')
            params.append(json.dumps(output_data) if not isinstance(output_data, str) else output_data)
            idx += 1
        if error_message is not None:
            set_clauses.append(f'"errorMessage" = ${idx}')
            params.append(error_message)
            idx += 1
        if retry_count is not None:
            set_clauses.append(f'"retryCount" = ${idx}')
            params.append(retry_count)
            idx += 1
        if started_at is not None:
            set_clauses.append(f'"startedAt" = ${idx}')
            params.append(started_at)
            idx += 1
        if completed_at is not None:
            set_clauses.append(f'"completedAt" = ${idx}')
            params.append(completed_at)
            idx += 1

        if not set_clauses:
            return None

        params.append(run_step_id)
        query = f"""
            UPDATE public."WorkflowNodeRuns"
            SET {", ".join(set_clauses)}
            WHERE id = ${idx}
            RETURNING *
        """

        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(query, *params)
            return dict(row) if row else None

    async def get_run_step_by_node(self, run_id: str, node_id: str) -> dict | None:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM public."WorkflowNodeRuns"
                WHERE "workflowRunId" = $1 AND "nodeId" = $2
                LIMIT 1
                """,
                run_id,
                node_id,
            )
            return dict(row) if row else None

    async def get_run_steps(self, run_id: str) -> list[dict]:
        pg = get_pg()
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM public."WorkflowNodeRuns"
                WHERE "workflowRunId" = $1
                ORDER BY "startedAt" ASC NULLS LAST
                """,
                run_id,
            )
            return [dict(r) for r in rows]
