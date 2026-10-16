"""Product Summary module routes: HTTP API for AI product summaries."""

import logging

from fastapi import APIRouter, HTTPException, Query

from app.modules.product_summary.service import get_product_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/product-summary", tags=["Product Summary"])


@router.get("")
async def product_summary(
    productId: str = Query(..., description="Product ID (internal UUID or external ID)"),
    platform: str = Query(..., description="Platform: 'shopify' or 'woocommerce'"),
    workspaceId: str = Query(..., description="Workspace ID"),
):
    """Get AI-generated product summary with suggested questions.

    Returns cached summary if available, otherwise generates one using GPT-4o-mini.
    """
    platform = platform.strip().lower()
    if platform not in ("shopify", "woocommerce"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid platform '{platform}'. Must be 'shopify' or 'woocommerce'.",
        )

    try:
        result = await get_product_summary(
            product_id=productId,
            platform=platform,
            workspace_id=workspaceId,
        )

        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Product '{productId}' not found for platform '{platform}'.",
            )

        return result

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error generating product summary: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
