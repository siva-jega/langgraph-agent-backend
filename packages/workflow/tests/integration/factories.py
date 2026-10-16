"""Factory helpers for creating test data directly in the DB."""
import json
from uuid import uuid4

import asyncpg


async def make_workflow(pool: asyncpg.Pool, workspace_id: str, **kwargs) -> dict:
    wf_id = str(uuid4())
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public."Workflows"
              (id, "workspaceId", name, status, version, "createdAt", "updatedAt")
            VALUES ($1, $2, $3, $4::"WorkflowStatus", 1, now(), now())
            RETURNING *
            """,
            wf_id,
            workspace_id,
            kwargs.get("name", "Test Workflow"),
            kwargs.get("status", "DRAFT"),
        )
    return dict(row)


async def make_node(pool: asyncpg.Pool, workflow_id: str, **kwargs) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public."WorkflowNodes"
              (id, "workflowId", "nodeId", type, category, label, config,
               "inputParameters", "outputParameters", "positionX", "positionY",
               "errorPolicy", "createdAt")
            VALUES ($1, $2, $3, $4, $5::"NodeCategory", $6, $7::jsonb,
                    $8::jsonb, $9::jsonb, 0, 0, $10::"ErrorPolicy", now())
            RETURNING *
            """,
            str(uuid4()),
            workflow_id,
            kwargs.get("node_id", f"node-{uuid4().hex[:8]}"),
            kwargs.get("type", "HTTP_REQUEST"),
            kwargs.get("category", "ACTION"),
            kwargs.get("label", "Test Node"),
            json.dumps(kwargs.get("config", {})),
            json.dumps(kwargs.get("input_parameters", [])),
            json.dumps(kwargs.get("output_parameters", [])),
            kwargs.get("error_policy", "FAIL"),
        )
    return dict(row)


async def make_edge(
    pool: asyncpg.Pool,
    workflow_id: str,
    source_node_id: str,
    target_node_id: str,
    source_handle: str | None = None,
) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public."WorkflowEdges"
              (id, "workflowId", "sourceNodeId", "targetNodeId", "sourceHandle", "createdAt")
            VALUES ($1, $2, $3, $4, $5, now())
            RETURNING *
            """,
            str(uuid4()),
            workflow_id, source_node_id, target_node_id, source_handle,
        )
    return dict(row)


async def make_run(pool: asyncpg.Pool, workflow_id: str, **kwargs) -> dict:
    run_id = str(uuid4())
    input_data = kwargs.get("input_data", None)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public."WorkflowRuns"
              (id, "workflowId", "triggerNodeId", "inputData", "correlationId", "createdAt")
            VALUES ($1, $2, $3, $4::jsonb, $5, now())
            RETURNING *
            """,
            run_id,
            workflow_id,
            kwargs.get("trigger_node_id", None),
            json.dumps(input_data) if input_data is not None else None,
            kwargs.get("correlation_id", None),
        )
    return dict(row)


async def seed_graph(
    pool: asyncpg.Pool,
    workspace_id: str,
    nodes: list[dict],
    edges: list[dict],
    **wf_kwargs,
) -> dict:
    """Create a workflow with the given nodes and edges in one call."""
    wf = await make_workflow(pool, workspace_id, **wf_kwargs)
    for n in nodes:
        await make_node(pool, wf["id"], **n)
    for e in edges:
        await make_edge(pool, wf["id"], e["source"], e["target"], e.get("source_handle"))
    return wf
