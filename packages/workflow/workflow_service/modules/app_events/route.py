from fastapi import APIRouter, Depends, HTTPException

from common.app_events import get_event_schema
from workflow_service.core.auth import verify_internal_secret

router = APIRouter(prefix="/app-events", tags=["App Events"])


@router.get("/{app_slug}/{event_type}")
async def get_app_event_schema(
    app_slug: str,
    event_type: str,
    _auth: bool = Depends(verify_internal_secret),
):
    """Return output parameter schema for a specific app+event combination."""
    schema = get_event_schema(app_slug, event_type)
    if schema is None:
        raise HTTPException(
            status_code=404,
            detail=f"No schema for {app_slug}/{event_type}",
        )
    return {"output_parameters": schema}
