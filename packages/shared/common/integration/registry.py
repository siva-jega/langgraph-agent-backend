"""
Shared service getters (lazy singletons). Used by tool providers.

Wires common services to infra: get_redis(), get_milvus(), and a thin NestJS HTTP client.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from common.infrastructure import get_milvus, get_redis
from common.modules.email import EmailService
from common.modules.retrieval import RetrievalService
from common.modules.token import TokenService
from common.modules.usage import UsageService
from common.project_settings import project_settings

logger = logging.getLogger(__name__)

_email: Optional[EmailService] = None
_retrieval: Optional[RetrievalService] = None
_token: Optional[TokenService] = None
_usage: Optional[UsageService] = None


class _NestJSApiClient:
    """Thin async HTTP client for NestJS user-backend (get_json / post_json)."""

    async def get_json(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.get(url)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.debug("NestJS get %s: %s", url, e)
            return None

    async def post_json(self, url: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post(url, json=data)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.debug("NestJS post %s: %s", url, e)
            return None


def get_email() -> EmailService:
    """Shared EmailService (lazy init)."""
    global _email
    if _email is None:
        _email = EmailService(project_settings())
    return _email


def get_retrieval() -> RetrievalService:
    """Shared RetrievalService (lazy init; uses get_milvus())."""
    global _retrieval
    if _retrieval is None:
        _retrieval = RetrievalService(
            settings=project_settings(), milvus_service=get_milvus()
        )
    return _retrieval


def get_token() -> TokenService:
    """Shared TokenService (lazy init; uses get_redis() + NestJS client)."""
    global _token
    if _token is None:
        _token = TokenService(
            settings=project_settings(),
            redis_service=get_redis(),
            api_service=_NestJSApiClient(),
        )
    return _token


def get_usage() -> UsageService:
    """Shared UsageService (lazy init). Used by agents repo for message credits."""
    global _usage
    if _usage is None:
        _usage = UsageService()
    return _usage
