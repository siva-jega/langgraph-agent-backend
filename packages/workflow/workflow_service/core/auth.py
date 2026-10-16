import logging

from fastapi import Header, HTTPException, Request

from workflow_service.core.config import app_settings

logger = logging.getLogger(__name__)


async def verify_internal_secret(
    request: Request,
    x_internal_secret: str = Header(..., alias="x-internal-secret"),
) -> bool:
    if not app_settings.INTERNAL_SECRET:
        logger.warning(
            "INTERNAL_SECRET not configured, allowing request: %s %s from %s",
            request.method,
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        return True
    if x_internal_secret != app_settings.INTERNAL_SECRET:
        raise HTTPException(status_code=401, detail="Invalid internal secret")
    return True


async def get_workspace_id(
    x_workspace_id: str = Header(..., alias="x-workspace-id"),
) -> str:
    if not x_workspace_id:
        raise HTTPException(status_code=400, detail="x-workspace-id header required")
    return x_workspace_id
