from fastapi import APIRouter, Depends, Query

from workflow_service.core.auth import get_workspace_id, verify_internal_secret
from workflow_service.modules.runs.models import (
    ManualTriggerRequest,
    RunDetailResponse,
)
from workflow_service.modules.runs.service import (
    get_run,
    list_runs,
    manual_trigger,
    retry_run,
)
from workflow_service.modules.workflows.models import PaginatedResponse

router = APIRouter(prefix="/workflows/{workflow_id}", tags=["Runs"])


@router.get("/runs", response_model=PaginatedResponse)
async def list_runs_endpoint(
    workflow_id: str,
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    return await list_runs(workflow_id, workspace_id, status, page, limit)


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
async def get_run_endpoint(
    workflow_id: str,
    run_id: str,
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
):
    return await get_run(workflow_id, workspace_id, run_id)


@router.post("/trigger/manual")
async def manual_trigger_endpoint(
    workflow_id: str,
    body: ManualTriggerRequest = ManualTriggerRequest(),
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
):
    return await manual_trigger(workflow_id, workspace_id, body)


@router.post("/runs/{run_id}/retry")
async def retry_run_endpoint(
    workflow_id: str,
    run_id: str,
    _auth: bool = Depends(verify_internal_secret),
    workspace_id: str = Depends(get_workspace_id),
):
    return await retry_run(workflow_id, workspace_id, run_id)
