"""
Integration tests for RunRepository and WorkflowRepository.
Require a live Postgres database (use -m integration to select).
"""
import json
import pytest
import pytest_asyncio
from uuid import uuid4

from tests.integration.factories import (
    make_workflow,
    make_node,
    make_edge,
    make_run,
)


pytestmark = pytest.mark.integration


# ── WorkflowRepository ────────────────────────────────────────────────────────

class TestWorkflowRepository:

    async def test_get_by_id_returns_workflow(self, pg_pool, workspace_seed, workflow_repo):
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id, name="Repo Test Workflow")

        result = await workflow_repo.get_by_id(wf["id"])

        assert result is not None
        assert result["id"] == wf["id"]
        assert result["name"] == "Repo Test Workflow"

    async def test_get_by_id_returns_none_for_missing(self, workflow_repo):
        result = await workflow_repo.get_by_id(str(uuid4()))
        assert result is None

    async def test_get_nodes_returns_in_created_at_asc_order(self, pg_pool, workspace_seed, workflow_repo):
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id)

        n1 = await make_node(pg_pool, wf["id"], node_id="n1", label="First")
        n2 = await make_node(pg_pool, wf["id"], node_id="n2", label="Second")
        n3 = await make_node(pg_pool, wf["id"], node_id="n3", label="Third")

        nodes = await workflow_repo.get_nodes(wf["id"])

        assert len(nodes) == 3
        # ORDER BY createdAt ASC — n1 created before n2 before n3
        node_ids = [n["nodeId"] for n in nodes]
        assert node_ids == ["n1", "n2", "n3"]

    async def test_get_edges_returns_source_handle(self, pg_pool, workspace_seed, workflow_repo):
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id)
        await make_node(pg_pool, wf["id"], node_id="src")
        await make_node(pg_pool, wf["id"], node_id="tgt")
        await make_edge(pg_pool, wf["id"], "src", "tgt", source_handle="true")

        edges = await workflow_repo.get_edges(wf["id"])

        assert len(edges) == 1
        assert edges[0]["sourceNodeId"] == "src"
        assert edges[0]["targetNodeId"] == "tgt"
        assert edges[0]["sourceHandle"] == "true"

    async def test_get_edges_source_handle_none_when_not_set(self, pg_pool, workspace_seed, workflow_repo):
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id)
        await make_edge(pg_pool, wf["id"], "a", "b")

        edges = await workflow_repo.get_edges(wf["id"])

        assert edges[0]["sourceHandle"] is None


# ── RunRepository ─────────────────────────────────────────────────────────────

class TestRunRepository:

    async def test_create_run_defaults_to_queued(self, pg_pool, workspace_seed, run_repo):
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id)
        node = await make_node(pg_pool, wf["id"], node_id="trigger-1", category="TRIGGER", type="MANUAL")

        run = await run_repo.create(wf["id"], trigger_node_id=node["id"], input_data={"key": "val"})

        assert run is not None
        assert run["status"] == "QUEUED"
        assert run["workflowId"] == wf["id"]
        assert run["triggerNodeId"] == node["id"]

    async def test_create_run_step_defaults_to_pending(self, pg_pool, workspace_seed, run_repo):
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id)
        run = await run_repo.create(wf["id"])

        step = await run_repo.create_run_step(run["id"], "node-1")

        assert step is not None
        assert step["status"] == "PENDING"
        assert step["workflowRunId"] == run["id"]
        assert step["nodeId"] == "node-1"

    async def test_update_run_step_partial_update_preserves_other_fields(self, pg_pool, workspace_seed, run_repo):
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id)
        run = await run_repo.create(wf["id"])
        step = await run_repo.create_run_step(run["id"], "node-partial")

        # Only update status — other fields should remain untouched
        updated = await run_repo.update_run_step(step["id"], status="RUNNING")

        assert updated["status"] == "RUNNING"
        assert updated["nodeId"] == "node-partial"
        assert updated["retryCount"] == 0

    async def test_update_run_step_jsonb_cast(self, pg_pool, workspace_seed, run_repo):
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id)
        run = await run_repo.create(wf["id"])
        step = await run_repo.create_run_step(run["id"], "node-json")

        output = {"result": "ok", "items": [1, 2, 3]}
        updated = await run_repo.update_run_step(
            step["id"],
            status="COMPLETED",
            output_data=output,
        )

        # outputData is stored as jsonb — should round-trip correctly
        assert updated["outputData"] is not None
        stored = updated["outputData"]
        if isinstance(stored, str):
            stored = json.loads(stored)
        assert stored["result"] == "ok"
        assert stored["items"] == [1, 2, 3]

    async def test_update_run_step_step_run_status_cast(self, pg_pool, workspace_seed, run_repo):
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id)
        run = await run_repo.create(wf["id"])
        step = await run_repo.create_run_step(run["id"], "node-status")

        for status in ("RUNNING", "COMPLETED", "FAILED", "SKIPPED"):
            updated = await run_repo.update_run_step(step["id"], status=status)
            assert updated["status"] == status

    async def test_update_status_coalesce_does_not_overwrite_existing_error(self, pg_pool, workspace_seed, run_repo):
        """COALESCE($3, errorMessage): once set, errorMessage stays unless explicitly updated."""
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id)
        run = await run_repo.create(wf["id"])

        await run_repo.update_status(run["id"], "FAILED", error_message="original error")
        # Second update with no error_message — COALESCE keeps original
        after = await run_repo.update_status(run["id"], "FAILED")

        assert after["errorMessage"] == "original error"

    async def test_get_run_steps_ordered_by_started_at_nulls_last(self, pg_pool, workspace_seed, run_repo):
        ws_id = workspace_seed["workspace_id"]
        wf = await make_workflow(pg_pool, ws_id)
        run = await run_repo.create(wf["id"])

        step_a = await run_repo.create_run_step(run["id"], "node-a")
        step_b = await run_repo.create_run_step(run["id"], "node-b")
        step_c = await run_repo.create_run_step(run["id"], "node-c")

        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await run_repo.update_run_step(step_b["id"], started_at=now)
        await run_repo.update_run_step(step_a["id"], started_at=now - timedelta(seconds=5))
        # step_c has no startedAt (NULL)

        steps = await run_repo.get_run_steps(run["id"])

        node_ids = [s["nodeId"] for s in steps]
        # node-a started 5 s ago, node-b started now, node-c NULL (last)
        assert node_ids.index("node-a") < node_ids.index("node-b")
        assert node_ids.index("node-c") == len(node_ids) - 1
