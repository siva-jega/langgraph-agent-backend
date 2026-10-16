import logging
from typing import Any

from fastapi import HTTPException

from workflow_service.modules.workflows.models import (
    EdgeResponse,
    NodeCategory,
    NodeResponse,
    PaginatedResponse,
    WorkflowListItem,
    WorkflowResponse,
)
from workflow_service.modules.workflows.repository import WorkflowRepository

logger = logging.getLogger(__name__)

_repo = WorkflowRepository()


def _to_str(val: Any) -> str:
    return str(val) if val is not None else ""


def _build_response(wf: dict, nodes: list[dict], edges: list[dict]) -> WorkflowResponse:
    return WorkflowResponse(
        id=_to_str(wf["id"]),
        workspaceId=_to_str(wf["workspaceId"]),
        name=wf["name"],
        description=wf.get("description"),
        status=wf["status"],
        version=wf["version"],
        nodes=[
            NodeResponse(
                id=_to_str(n["id"]),
                nodeId=n["nodeId"],
                type=n["type"],
                category=n["category"],
                label=n["label"],
                config=n.get("config") or {},
                inputParameters=n.get("inputParameters") or [],
                outputParameters=n.get("outputParameters") or [],
                positionX=n.get("positionX", 0),
                positionY=n.get("positionY", 0),
                errorPolicy=n.get("errorPolicy", "FAIL"),
                createdAt=n["createdAt"],
            )
            for n in nodes
        ],
        edges=[
            EdgeResponse(
                id=_to_str(e["id"]),
                sourceNodeId=e["sourceNodeId"],
                targetNodeId=e["targetNodeId"],
                sourceHandle=e.get("sourceHandle"),
            )
            for e in edges
        ],
        createdAt=wf["createdAt"],
        updatedAt=wf["updatedAt"],
    )


async def _verify(workflow_id: str, workspace_id: str) -> None:
    if not await _repo.verify_ownership(workflow_id, workspace_id):
        raise HTTPException(status_code=404, detail="Workflow not found")


async def get_workflow(workflow_id: str, workspace_id: str) -> WorkflowResponse:
    await _verify(workflow_id, workspace_id)
    wf = await _repo.get_by_id(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    nodes = await _repo.get_nodes(workflow_id)
    edges = await _repo.get_edges(workflow_id)
    return _build_response(wf, nodes, edges)


async def list_workflows(
    workspace_id: str,
    status: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> PaginatedResponse:
    items = await _repo.list_by_workspace(workspace_id, status, page, limit)
    total = await _repo.count_by_workspace(workspace_id, status)
    return PaginatedResponse(
        data=[
            WorkflowListItem(
                id=_to_str(w["id"]),
                workspaceId=_to_str(w["workspaceId"]),
                name=w["name"],
                description=w.get("description"),
                status=w["status"],
                version=w["version"],
                createdAt=w["createdAt"],
                updatedAt=w["updatedAt"],
            ).model_dump()
            for w in items
        ],
        total=total,
        page=page,
        limit=limit,
    )


async def publish_workflow(workflow_id: str, workspace_id: str) -> WorkflowResponse:
    await _verify(workflow_id, workspace_id)

    nodes = await _repo.get_nodes(workflow_id)
    trigger_nodes = [n for n in nodes if n.get("category") == "TRIGGER"]
    if len(trigger_nodes) != 1:
        raise HTTPException(
            status_code=400,
            detail=f"Workflow must have exactly one trigger node (found {len(trigger_nodes)})",
        )

    wf = await _repo.publish(workflow_id)
    if not wf:
        raise HTTPException(status_code=500, detail="Failed to publish workflow")

    edges = await _repo.get_edges(workflow_id)
    return _build_response(wf, nodes, edges)


async def deactivate_workflow(workflow_id: str, workspace_id: str) -> WorkflowResponse:
    await _verify(workflow_id, workspace_id)
    wf = await _repo.deactivate(workflow_id)
    if not wf:
        raise HTTPException(status_code=500, detail="Failed to deactivate workflow")
    nodes = await _repo.get_nodes(workflow_id)
    edges = await _repo.get_edges(workflow_id)
    return _build_response(wf, nodes, edges)
