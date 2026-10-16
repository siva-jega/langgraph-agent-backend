"""Agents module routes: HTTP API layer."""

import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.config import app_settings
from app.modules.agents.models import BenchChatRequest, ChatRequest
from app.modules.agents.repository import AgentsRepository
from app.modules.agents.service import (
    process_chat_request,
    process_chat_request_non_streaming,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["Agents"])
_repo = AgentsRepository()

def _get_client_ip(request: Request) -> str:
    """Extract client IP from request headers / connection info."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


def _get_request_domain(request: Request) -> str:
    """Extract hostname from the Origin header (falls back to Referer)."""
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if not origin:
        return ""
    try:
        return urlparse(origin).hostname or ""
    except Exception:
        return ""


_ALWAYS_ALLOWED_DOMAINS = {"localhost"}


def _check_domain_allowed(request_domain: str, allowed_domains: list) -> bool:
    """Return True if the request domain is in the allowed list, or if the list is empty."""
    if request_domain in _ALWAYS_ALLOWED_DOMAINS:
        return True
    if not allowed_domains:
        return True
    if not request_domain:
        return False
    normalized = {urlparse(d).hostname or d for d in allowed_domains}
    return request_domain in normalized


@router.post("/stream")
async def agents_chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    """Streaming chat endpoint for agents."""
    try:
        repo = getattr(request.app.state, "agents_repo", _repo)

        prefetched_config: dict | None = None
        if not payload.isPreview:
            prefetched_config = await repo.get_config(payload.agentId)
            if prefetched_config is None:
                raise HTTPException(status_code=404, detail=f"Agent {payload.agentId} not found")
            allowed_domains = prefetched_config.get("domains", [])
            request_domain = _get_request_domain(request)
            if not _check_domain_allowed(request_domain, allowed_domains):
                logger.warning(
                    "Domain '%s' not allowed for agent %s (allowed: %s)",
                    request_domain,
                    payload.agentId,
                    allowed_domains,
                )
                raise HTTPException(status_code=401, detail="Unauthorized")

        async def stream():
            geoip_reader = getattr(request.app.state, "geoip_reader", None)
            client_ip = _get_client_ip(request)
            pfc_dict = None
            if payload.productFaqContext is not None:
                pfc_dict = payload.productFaqContext.model_dump(
                    mode="json", by_alias=False, exclude_none=True
                )
            async for chunk in process_chat_request(
                message=payload.message,
                chat_id=payload.chatId,
                session_id=payload.sessionId,
                agent_id=payload.agentId,
                is_preview=payload.isPreview,
                customer_email=payload.customerEmail,
                customer_name=payload.customerName,
                interrupt_payload=payload.interruptPayload,
                resume_config=payload.resumeConfig,
                repo=repo,
                client_ip=client_ip,
                geoip_reader=geoip_reader,
                product_faq_context=pfc_dict,
                collection_id=payload.collectionId,
                prefetched_config=prefetched_config,
            ):
                yield chunk

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error in chat stream: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/bench/stream")
async def agents_bench_stream(payload: BenchChatRequest, request: Request) -> StreamingResponse:
    """Benchmarking endpoint: same as /stream but accepts a `model` override.
    Requires the `x-bench-secret` header to match BENCH_SECRET env var.
    """
    secret = request.headers.get("x-bench-secret", "")
    if not app_settings.BENCH_SECRET or secret != app_settings.BENCH_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        repo = getattr(request.app.state, "agents_repo", _repo)

        async def stream():
            async for chunk in process_chat_request(
                message=payload.message,
                chat_id=payload.chatId,
                session_id=payload.sessionId,
                agent_id=payload.agentId,
                is_preview=payload.isPreview,
                repo=repo,
                model_override=payload.model,
            ):
                yield chunk

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error in bench stream: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/chat")
async def agents_chat_non_streaming(request: ChatRequest):
    """Non-streaming chat endpoint for agents. Returns full response JSON."""
    try:
        # Get config from DB/Redis and merge with request params
        config = await _repo.get_config(request.agentId)
        if config is None:
            raise HTTPException(
                status_code=404, detail=f"Agent {request.agentId} not found"
            )

        additional_params = {
            **(config if isinstance(config, dict) else {}),
            "chat_id": request.chatId,
            "session_id": request.sessionId,
            "website_info": request.websiteInfo,
            "customer_email": request.customerEmail,
            "customer_name": request.customerName,
            "interrupt_payload": request.interruptPayload,
        }

        return await process_chat_request_non_streaming(
            message=request.message,
            agent_id=request.agentId,
            is_preview=request.isPreview,
            resume_config=request.resumeConfig,
            additional_params=additional_params,
        )
    except Exception as e:
        logger.error("Unexpected error in chat (non-streaming): %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{agent_id}/config")
async def get_agent_config(agent_id: str):
    """Get agent configuration. Returns cached value from Redis if available, otherwise fetches from DB."""
    try:
        config = await _repo.get_config(agent_id)
        if config is None:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
        return config
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error getting agent config: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/{agent_id}/config/cache")
async def invalidate_agent_config_cache(agent_id: str):
    """Invalidate Redis cache for agent config. Next get_config call will fetch fresh data from DB."""
    try:
        deleted = await _repo.invalidate_config_cache(agent_id)
        return {"agent_id": agent_id, "cache_invalidated": deleted}
    except Exception as e:
        logger.error("Unexpected error invalidating cache: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
