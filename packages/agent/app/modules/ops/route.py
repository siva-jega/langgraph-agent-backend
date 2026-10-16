"""Operations routes."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel

from app import otel_utils
from app.modules.ops.service import MilvusWarmupService, WarmupScheduleSnapshot

router = APIRouter(prefix="/ops", tags=["Ops"])


class MilvusWarmupResponse(BaseModel):
    status: str
    forced: bool
    environment: str
    worker_pid: int
    next_eligible_at: datetime | None
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_status: str
    last_error: str | None

    @classmethod
    def from_snapshot(cls, snapshot: WarmupScheduleSnapshot) -> "MilvusWarmupResponse":
        return cls(
            status=snapshot.status,
            forced=snapshot.forced,
            environment=snapshot.environment,
            worker_pid=snapshot.worker_pid,
            next_eligible_at=snapshot.next_eligible_at,
            last_started_at=snapshot.last_started_at,
            last_finished_at=snapshot.last_finished_at,
            last_status=snapshot.last_status,
            last_error=snapshot.last_error,
        )


def _get_service(request: Request) -> MilvusWarmupService:
    return request.app.state.milvus_warmup_service


@router.post(
    "/milvus-warmup",
    include_in_schema=False,
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MilvusWarmupResponse,
)
@otel_utils.no_trace
async def trigger_milvus_warmup(
    request: Request,
    force: bool = Query(default=False),
) -> MilvusWarmupResponse:
    service = _get_service(request)
    snapshot = await service.request_run(force=force, source="manual")
    return MilvusWarmupResponse.from_snapshot(snapshot)
