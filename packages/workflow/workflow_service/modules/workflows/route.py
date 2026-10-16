from fastapi import APIRouter, Depends, Query

from workflow_service.core.auth import get_workspace_id, verify_internal_secret
from workflow_service.modules.workflows.models import (
    PaginatedResponse,
    WorkflowResponse,
)
from workflow_service.modules.workflows.service import (
    deactivate_workflow,
    get_workflow,
    list_workflows,
    publish_workflow,
)

router = APIRouter(prefix="/workflows", tags=["Workflows"])


@router.get("", response_model=PaginatedResponse)
async def list_workflows_endpoint(
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    return await list_workflows(workspace_id, status, page, limit)


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow_endpoint(
    workflow_id: str,
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
):
    return await get_workflow(workflow_id, workspace_id)


@router.patch("/{workflow_id}/publish", response_model=WorkflowResponse)
async def publish_workflow_endpoint(
    workflow_id: str,
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
):
    return await publish_workflow(workflow_id, workspace_id)


@router.patch("/{workflow_id}/deactivate", response_model=WorkflowResponse)
async def deactivate_workflow_endpoint(
    workflow_id: str,
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
):
    return await deactivate_workflow(workflow_id, workspace_id)
