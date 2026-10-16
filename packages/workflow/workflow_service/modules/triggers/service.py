import logging

from fastapi import HTTPException

from workflow_service.modules.triggers.models import (
    TriggerCreate,
    TriggerResponse,
    TriggerUpdate,
)
from workflow_service.modules.triggers.repository import TriggerRepository
from workflow_service.modules.workflows.repository import WorkflowRepository

logger = logging.getLogger(__name__)

_trigger_repo = TriggerRepository()
_workflow_repo = WorkflowRepository()


def _to_str(val) -> str:
    return str(val) if val is not None else ""


def _to_response(row: dict) -> TriggerResponse:
    return TriggerResponse(
        id=_to_str(row["id"]),
        workflowDefinitionId=_to_str(row["workflowDefinitionId"]),
        type=row["type"],
        configJson=row.get("configJson") or {},
        webhookUrl=row.get("webhookUrl"),
        agentId=_to_str(row["agentId"]) if row.get("agentId") else None,
        filterJson=row.get("filterJson"),
        isActive=row["isActive"],
        createdAt=row["createdAt"],
    )


async def _verify_workflow(workflow_id: str, workspace_id: str) -> None:
    if not await _workflow_repo.verify_ownership(workflow_id, workspace_id):
        raise HTTPException(status_code=404, detail="Workflow not found")


async def create_trigger(
    workflow_id: str, workspace_id: str, data: TriggerCreate
) -> TriggerResponse:
    await _verify_workflow(workflow_id, workspace_id)
    row = await _trigger_repo.create(
        workflow_def_id=workflow_id,
        trigger_type=data.type.value,
        config_json=data.configJson,
        agent_id=data.agentId,
        filter_json=data.filterJson,
        is_active=data.isActive,
    )
    if not row:
        raise HTTPException(status_code=500, detail="Failed to create trigger")
    return _to_response(row)


async def list_triggers(
    workflow_id: str, workspace_id: str
) -> list[TriggerResponse]:
    await _verify_workflow(workflow_id, workspace_id)
    rows = await _trigger_repo.list_by_workflow(workflow_id)
    return [_to_response(r) for r in rows]


async def update_trigger(
    workflow_id: str, workspace_id: str, trigger_id: str, data: TriggerUpdate
) -> TriggerResponse:
    await _verify_workflow(workflow_id, workspace_id)

    fields = {}
    if data.configJson is not None:
        fields["configJson"] = data.configJson
    if data.agentId is not None:
        fields["agentId"] = data.agentId
    if data.filterJson is not None:
        fields["filterJson"] = data.filterJson
    if data.isActive is not None:
        fields["isActive"] = data.isActive

    row = await _trigger_repo.update(workflow_id, trigger_id, fields)
    if not row:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return _to_response(row)


async def delete_trigger(
    workflow_id: str, workspace_id: str, trigger_id: str
) -> dict:
    await _verify_workflow(workflow_id, workspace_id)
    deleted = await _trigger_repo.delete(workflow_id, trigger_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return {"success": True}
