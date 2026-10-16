"""Unit tests for DagExecutor branching and error-policy routing."""

from __future__ import annotations

import pytest

from workflow_service.engine.dag import DagExecutor
from workflow_service.testing import FakeRunRepo, FakeWorkflowRepo


# ── Test-data builders (duplicated from test_dag_graph_logic for isolation) ──


def make_run(input_data=None) -> dict:
    return {"id": "run-1", "workflowId": "wf-1", "inputData": input_data or {}}


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


def _patch_dag(monkeypatch, run_repo: FakeRunRepo, workflow_repo: FakeWorkflowRepo, executors: dict):
    import workflow_service.engine.dag as dag_mod

    monkeypatch.setattr(dag_mod, "_run_repo", run_repo)
    monkeypatch.setattr(dag_mod, "_workflow_repo", workflow_repo)
    monkeypatch.setattr(dag_mod, "STEP_EXECUTORS", executors)
    return DagExecutor()


# ── Branch routing tests ──────────────────────────────────────────────────────


async def test_condition_true_branch_taken(monkeypatch) -> None:
    class _True:
        async def execute(self, config, context):
            return {"result": True}

    nodes = [make_node("t"), make_node("cond", "CONDITION"), make_node("yes", "ACT"), make_node("no", "ACT")]
    edges = [
        make_edge("t", "cond"),
        make_edge("cond", "yes", "true"),
        make_edge("cond", "no", "false"),
    ]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "CONDITION": _True, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert "yes" in run_repo._nodes_created
    assert run_repo.final_step_status("no") == "SKIPPED"
    assert run_repo.status_history[-1] == "COMPLETED"


async def test_condition_false_branch_taken(monkeypatch) -> None:
    class _False:
        async def execute(self, config, context):
            return {"result": False}

    nodes = [make_node("t"), make_node("cond", "CONDITION"), make_node("yes", "ACT"), make_node("no", "ACT")]
    edges = [
        make_edge("t", "cond"),
        make_edge("cond", "yes", "true"),
        make_edge("cond", "no", "false"),
    ]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "CONDITION": _False, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert "no" in run_repo._nodes_created
    assert run_repo.final_step_status("yes") == "SKIPPED"


async def test_skip_propagates_to_merge_but_merge_still_runs_once(monkeypatch) -> None:
    """Skipped branch still decrements merge in_degree; merge runs exactly once."""

    class _True:
        async def execute(self, config, context):
            return {"result": True}

    nodes = [
        make_node("t"),
        make_node("cond", "CONDITION"),
        make_node("yes", "ACT"),
        make_node("no", "ACT"),
        make_node("merge", "ACT"),
    ]
    edges = [
        make_edge("t", "cond"),
        make_edge("cond", "yes", "true"),
        make_edge("cond", "no", "false"),
        make_edge("yes", "merge"),
        make_edge("no", "merge"),
    ]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "CONDITION": _True, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert run_repo._nodes_created.count("merge") == 1
    assert run_repo.final_step_status("no") == "SKIPPED"
    assert run_repo.status_history[-1] == "COMPLETED"


async def test_condition_default_true_when_result_key_missing(monkeypatch) -> None:
    """output.get('result', 'true') defaults to 'true' when key absent."""

    class _NoResultKey:
        async def execute(self, config, context):
            return {"something": "else"}  # truthy output, no 'result' key

    nodes = [make_node("t"), make_node("cond", "CONDITION"), make_node("yes", "ACT"), make_node("no", "ACT")]
    edges = [
        make_edge("t", "cond"),
        make_edge("cond", "yes", "true"),
        make_edge("cond", "no", "false"),
    ]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "CONDITION": _NoResultKey, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert "yes" in run_repo._nodes_created
    assert run_repo.final_step_status("no") == "SKIPPED"


async def test_connector_branching_via_branch_output(monkeypatch) -> None:
    class _BranchConnector:
        async def execute(self, config, context):
            return {"branch": "yes"}

    nodes = [
        make_node("t"),
        make_node("conn", "CONNECTOR"),
        make_node("yes_node", "ACT"),
        make_node("no_node", "ACT"),
    ]
    edges = [
        make_edge("t", "conn"),
        make_edge("conn", "yes_node", "yes"),
        make_edge("conn", "no_node", "no"),
    ]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "CONNECTOR": _BranchConnector, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert "yes_node" in run_repo._nodes_created
    assert run_repo.final_step_status("no_node") == "SKIPPED"


# ── Error policy routing tests ────────────────────────────────────────────────


async def test_retry_then_succeed_records_correct_delays(monkeypatch, no_sleep) -> None:
    attempt = 0

    class _FailTwiceThenSucceed:
        async def execute(self, config, context):
            nonlocal attempt
            attempt += 1
            if attempt <= 2:
                raise RuntimeError(f"attempt {attempt}")
            return {"result": "ok"}

    nodes = [make_node("t"), make_node("r", "RETRY_NODE", error_policy="RETRY")]
    edges = [make_edge("t", "r")]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "RETRY_NODE": _FailTwiceThenSucceed},
    )

    await executor.execute("run-1")

    assert run_repo.status_history[-1] == "COMPLETED"
    assert no_sleep == [1.0, 2.0]  # min(2^0, 60) and min(2^1, 60)
    assert run_repo.retry_count("r") == 2


async def test_retry_exhausted_fallback_skip_run_completes(monkeypatch, no_sleep) -> None:
    class _AlwaysFail:
        async def execute(self, config, context):
            raise RuntimeError("always")

    params = [
        {"key": "maxRetries", "value": 2},
        {"key": "fallbackPolicy", "value": "SKIP"},
    ]
    nodes = [
        make_node("t"),
        make_node("r", "FAIL_NODE", params=params, error_policy="RETRY"),
        make_node("after", "ACT"),
    ]
    edges = [make_edge("t", "r"), make_edge("r", "after")]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "FAIL_NODE": _AlwaysFail, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert run_repo.status_history[-1] == "COMPLETED"
    assert run_repo.final_step_status("r") == "SKIPPED"
    assert "after" in run_repo._nodes_created


async def test_retry_exhausted_fallback_fail_run_fails(monkeypatch, no_sleep) -> None:
    class _AlwaysFail:
        async def execute(self, config, context):
            raise RuntimeError("always")

    params = [{"key": "maxRetries", "value": 1}]  # fallbackPolicy defaults to FAIL
    nodes = [
        make_node("t"),
        make_node("r", "FAIL_NODE", params=params, error_policy="RETRY"),
        make_node("after", "ACT"),
    ]
    edges = [make_edge("t", "r"), make_edge("r", "after")]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "FAIL_NODE": _AlwaysFail, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert run_repo.status_history[-1] == "FAILED"
    assert "after" not in run_repo._nodes_created


async def test_skip_policy_downstream_still_runs(monkeypatch) -> None:
    class _Fail:
        async def execute(self, config, context):
            raise RuntimeError("fail")

    nodes = [
        make_node("t"),
        make_node("s", "FAIL_NODE", error_policy="SKIP"),
        make_node("after", "ACT"),
    ]
    edges = [make_edge("t", "s"), make_edge("s", "after")]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "FAIL_NODE": _Fail, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert "after" in run_repo._nodes_created
    assert run_repo.final_step_status("s") == "SKIPPED"
    assert run_repo.status_history[-1] == "COMPLETED"


async def test_fail_policy_halts_downstream(monkeypatch) -> None:
    class _Fail:
        async def execute(self, config, context):
            raise RuntimeError("fail")

    nodes = [
        make_node("t"),
        make_node("f", "FAIL_NODE", error_policy="FAIL"),
        make_node("after", "ACT"),
    ]
    edges = [make_edge("t", "f"), make_edge("f", "after")]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "FAIL_NODE": _Fail, "ACT": _OkExecutor},
    )

    await executor.execute("run-1")

    assert "after" not in run_repo._nodes_created
    assert run_repo.status_history[-1] == "FAILED"


async def test_skipped_node_output_available_for_downstream_expressions(monkeypatch) -> None:
    """A node skipped by SKIP policy stores empty output so downstream can reference it."""
    captured: list[dict] = []

    class _Fail:
        async def execute(self, config, context):
            raise RuntimeError("fail")

    class _CaptureCtx:
        async def execute(self, config, context):
            captured.append(dict(context))
            return {}

    nodes = [
        make_node("t"),
        make_node("s", "FAIL_NODE", error_policy="SKIP"),
        make_node("after", "CAPTURE"),
    ]
    edges = [make_edge("t", "s"), make_edge("s", "after")]
    run_repo = FakeRunRepo(run=make_run())
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)
    executor = _patch_dag(
        monkeypatch, run_repo, workflow_repo,
        {"WEBHOOK": _OkExecutor, "FAIL_NODE": _Fail, "CAPTURE": _CaptureCtx},
    )

    await executor.execute("run-1")

    assert len(captured) == 1
    # "s" is in context even though it was skipped
    assert "s" in captured[0]
    assert captured[0]["s"] == {"output": {}}
