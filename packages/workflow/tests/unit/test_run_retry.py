"""Run retry and queue failure behavior."""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from workflow_service.modules.runs import service


@pytest.fixture
def run_context(monkeypatch):
    runs = Mock()
    runs.get_by_id = AsyncMock()
    runs.create = AsyncMock()
    runs.update_status = AsyncMock()
    workflows = Mock()
    workflows.verify_ownership = AsyncMock(return_value=True)
    workflows.get_by_id = AsyncMock(return_value={"status": "ACTIVE"})
    monkeypatch.setattr(service, "_run_repo", runs)
    monkeypatch.setattr(service, "_workflow_repo", workflows)
    return runs, workflows


class FakeRedis:
    def __init__(self, error=None):
        self.error = error
        self.items = []

    async def lpush(self, key, value):
        if self.error:
            raise self.error
        self.items.append((key, value))


@pytest.mark.asyncio
async def test_retry_requeues_saved_input_as_a_new_run(monkeypatch, run_context):
    runs, _ = run_context
    runs.get_by_id.return_value = {
        "id": "failed-1",
        "workflowId": "workflow-1",
        "triggerNodeId": "manual",
        "inputData": {"customer": "Ada"},
        "status": "FAILED",
    }
    runs.create.return_value = {"id": "retry-2"}
    redis = FakeRedis()
    monkeypatch.setattr(service, "get_redis", lambda: redis)

    result = await service.retry_run("workflow-1", "workspace-1", "failed-1")

    assert result == {"runId": "retry-2", "status": "QUEUED", "retryOf": "failed-1"}
    assert runs.create.await_args.kwargs["input_data"] == {"customer": "Ada"}
    assert runs.create.await_args.kwargs["trigger_node_id"] == "manual"
    assert runs.create.await_args.kwargs["correlation_id"]
    assert len(redis.items) == 1
    assert '"workflowRunId": "retry-2"' in redis.items[0][1]


@pytest.mark.asyncio
async def test_retry_hides_runs_from_other_workflows(run_context):
    runs, _ = run_context
    runs.get_by_id.return_value = {
        "id": "foreign-run",
        "workflowId": "another-workflow",
        "status": "FAILED",
    }

    with pytest.raises(HTTPException) as error:
        await service.retry_run("workflow-1", "workspace-1", "foreign-run")

    assert error.value.status_code == 404
    runs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_rejects_missing_or_non_failed_run(run_context):
    runs, _ = run_context
    runs.get_by_id.return_value = None
    with pytest.raises(HTTPException) as missing:
        await service.retry_run("workflow-1", "workspace-1", "absent")
    assert missing.value.status_code == 404

    runs.get_by_id.return_value = {
        "id": "active-run",
        "workflowId": "workflow-1",
        "status": "RUNNING",
    }
    with pytest.raises(HTTPException) as active:
        await service.retry_run("workflow-1", "workspace-1", "active-run")
    assert active.value.status_code == 409
    runs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_requires_an_active_workflow(run_context):
    runs, workflows = run_context
    runs.get_by_id.return_value = {
        "id": "failed-1",
        "workflowId": "workflow-1",
        "status": "FAILED",
    }
    workflows.get_by_id.return_value = {"status": "INACTIVE"}

    with pytest.raises(HTTPException) as error:
        await service.retry_run("workflow-1", "workspace-1", "failed-1")

    assert error.value.status_code == 400
    runs.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_queue_failure_marks_new_run_failed(monkeypatch, run_context):
    runs, _ = run_context
    runs.get_by_id.return_value = {
        "id": "failed-1",
        "workflowId": "workflow-1",
        "inputData": {"safe": True},
        "status": "FAILED",
    }
    runs.create.return_value = {"id": "retry-2"}
    monkeypatch.setattr(service, "get_redis", lambda: FakeRedis(ConnectionError()))

    with pytest.raises(HTTPException) as error:
        await service.retry_run("workflow-1", "workspace-1", "failed-1")

    assert error.value.status_code == 503
    assert runs.update_status.await_args.args[:2] == ("retry-2", "FAILED")


@pytest.mark.asyncio
async def test_manual_trigger_does_not_claim_success_when_queue_is_down(
    monkeypatch, run_context
):
    runs, _ = run_context
    runs.create.return_value = {"id": "new-run"}
    monkeypatch.setattr(service, "get_redis", lambda: FakeRedis(ConnectionError()))
    request = service.ManualTriggerRequest(inputData={"hello": "world"})

    with pytest.raises(HTTPException) as error:
        await service.manual_trigger("workflow-1", "workspace-1", request)

    assert error.value.status_code == 503
    assert runs.update_status.await_args.args[:2] == ("new-run", "FAILED")
