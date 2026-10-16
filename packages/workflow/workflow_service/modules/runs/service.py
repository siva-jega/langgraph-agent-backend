import json
import logging

from fastapi import HTTPException

from common.infrastructure.redis import get_redis
from workflow_service.core.config import app_settings
from workflow_service.modules.runs.models import (
    ManualTriggerRequest,
    RunDetailResponse,
    RunNodeResponse,
    RunResponse,
)
from workflow_service.modules.runs.repository import RunRepository
from workflow_service.modules.workflows.models import PaginatedResponse
from workflow_service.modules.workflows.repository import WorkflowRepository

logger = logging.getLogger(__name__)

_run_repo = RunRepository()
_workflow_repo = WorkflowRepository()


def _to_str(val) -> str:
    return str(val) if val is not None else ""


def _to_run_response(row: dict) -> RunResponse:
    return RunResponse(
        id=_to_str(row["id"]),
        workflowId=_to_str(row["workflowId"]),
        triggerNodeId=_to_str(row["triggerNodeId"]) if row.get("triggerNodeId") else None,
        status=row["status"],
        correlationId=row.get("correlationId"),
        inputData=row.get("inputData"),
        errorMessage=row.get("errorMessage"),
        startedAt=row.get("startedAt"),
        completedAt=row.get("completedAt"),
        createdAt=row["createdAt"],
    )


async def _verify_workflow(workflow_id: str, workspace_id: str) -> None:
    if not await _workflow_repo.verify_ownership(workflow_id, workspace_id):
        raise HTTPException(status_code=404, detail="Workflow not found")


async def list_runs(
    workflow_id: str,
    workspace_id: str,
    status: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> PaginatedResponse:
    await _verify_workflow(workflow_id, workspace_id)
    rows = await _run_repo.list_by_workflow(workflow_id, status, page, limit)
    total = await _run_repo.count_by_workflow(workflow_id, status)
    return PaginatedResponse(
        data=[_to_run_response(r).model_dump() for r in rows],
        total=total,
        page=page,
        limit=limit,
    )


async def get_run(
    workflow_id: str, workspace_id: str, run_id: str
) -> RunDetailResponse:
    await _verify_workflow(workflow_id, workspace_id)
    run = await _run_repo.get_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run_nodes = await _run_repo.get_run_steps(run_id)
    return RunDetailResponse(
        id=_to_str(run["id"]),
        workflowId=_to_str(run["workflowId"]),
        triggerNodeId=_to_str(run["triggerNodeId"]) if run.get("triggerNodeId") else None,
        status=run["status"],
        correlationId=run.get("correlationId"),
        inputData=run.get("inputData"),
        errorMessage=run.get("errorMessage"),
        startedAt=run.get("startedAt"),
        completedAt=run.get("completedAt"),
        createdAt=run["createdAt"],
        nodes=[
            RunNodeResponse(
                id=_to_str(n["id"]),
                nodeId=n["nodeId"],
                status=n["status"],
                inputData=n.get("inputData"),
                outputData=n.get("outputData"),
                errorMessage=n.get("errorMessage"),
                retryCount=n.get("retryCount", 0),
                startedAt=n.get("startedAt"),
                completedAt=n.get("completedAt"),
            )
            for n in run_nodes
        ],
    )


async def manual_trigger(
    workflow_id: str, workspace_id: str, data: ManualTriggerRequest
) -> dict:
    await _verify_workflow(workflow_id, workspace_id)

    wf = await _workflow_repo.get_by_id(workflow_id)
    if not wf or wf["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="Workflow must be ACTIVE to trigger")

    run = await _run_repo.create(
        workflow_id=workflow_id,
        trigger_node_id=None,
        input_data=data.inputData,
        correlation_id=None,
    )
    if not run:
        raise HTTPException(status_code=500, detail="Failed to create run")

    run_id = str(run["id"])

    try:
        redis = get_redis()
        await redis.lpush(
            app_settings.WORKFLOW_QUEUE_KEY,
            json.dumps({"workflowRunId": run_id, "workflowId": workflow_id}),
        )
    except Exception as e:
        logger.error("Failed to enqueue workflow run %s: %s", run_id, e)

    return {"runId": run_id, "status": "QUEUED"}
