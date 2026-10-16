from fastapi import APIRouter, Depends, HTTPException

from workflow_service.core.auth import verify_internal_secret
from workflow_service.engine.node_registry import get_all_nodes, get_node

router = APIRouter(prefix="/nodes", tags=["Nodes"])


@router.get("")
async def list_nodes(
    _auth: bool = Depends(verify_internal_secret),
):
    """Return the full node registry — all built-in trigger and step definitions."""
    return get_all_nodes()


@router.get("/{node_id}")
async def get_node_by_id(
    node_id: str,
    _auth: bool = Depends(verify_internal_secret),
):
    """Return the definition for a single node type by its ID (e.g. WAIT, WEBHOOK)."""
    node = get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in registry")
    return node
