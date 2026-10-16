from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.modules.ops.route import router as ops_router
from app.modules.ops.service import WarmupScheduleSnapshot


class FakeWarmupService:
    async def request_run(self, *, force: bool, source: str) -> WarmupScheduleSnapshot:
        assert source == "manual"
        return WarmupScheduleSnapshot(
            status="scheduled",
            forced=force,
            environment="staging",
            worker_pid=12345,
            next_eligible_at=datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc),
            last_started_at=None,
            last_finished_at=None,
            last_status="never",
            last_error=None,
        )


@pytest.mark.asyncio
async def test_post_ops_milvus_warmup_returns_202_and_state() -> None:
    app = FastAPI()
    app.state.milvus_warmup_service = FakeWarmupService()
    app.include_router(ops_router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/ops/milvus-warmup?force=true")

    assert response.status_code == 202
    assert response.json() == {
        "status": "scheduled",
        "forced": True,
        "environment": "staging",
        "worker_pid": 12345,
        "next_eligible_at": "2026-05-29T12:00:00Z",
        "last_started_at": None,
        "last_finished_at": None,
        "last_status": "never",
        "last_error": None,
    }
