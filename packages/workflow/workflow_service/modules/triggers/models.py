from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from workflow_service.modules.workflows.models import TriggerType


class TriggerCreate(BaseModel):
    type: TriggerType
    configJson: dict[str, Any] = Field(default_factory=dict)
    agentId: str | None = None
    filterJson: dict[str, Any] | None = None
    isActive: bool = True


class TriggerUpdate(BaseModel):
    configJson: dict[str, Any] | None = None
    agentId: str | None = None
    filterJson: dict[str, Any] | None = None
    isActive: bool | None = None


class TriggerResponse(BaseModel):
    id: str
    workflowDefinitionId: str
    type: TriggerType
    configJson: dict[str, Any]
    webhookUrl: str | None
    agentId: str | None
    filterJson: dict[str, Any] | None
    isActive: bool
    createdAt: datetime
