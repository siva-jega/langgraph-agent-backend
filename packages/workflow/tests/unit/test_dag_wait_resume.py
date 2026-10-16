"""Tests for wait-node pause/resume flow in DagExecutor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from workflow_service.engine.dag import DagExecutor
from workflow_service.engine.nodes.wait import PauseRequired
from workflow_service.testing import FakeRunRepo, FakeWorkflowRepo


def make_node(node_id: str, node_type: str = "WEBHOOK", params: list | None = None) -> dict:
    return {
        "nodeId": node_id,
        "type": node_type,
        "label": node_id,
        "inputParameters": params or [],
        "errorPolicy": "FAIL",
    }


def make_edge(src: str, tgt: str) -> dict:
    return {"sourceNodeId": src, "targetNodeId": tgt, "sourceHandle": None}


class _OkExecutor:
    async def execute(self, config, context):
        return {"ok": True}


class _LongWaitExecutor:
    async def execute(self, config, context):
        raise PauseRequired(resume_at=datetime.now(timezone.utc) + timedelta(hours=1))


def _setup(monkeypatch, run_repo, workflow_repo, executors):
    import workflow_service.engine.dag as dag_mod
    monkeypatch.setattr(dag_mod, "_run_repo", run_repo)
    monkeypatch.setattr(dag_mod, "_workflow_repo", workflow_repo)
    monkeypatch.setattr(dag_mod, "STEP_EXECUTORS", executors)
    return DagExecutor()


async def test_wait_node_pauses_run_and_schedules_temporal(monkeypatch):
    nodes = [make_node("trigger"), make_node("wait", "WAIT"), make_node("action")]
    edges = [make_edge("trigger", "wait"), make_edge("wait", "action")]

    run_repo = FakeRunRepo(run={"id": "run-1", "workflowId": "wf-1", "status": "PENDING", "inputData": {}})
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)

    mock_schedule = AsyncMock()
    monkeypatch.setattr("workflow_service.engine.dag._schedule_resume", mock_schedule)

    executor = _setup(monkeypatch, run_repo, workflow_repo, {
        "WEBHOOK": _OkExecutor,
        "WAIT": _LongWaitExecutor,
        "ACT": _OkExecutor,
    })

    await executor.execute("run-1")

    assert "PAUSED" in run_repo.status_history
    assert mock_schedule.called
    assert "action" not in run_repo._nodes_created


async def test_wait_node_resume_completes_wait_and_continues(monkeypatch):
    nodes = [make_node("trigger"), make_node("wait", "WAIT"), make_node("action")]
    edges = [make_edge("trigger", "wait"), make_edge("wait", "action")]

    preloaded = [
        {"id": "step-trigger", "nodeId": "trigger", "status": "COMPLETED", "outputData": {"ok": True}},
        {"id": "step-wait", "nodeId": "wait", "status": "RUNNING", "outputData": None},
    ]
    run_repo = FakeRunRepo(
        run={"id": "run-1", "workflowId": "wf-1", "status": "PAUSED", "inputData": {}},
        preloaded_steps=preloaded,
    )
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)

    executor = _setup(monkeypatch, run_repo, workflow_repo, {
        "WEBHOOK": _OkExecutor,
        "WAIT": _LongWaitExecutor,
        "ACT": _OkExecutor,
    })

    await executor.execute("run-1")

    assert run_repo.status_history[-1] == "COMPLETED"
    assert run_repo.final_step_status("wait") == "COMPLETED"
    assert "action" in run_repo._nodes_created
    assert run_repo.final_step_status("action") == "COMPLETED"


async def test_wait_node_resume_does_not_re_pause(monkeypatch):
    nodes = [make_node("wait", "WAIT")]
    edges = []

    preloaded = [
        {"id": "step-wait", "nodeId": "wait", "status": "RUNNING", "outputData": None},
    ]
    run_repo = FakeRunRepo(
        run={"id": "run-1", "workflowId": "wf-1", "status": "PAUSED", "inputData": {}},
        preloaded_steps=preloaded,
    )
    workflow_repo = FakeWorkflowRepo(nodes=nodes, edges=edges)

    mock_schedule = AsyncMock()
    monkeypatch.setattr("workflow_service.engine.dag._schedule_resume", mock_schedule)

    executor = _setup(monkeypatch, run_repo, workflow_repo, {"WAIT": _LongWaitExecutor})

    await executor.execute("run-1")

    assert not mock_schedule.called
    assert run_repo.status_history[-1] == "COMPLETED"
    assert run_repo.final_step_status("wait") == "COMPLETED"
