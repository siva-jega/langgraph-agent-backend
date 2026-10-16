from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from workflow_service.modules.workflows.models import RunStatus, StepRunStatus


class ManualTriggerRequest(BaseModel):
    inputData: dict[str, Any] | None = Field(default=None)


class RunNodeResponse(BaseModel):
    id: str
    nodeId: str
    status: StepRunStatus
    inputData: dict[str, Any] | None
    outputData: dict[str, Any] | None
    errorMessage: str | None
    retryCount: int
    startedAt: datetime | None
    completedAt: datetime | None


class RunResponse(BaseModel):
    id: str
    workflowId: str
    triggerNodeId: str | None
    status: RunStatus
    correlationId: str | None
    inputData: dict[str, Any] | None
    errorMessage: str | None
    startedAt: datetime | None
    completedAt: datetime | None
    createdAt: datetime


class RunDetailResponse(RunResponse):
    nodes: list[RunNodeResponse] = Field(default_factory=list)
