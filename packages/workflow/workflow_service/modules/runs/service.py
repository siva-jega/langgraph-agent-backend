import json
import logging
from uuid import uuid4

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
    if not run or str(run.get("workflowId")) != workflow_id:
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
    except Exception as exc:
        logger.exception("Failed to enqueue workflow run %s", run_id)
        await _run_repo.update_status(
            run_id,
            "FAILED",
            error_message="Run could not be queued; please retry again",
        )
        raise HTTPException(status_code=503, detail="Run could not be queued") from exc

    return {"runId": run_id, "status": "QUEUED"}


async def retry_run(workflow_id: str, workspace_id: str, run_id: str) -> dict:
    """Create a fresh queued run from a failed run's saved input."""
    await _verify_workflow(workflow_id, workspace_id)

    original = await _run_repo.get_by_id(run_id)
    if not original or str(original.get("workflowId")) != workflow_id:
        raise HTTPException(status_code=404, detail="Run not found")
    if original.get("status") != "FAILED":
        raise HTTPException(
            status_code=409,
            detail="Only failed runs can be retried",
        )

    workflow = await _workflow_repo.get_by_id(workflow_id)
    if not workflow or workflow["status"] != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail="Workflow must be ACTIVE to retry a run",
        )

    retry = await _run_repo.create(
        workflow_id=workflow_id,
        trigger_node_id=original.get("triggerNodeId"),
        input_data=original.get("inputData"),
        correlation_id=str(uuid4()),
    )
    if not retry:
        raise HTTPException(status_code=500, detail="Failed to create retry run")

    retry_id = str(retry["id"])
    try:
        redis = get_redis()
        await redis.lpush(
            app_settings.WORKFLOW_QUEUE_KEY,
            json.dumps({"workflowRunId": retry_id, "workflowId": workflow_id}),
        )
    except Exception as exc:
        logger.exception("Failed to enqueue retry run %s", retry_id)
        await _run_repo.update_status(
            retry_id,
            "FAILED",
            error_message="Retry could not be queued; please retry again",
        )
        raise HTTPException(
            status_code=503,
            detail="Retry could not be queued",
        ) from exc

    return {"runId": retry_id, "status": "QUEUED", "retryOf": run_id}
