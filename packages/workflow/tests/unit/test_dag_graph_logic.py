"""Unit tests for DagExecutor graph traversal logic — no DB / I/O."""

from __future__ import annotations

import pytest

from workflow_service.engine.dag import DagExecutor
from workflow_service.engine.nodes.identity_trigger import IdentityTriggerExecutor
from workflow_service.testing import FakeRunRepo, FakeWorkflowRepo


# ── Test-data builders ────────────────────────────────────────────────────────


def make_run(workflow_id: str = "wf-1", input_data=None) -> dict:
    return {"id": "run-1", "workflowId": workflow_id, "inputData": input_data or {}}


def make_node(
    node_id: str,
    node_type: str = "WEBHOOK",
    params: list | None = None,
    error_policy: str = "FAIL",
) -> dict:
    return {
        "nodeId": node_id,
        "type": node_type,
        "label": node_id,
        "inputParameters": params or [],
        "errorPolicy": error_policy,
    }


def make_edge(src: str, tgt: str, handle: str | None = None) -> dict:
    return {"sourceNodeId": src, "targetNodeId": tgt, "sourceHandle": handle}


class _OkExecutor:
    async def execute(self, config: dict, context: dict) -> dict:
        return {"ok": True}


# ── Fixture helpers ───────────────────────────────────────────────────────────


def _patch_dag(monkeypatch, run_repo: FakeRunRepo, workflow_repo: FakeWorkflowRepo, executors: dict):
    import workflow_service.engine.dag as dag_mod

    monkeypatch.setattr(dag_mod, "_run_repo", run_repo)
    monkeypatch.setattr(dag_mod, "_workflow_repo", workflow_repo)
    monkeypatch.setattr(dag_mod, "STEP_EXECUTORS", executors)
    return DagExecutor()


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_run_not_found_is_noop(monkeypatch) -> None:
    run_repo = FakeRunRepo(run=None)
    workflow_repo = FakeWorkflowRepo()
    executor = _patch_dag(monkeypatch, run_repo, workflow_repo, {})

    await executor.execute("nonexistent")

    assert run_repo.status_history == []
    assert run_repo._nodes_created == []


async def test_empty_workflow_completes(monkeypatch) -> None:
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=[], edges=[])
    executor = _patch_dag(monkeypatch, run_repo, workflow_repo, {})

    await executor.execute("run-1")

    assert run_repo.status_history[-1] == "COMPLETED"
    assert run_repo._nodes_created == []


async def test_status_timeline_running_then_completed(monkeypatch) -> None:
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=[make_node("t")], edges=[])
    executor = _patch_dag(monkeypatch, run_repo, workflow_repo, {"WEBHOOK": _OkExecutor})

    await executor.execute("run-1")

    assert run_repo.status_history[0] == "RUNNING"
    assert run_repo.status_history[-1] == "COMPLETED"


async def test_linear_chain_all_nodes_run(monkeypatch) -> None:
    nodes = [make_node("t"), make_node("a", "ACT"), make_node("b", "ACT")]
    edges = [make_edge("t", "a"), make_edge("a", "b")]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert set(run_repo._nodes_created) == {"t", "a", "b"}
    assert run_repo.status_history[-1] == "COMPLETED"


async def test_diamond_join_merge_node_runs_exactly_once(monkeypatch) -> None:
    """Two paths converge on a merge node; merge must execute only once."""
    nodes = [
        make_node("t"),
        make_node("a", "ACT"),
        make_node("b", "ACT"),
        make_node("merge", "ACT"),
    ]
    edges = [
        make_edge("t", "a"),
        make_edge("t", "b"),
        make_edge("a", "merge"),
        make_edge("b", "merge"),
    ]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert run_repo._nodes_created.count("merge") == 1
    assert run_repo.status_history[-1] == "COMPLETED"


async def test_unsupported_node_type_marked_skipped_and_downstream_runs(monkeypatch) -> None:
    nodes = [make_node("t"), make_node("u", "UNKNOWN"), make_node("after", "ACT")]
    edges = [make_edge("t", "u"), make_edge("u", "after")]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    # UNKNOWN is NOT in executors → falls through to the "no executor" SKIPPED path
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert run_repo.final_step_status("u") == "SKIPPED"
    assert "after" in run_repo._nodes_created
    assert run_repo.status_history[-1] == "COMPLETED"


async def test_connector_workspace_id_injected_from_workflow(monkeypatch) -> None:
    received_configs: list[dict] = []

    class _CaptureConnector:
        async def execute(self, config: dict, context: dict) -> dict:
            received_configs.append(dict(config))
            return {}

    nodes = [make_node("t"), make_node("conn", "CONNECTOR")]
    edges = [make_edge("t", "conn")]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(
        workflow={"workspaceId": "ws-injected"},
        nodes=nodes,
        edges=edges,
    )
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "CONNECTOR": _CaptureConnector},
    )

    await executor.execute("run-1")

    assert len(received_configs) == 1
    assert received_configs[0]["workspaceId"] == "ws-injected"


async def test_input_data_json_string_is_parsed(monkeypatch) -> None:
    """run.inputData stored as a JSON string must be parsed before execution."""
    run_repo = FakeRunRepo(run=make_run(input_data='{"orderId": "ORD-99"}'))
    workflow_repo = FakeWorkflowRepo(nodes=[make_node("t", "WEBHOOK")], edges=[])
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": IdentityTriggerExecutor},
    )

    await executor.execute("run-1")

    # IdentityTrigger reads context["__run__"]["inputData"] and returns it
    assert run_repo.step_output("t") == {"orderId": "ORD-99"}
