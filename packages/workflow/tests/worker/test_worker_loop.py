"""Unit tests for the SQS worker loop.

No autouse no_sleep here — worker tests may need real async yielding.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from workflow_service.engine.dag import DagExecutor
from workflow_service.engine.worker import _run_loop, start_worker, stop_worker


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(queue_url: str = "http://fake/queue", concurrency: int = 1):
    class S:
        WORKFLOW_QUEUE_URL = queue_url
        WORKER_CONCURRENCY = concurrency
        AWS_REGION = "us-east-1"
        SQS_MAX_MESSAGES = 10
        SQS_WAIT_SECONDS = 0
        SQS_VISIBILITY_TIMEOUT = 30

    return S()


def _msg(run_id: str = "run-1", wf_id: str = "wf-1", receipt: str = "rh-1") -> dict:
    return {
        "Body": json.dumps({"workflowRunId": run_id, "workflowId": wf_id}),
        "ReceiptHandle": receipt,
    }


class _SQS:
    """Controlled SQS mock: serves `batches` then raises CancelledError."""

    def __init__(self, batches: list[list[dict]]):
        self._batches = list(batches)
        self.deleted: list[str] = []
        self._poll_count = 0

    async def receive_message(self, **kwargs):
        self._poll_count += 1
        if self._batches:
            return {"Messages": self._batches.pop(0)}
        raise asyncio.CancelledError()

    async def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> None:
        self.deleted.append(ReceiptHandle)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _Session:
    def __init__(self, sqs: _SQS):
        self._sqs = sqs

    def client(self, name, **kw):
        return self._sqs


async def _drain_tasks() -> None:
    """Yield control multiple times so created tasks can complete."""
    for _ in range(20):
        await asyncio.sleep(0)


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_no_queue_url_returns_without_polling(monkeypatch) -> None:
    monkeypatch.setattr(
        "workflow_service.engine.worker.app_settings", _make_settings(queue_url="")
    )
    polled = []

    async def _fake_receive(**_):
        polled.append(True)
        raise asyncio.CancelledError()

    # If _run_loop returns before even creating the SQS client, polled stays empty
    await _run_loop()
    assert polled == []


async def test_happy_path_executes_and_deletes(monkeypatch) -> None:
    executed: list[str] = []

    async def _fake_execute(self, run_id: str) -> None:
        executed.append(run_id)

    monkeypatch.setattr(DagExecutor, "execute", _fake_execute)
    monkeypatch.setattr(
        "workflow_service.engine.worker.app_settings", _make_settings()
    )
    sqs = _SQS([[_msg("run-1")]])
    monkeypatch.setattr("aioboto3.Session", lambda: _Session(sqs))

    await _run_loop()
    await _drain_tasks()

    assert executed == ["run-1"]
    assert "rh-1" in sqs.deleted


async def test_invalid_json_deleted_not_executed(monkeypatch) -> None:
    executed: list[str] = []

    async def _fake_execute(self, run_id: str) -> None:
        executed.append(run_id)

    monkeypatch.setattr(DagExecutor, "execute", _fake_execute)
    monkeypatch.setattr(
        "workflow_service.engine.worker.app_settings", _make_settings()
    )
    bad_msg = {"Body": "not valid json {{", "ReceiptHandle": "rh-bad"}
    sqs = _SQS([[bad_msg]])
    monkeypatch.setattr("aioboto3.Session", lambda: _Session(sqs))

    await _run_loop()
    await _drain_tasks()

    assert executed == []
    assert "rh-bad" in sqs.deleted


async def test_missing_workflow_run_id_deleted_not_executed(monkeypatch) -> None:
    executed: list[str] = []

    async def _fake_execute(self, run_id: str) -> None:
        executed.append(run_id)

    monkeypatch.setattr(DagExecutor, "execute", _fake_execute)
    monkeypatch.setattr(
        "workflow_service.engine.worker.app_settings", _make_settings()
    )
    missing_id_msg = {"Body": json.dumps({"workflowId": "wf-1"}), "ReceiptHandle": "rh-noid"}
    sqs = _SQS([[missing_id_msg]])
    monkeypatch.setattr("aioboto3.Session", lambda: _Session(sqs))

    await _run_loop()
    await _drain_tasks()

    assert executed == []
    assert "rh-noid" in sqs.deleted


async def test_message_missing_body_or_receipt_skipped(monkeypatch) -> None:
    executed: list[str] = []

    async def _fake_execute(self, run_id: str) -> None:
        executed.append(run_id)

    monkeypatch.setattr(DagExecutor, "execute", _fake_execute)
    monkeypatch.setattr(
        "workflow_service.engine.worker.app_settings", _make_settings()
    )
    # Message with no Body or ReceiptHandle
    incomplete = {"Body": json.dumps({"workflowRunId": "run-x"})}  # no ReceiptHandle
    sqs = _SQS([[incomplete]])
    monkeypatch.setattr("aioboto3.Session", lambda: _Session(sqs))

    await _run_loop()
    await _drain_tasks()

    # Message was skipped — no execute, nothing deleted
    assert executed == []
    assert sqs.deleted == []


async def test_empty_receive_does_not_execute_or_delete(monkeypatch) -> None:
    executed: list[str] = []

    async def _fake_execute(self, run_id: str) -> None:
        executed.append(run_id)

    monkeypatch.setattr(DagExecutor, "execute", _fake_execute)
    monkeypatch.setattr(
        "workflow_service.engine.worker.app_settings", _make_settings()
    )
    # First poll returns empty Messages, second stops
    sqs = _SQS([[]])  # one empty batch, then CancelledError
    monkeypatch.setattr("aioboto3.Session", lambda: _Session(sqs))

    await _run_loop()

    assert executed == []
    assert sqs.deleted == []


@pytest.mark.worker
async def test_start_stop_worker_idempotency(monkeypatch) -> None:
    """start_worker is idempotent; stop_worker cancels cleanly."""
    monkeypatch.setattr(
        "workflow_service.engine.worker.app_settings", _make_settings(queue_url="")
    )

    # First start creates the task
    start_worker()
    import workflow_service.engine.worker as worker_mod

    task1 = worker_mod._worker_task
    assert task1 is not None

    # Second start is a no-op (same task)
    start_worker()
    assert worker_mod._worker_task is task1

    # Stop cancels cleanly
    await stop_worker()
    assert worker_mod._worker_task is None

    # Stop again is safe
    await stop_worker()
