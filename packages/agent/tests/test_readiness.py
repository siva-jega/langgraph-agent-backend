"""Readiness checks must stay bounded and avoid exposing dependency errors."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.ops.readiness import ReadinessService


def settings(**overrides):
    values = {
        "DATABASE_URL": None,
        "REDIS_URI": None,
        "MILVUS_URI": None,
        "CHECKPOINTER_URL": None,
        "POSTGRES_URL": None,
        "CHECKPOINT_TYPE": "memory",
        "READINESS_CACHE_SECONDS": 2.0,
        "READINESS_TIMEOUT_SECONDS": 0.1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def app_with_checkpointer(checkpointer=None):
    return SimpleNamespace(state=SimpleNamespace(checkpointer=checkpointer))


@pytest.mark.asyncio
async def test_unconfigured_services_are_skipped_after_startup():
    service = ReadinessService(settings())

    report = await service.snapshot(app_with_checkpointer(object()))

    assert report.status == "ready"
    assert report.http_status == 200
    assert report.checks["postgres"].status == "skipped"
    assert report.checks["redis"].status == "skipped"
    assert report.checks["milvus_client"].status == "skipped"


@pytest.mark.asyncio
async def test_request_before_startup_is_not_ready():
    service = ReadinessService(settings())

    report = await service.snapshot(app_with_checkpointer())

    assert report.status == "unavailable"
    assert report.http_status == 503
    assert report.checks["checkpointer"].status == "unavailable"


@pytest.mark.asyncio
async def test_configured_dependency_failure_is_redacted():
    service = ReadinessService(settings(REDIS_URI="redis://localhost"))
    service._redis = AsyncMock(side_effect=RuntimeError("private connection string"))

    report = await service.snapshot(app_with_checkpointer(object()))

    assert report.http_status == 503
    assert report.checks["redis"].status == "unavailable"
    assert "private" not in str(report.as_dict())


@pytest.mark.asyncio
async def test_probe_timeout_returns_unavailable():
    service = ReadinessService(settings(DATABASE_URL="postgres://localhost"))

    async def slow_postgres():
        await asyncio.sleep(1)
        return True

    service._postgres = slow_postgres
    report = await service.snapshot(app_with_checkpointer(object()))

    assert report.http_status == 503
    assert report.checks["postgres"].status == "unavailable"


@pytest.mark.asyncio
async def test_concurrent_requests_share_cached_checks():
    service = ReadinessService(settings(REDIS_URI="redis://localhost"))
    redis_probe = AsyncMock(return_value=True)
    service._redis = redis_probe
    app = app_with_checkpointer(object())

    first, second = await asyncio.gather(service.snapshot(app), service.snapshot(app))

    assert first is second
    redis_probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_configured_durable_checkpointer_cannot_fall_back_to_memory():
    from app.core.config import memory_saver

    service = ReadinessService(
        settings(CHECKPOINTER_URL="postgres://localhost", CHECKPOINT_TYPE="postgres")
    )

    report = await service.snapshot(app_with_checkpointer(memory_saver))

    assert report.http_status == 503
    assert report.checks["checkpointer"].status == "unavailable"
