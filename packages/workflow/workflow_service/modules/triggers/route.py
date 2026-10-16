from fastapi import APIRouter, Depends

from workflow_service.core.auth import get_workspace_id, verify_internal_secret
from workflow_service.modules.triggers.models import (
    TriggerCreate,
    TriggerResponse,
    TriggerUpdate,
)
from workflow_service.modules.triggers.service import (
    create_trigger,
    delete_trigger,
    list_triggers,
    update_trigger,
)

router = APIRouter(prefix="/workflows/{workflow_id}/triggers", tags=["Triggers"])


@router.post("", response_model=TriggerResponse)
async def create_trigger_endpoint(
    workflow_id: str,
    body: TriggerCreate,
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
):
    return await create_trigger(workflow_id, workspace_id, body)


@router.get("", response_model=list[TriggerResponse])
async def list_triggers_endpoint(
    workflow_id: str,
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
):
    return await list_triggers(workflow_id, workspace_id)


@router.patch("/{trigger_id}", response_model=TriggerResponse)
async def update_trigger_endpoint(
    workflow_id: str,
    trigger_id: str,
    body: TriggerUpdate,
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
):
    return await update_trigger(workflow_id, workspace_id, trigger_id, body)


@router.delete("/{trigger_id}")
async def delete_trigger_endpoint(
    workflow_id: str,
    trigger_id: str,
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
):
    return await delete_trigger(workflow_id, workspace_id, trigger_id)
