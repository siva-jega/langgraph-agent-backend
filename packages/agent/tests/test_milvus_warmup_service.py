from __future__ import annotations

import asyncio
import json

import pytest
from prometheus_client import REGISTRY

from app.core.config import AppSettings
from app.modules.ops import metrics as warmup_metrics
from app.modules.ops.service import (
    MilvusWarmupService,
)


def _settings(**overrides) -> AppSettings:
    base = {
        "ENVIRONMENT": "staging",
        "MILVUS_WARMUP_ENABLED": True,
        "MILVUS_WARMUP_QUERY": "black t-shirt",
        "MILVUS_WARMUP_TTL_SECONDS": 300,
        "MILVUS_WARMUP_SCOPES_JSON": json.dumps(
            {
                "staging": {
                    "association_id": "assoc-1",
                    "entity_ids": ["entity-1", "entity-2"],
                }
            }
        ),
    }
    base.update(overrides)
    return AppSettings(**base)


def _sample_value(name: str, labels: dict[str, str]) -> float | None:
    return REGISTRY.get_sample_value(name, labels=labels)


@pytest.mark.asyncio
async def test_milvus_warmup_disabled_when_flag_off() -> None:
    service = MilvusWarmupService(_settings(MILVUS_WARMUP_ENABLED=False))

    snapshot = await service.request_run(force=False, source="manual")

    assert snapshot.status == "disabled"
    assert snapshot.last_status == "never"


@pytest.mark.asyncio
async def test_milvus_warmup_misconfigured_when_json_invalid() -> None:
    service = MilvusWarmupService(
        _settings(MILVUS_WARMUP_SCOPES_JSON="{not-json")
    )

    snapshot = await service.request_run(force=False, source="manual")

    assert snapshot.status == "misconfigured"
    assert "invalid JSON" in (snapshot.last_error or "")


@pytest.mark.asyncio
async def test_milvus_warmup_already_running_then_throttled_then_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MilvusWarmupService(_settings())
    release = asyncio.Event()

    class FakeRetrieval:
        async def retrieve(self, **kwargs):
            await release.wait()
            return []

        async def retrieve_sparse(self, **kwargs):
            await release.wait()
            return []

    monkeypatch.setattr("app.modules.ops.service.get_retrieval", lambda: FakeRetrieval())

    first = await service.request_run(force=False, source="manual")
    second = await service.request_run(force=True, source="manual")

    assert first.status == "scheduled"
    assert second.status == "already_running"

    release.set()
    await service.wait_for_pending()

    throttled = await service.request_run(force=False, source="manual")
    forced = await service.request_run(force=True, source="manual")

    assert throttled.status == "throttled"
    assert forced.status == "scheduled"

    release = asyncio.Event()
    release.set()
    await service.wait_for_pending()


@pytest.mark.asyncio
async def test_milvus_warmup_records_partial_status_and_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MilvusWarmupService(_settings())

    dense_before = _sample_value(
        "ai_milvus_warmup_runs_total",
        {
            "environment": "staging",
            "warmup_type": warmup_metrics.WARMUP_TYPE_DENSE,
            "status": "success",
        },
    ) or 0.0
    sparse_before = _sample_value(
        "ai_milvus_warmup_runs_total",
        {
            "environment": "staging",
            "warmup_type": warmup_metrics.WARMUP_TYPE_SPARSE,
            "status": "error",
        },
    ) or 0.0
    total_before = _sample_value(
        "ai_milvus_warmup_runs_total",
        {
            "environment": "staging",
            "warmup_type": warmup_metrics.WARMUP_TYPE_TOTAL,
            "status": "partial",
        },
    ) or 0.0

    class FakeRetrieval:
        async def retrieve(self, **kwargs):
            return []

        async def retrieve_sparse(self, **kwargs):
            raise RuntimeError("sparse boom")

    monkeypatch.setattr("app.modules.ops.service.get_retrieval", lambda: FakeRetrieval())

    snapshot = await service.request_run(force=True, source="manual")
    assert snapshot.status == "scheduled"
    await service.wait_for_pending()

    post = service.current_snapshot(status="noop", forced=False)
    assert post.last_status == "partial"
    assert "sparse boom" in (post.last_error or "")

    assert (
        _sample_value(
            "ai_milvus_warmup_runs_total",
            {
                "environment": "staging",
                "warmup_type": warmup_metrics.WARMUP_TYPE_DENSE,
                "status": "success",
            },
        )
        or 0.0
    ) == dense_before + 1.0
    assert (
        _sample_value(
            "ai_milvus_warmup_runs_total",
            {
                "environment": "staging",
                "warmup_type": warmup_metrics.WARMUP_TYPE_SPARSE,
                "status": "error",
            },
        )
        or 0.0
    ) == sparse_before + 1.0
    assert (
        _sample_value(
            "ai_milvus_warmup_runs_total",
            {
                "environment": "staging",
                "warmup_type": warmup_metrics.WARMUP_TYPE_TOTAL,
                "status": "partial",
            },
        )
        or 0.0
    ) == total_before + 1.0

    last_status = _sample_value(
        "ai_milvus_warmup_last_status",
        {
            "environment": "staging",
            "warmup_type": warmup_metrics.WARMUP_TYPE_TOTAL,
        },
    )
    assert last_status == 0.5


@pytest.mark.asyncio
async def test_scheduler_loop_runs_startup_then_periodic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MilvusWarmupService(_settings(MILVUS_WARMUP_TTL_SECONDS=1))
    calls: list[tuple[bool, str]] = []
    sleep_calls = {"count": 0}

    async def fake_request_run(*, force: bool, source: str):
        calls.append((force, source))
        return service.current_snapshot(status="scheduled", forced=force)

    async def fake_sleep(delay: float) -> None:
        sleep_calls["count"] += 1
        if sleep_calls["count"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(service, "request_run", fake_request_run)
    monkeypatch.setattr("app.modules.ops.service.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await service.run_scheduler_loop()

    assert calls == [(True, "startup"), (True, "periodic")]
