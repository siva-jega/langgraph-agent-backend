"""Product FAQ proposal generation: internal (Nest) and public (dashboard proxy) routes."""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from common.core.nestjs_staging_defaults import resolved_nestjs_internal_secret
from app.modules.product_faq_generation.service import generate_product_faq_proposals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/product-faqs", tags=["Internal — Product FAQs"])

# Unauthenticated for now; intended to be called via the Next.js proxy (session) only.
public_router = APIRouter(prefix="/product-faqs", tags=["Product FAQs"])


class GenerateFaqProposalsBody(BaseModel):
    product_id: str = Field(..., min_length=1)
    workspace_id: str = Field(..., min_length=1)
    platform: Literal["shopify", "woocommerce"]
    proposal_count: int = Field(
        default=3,
        ge=1,
        le=3,
        description="How many FAQs to propose in one call (Nest sends min(3, 5 − existing count)).",
    )


def _require_internal_secret(request: Request) -> None:
    expected = (resolved_nestjs_internal_secret() or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Internal auth not configured")
    got = (request.headers.get("x-internal-secret") or "").strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _generate_faq_proposals_body(body: GenerateFaqProposalsBody):
    try:
        result = await generate_product_faq_proposals(
            body.product_id,
            body.workspace_id,
            body.platform,
            body.proposal_count,
        )
        if not result.get("ok") and result.get("error") == "product_not_found":
            raise HTTPException(status_code=404, detail="Product not found")
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("generate_faq_proposals failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/generate")
async def generate_faq_proposals_internal(request: Request, body: GenerateFaqProposalsBody):
    """Server-to-server: load product from DB and return proposed FAQs (not persisted)."""
    _require_internal_secret(request)
    return await _generate_faq_proposals_body(body)


@public_router.post("/generate")
async def generate_faq_proposals_public(body: GenerateFaqProposalsBody):
    """Same behavior as the internal route, without shared-secret auth (lock down later)."""
    return await _generate_faq_proposals_body(body)
