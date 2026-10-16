"""Tools module routes: HTTP API for tools by provider slug."""

import logging

from fastapi import APIRouter, HTTPException

from app.modules.tools.service import (
    get_all_tools_metadata,
    get_tools_by_provider_slug,
    get_available_provider_slugs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["Tools"])


@router.get("")
async def list_providers():
    """Return all available provider slugs."""
    try:
        slugs = get_available_provider_slugs()
        tools = get_all_tools_metadata()
        return {"providers": slugs, "tools": tools}
    except Exception as e:
        logger.error("Unexpected error listing providers: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/all")
async def get_all_tools():
    """Return tools for all providers."""
    try:
        tools = get_all_tools_metadata()
        return {"tools": tools}
    except Exception as e:
        logger.error("Unexpected error getting all tools: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{provider}")
async def get_tools_by_provider(provider: str):
    """Return tools for the given provider slug (e.g. knowledgebase, gmail, shopify)."""
    try:
        slug = provider.strip().lower()
        tools = get_tools_by_provider_slug(slug)
        return {
            "provider": slug,
            "tools": tools,
        }
    except Exception as e:
        logger.error("Unexpected error getting tools for provider %s: %s", provider, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
