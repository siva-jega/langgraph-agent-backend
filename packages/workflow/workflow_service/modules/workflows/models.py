from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class NodeCategory(str, Enum):
    TRIGGER = "TRIGGER"
    ACTION = "ACTION"
    CONDITION = "CONDITION"
    CONNECTOR = "CONNECTOR"


class ErrorPolicy(str, Enum):
    FAIL = "FAIL"
    SKIP = "SKIP"
    RETRY = "RETRY"


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StepRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# ── Response Models ────────────────────────────────────────────────────────────


class NodeResponse(BaseModel):
    id: str
    nodeId: str
    type: str
    category: NodeCategory
    label: str
    config: dict[str, Any]
    inputParameters: list[Any]
    outputParameters: list[Any]
    positionX: float
    positionY: float
    errorPolicy: ErrorPolicy
    createdAt: datetime


class EdgeResponse(BaseModel):
    id: str
    sourceNodeId: str
    targetNodeId: str
    sourceHandle: str | None


class WorkflowResponse(BaseModel):
    id: str
    workspaceId: str
    name: str
    description: str | None
    status: WorkflowStatus
    version: int
    nodes: list[NodeResponse] = Field(default_factory=list)
    edges: list[EdgeResponse] = Field(default_factory=list)
    createdAt: datetime
    updatedAt: datetime


class WorkflowListItem(BaseModel):
    id: str
    workspaceId: str
    name: str
    description: str | None
    status: WorkflowStatus
    version: int
    createdAt: datetime
    updatedAt: datetime


class PaginatedResponse(BaseModel):
    data: list[Any]
    total: int
    page: int
    limit: int
