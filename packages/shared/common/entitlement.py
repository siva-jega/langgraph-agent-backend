"""Pre-flight AI usage entitlement check against user-backend.

Calls Nest ``POST /internal/ai-entitlement`` (guarded by ``x-internal-secret``) to
decide whether a user may invoke AI right now — blocked when their trial has ended
or their plan's ``message_credits`` are spent.

Fails OPEN on any error (missing config, network, bad response) so a user-backend
hiccup never takes every widget down; the post-hoc usage logging still runs.
Results are cached in-process for a short TTL to keep the chat hot-path fast.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import httpx

from common.project_settings import project_settings
from common.core.nestjs_staging_defaults import resolved_nestjs_internal_secret

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30.0
_REQUEST_TIMEOUT_SECONDS = 5.0

# user_id -> (cached_at_monotonic, verdict)
_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}

_ALLOW: Dict[str, Any] = {
    "allowed": True,
    "blockedReason": None,
    "limit": -1,
    "current": 0,
    "remaining": -1,
}


async def check_ai_entitlement(user_id: Optional[str]) -> Dict[str, Any]:
    """Return ``{allowed, blockedReason, limit, current, remaining}``.

    ``blockedReason`` is ``TRIAL_ENDED`` | ``LIMIT_REACHED`` | ``None``.
    Fail-open: returns an "allowed" verdict on any error or missing config.
    """
    if not user_id:
        return dict(_ALLOW)

    now = time.monotonic()
    cached = _cache.get(user_id)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        base_url = (
            getattr(project_settings(), "NESTJS_URL", None) or ""
        ).rstrip("/")
        secret = (resolved_nestjs_internal_secret() or "").strip()
        if not base_url or not secret:
            logger.warning(
                "ai-entitlement: missing NESTJS_URL or internal secret; allowing"
            )
            return dict(_ALLOW)

        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{base_url}/internal/ai-entitlement",
                json={"userId": user_id},
                headers={"x-internal-secret": secret},
            )
            resp.raise_for_status()
            data = resp.json()
            # Tolerate both the raw object and a {result: ...} envelope.
            verdict = data.get("result", data) if isinstance(data, dict) else data
            if not isinstance(verdict, dict) or "allowed" not in verdict:
                logger.warning(
                    "ai-entitlement: unexpected response shape; allowing: %r", data
                )
                return dict(_ALLOW)
            _cache[user_id] = (now, verdict)
            return verdict
    except Exception as exc:  # noqa: BLE001 — fail-open on any failure
        logger.warning("ai-entitlement check failed (%s); allowing", exc)
        return dict(_ALLOW)
