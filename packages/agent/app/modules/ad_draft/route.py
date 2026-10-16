import logging

from fastapi import APIRouter, HTTPException, Request

from common.core.nestjs_staging_defaults import resolved_nestjs_internal_secret
from app.modules.ad_draft.models import AdDraftRequest
from app.modules.ad_draft.service import draft_ad

logger = logging.getLogger(__name__)

# Server-to-server (Nest). Reached at /api/internal/ad-draft/generate.
router = APIRouter(prefix="/internal/ad-draft", tags=["Internal — Ad Draft"])


def _require_internal_secret(request: Request) -> None:
    expected = (resolved_nestjs_internal_secret() or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Internal auth not configured")
    got = (request.headers.get("x-internal-secret") or "").strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/generate")
async def generate_ad_draft_route(request: Request, payload: AdDraftRequest):
    _require_internal_secret(request)
    try:
        return await draft_ad(payload)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("ad draft generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to draft the ad")
