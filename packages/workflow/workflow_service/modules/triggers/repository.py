import json
import logging
from typing import Any
from uuid import uuid4

from common.infrastructure.postgres import get_pg

logger = logging.getLogger(__name__)


class TriggerRepository:

    async def create(
        self,
        workflow_def_id: str,
        trigger_type: str,
        config_json: dict,
        agent_id: str | None = None,
        filter_json: dict | None = None,
        is_active: bool = True,
    ) -> dict | None:
        webhook_url = None
        if trigger_type == "WEBHOOK":
            webhook_url = f"/webhooks/wf/{uuid4()}"

        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public."WorkflowTrigger"
                    (id, "workflowDefinitionId", type, "configJson", "webhookUrl",
                     "agentId", "filterJson", "isActive")
                VALUES ($1, $2, $3::"TriggerType", $4::jsonb, $5,
                        $6, $7::jsonb, $8)
                RETURNING *
                """,
                str(uuid4()),
                workflow_def_id,
                trigger_type,
                json.dumps(config_json),
                webhook_url,
                agent_id if agent_id else None,
                json.dumps(filter_json) if filter_json else None,
                is_active,
            )
            return dict(row) if row else None

    async def list_by_workflow(self, workflow_def_id: str) -> list[dict]:
        pg = get_pg()
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM public."WorkflowTrigger"
                WHERE "workflowDefinitionId" = $1
                ORDER BY "createdAt" DESC
                """,
                workflow_def_id,
            )
            return [dict(r) for r in rows]

    async def get_by_id(self, trigger_id: str) -> dict | None:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM public."WorkflowTrigger" WHERE id = $1',
                trigger_id,
            )
            return dict(row) if row else None

    async def update(self, trigger_id: str, fields: dict[str, Any]) -> dict | None:
        if not fields:
            return await self.get_by_id(trigger_id)

        set_clauses = []
        params: list[Any] = []
        idx = 1

        for key, value in fields.items():
            if key in ("configJson", "filterJson"):
                set_clauses.append(f'"{key}" = ${idx}::jsonb')
                params.append(json.dumps(value) if value is not None else None)
            elif key == "agentId":
                set_clauses.append(f'"agentId" = ${idx}')
                params.append(value if value else None)
            elif key == "isActive":
                set_clauses.append(f'"isActive" = ${idx}')
                params.append(value)
            else:
                set_clauses.append(f'"{key}" = ${idx}')
                params.append(value)
            idx += 1

        params.append(trigger_id)
        query = f"""
            UPDATE public."WorkflowTrigger"
            SET {", ".join(set_clauses)}
            WHERE id = ${idx}
            RETURNING *
        """

        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(query, *params)
            return dict(row) if row else None

    async def delete(self, trigger_id: str) -> bool:
        pg = get_pg()
        async with pg.acquire() as conn:
            result = await conn.execute(
                'DELETE FROM public."WorkflowTrigger" WHERE id = $1',
                trigger_id,
            )
            return result == "DELETE 1"

    async def get_active_by_type(self, trigger_type: str) -> list[dict]:
        pg = get_pg()
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT wt.*, wd."workspaceId"
                FROM public."WorkflowTrigger" wt
                JOIN public."WorkflowDefinition" wd ON wd.id = wt."workflowDefinitionId"
                WHERE wt.type = $1::"TriggerType"
                  AND wt."isActive" = true
                  AND wd.status = 'ACTIVE'
                """,
                trigger_type,
            )
            return [dict(r) for r in rows]
