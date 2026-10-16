"""Bounded, cached readiness checks for the agent API.

The liveness route stays cheap. Readiness checks only services that were
configured, and never returns connection strings or exception messages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic, perf_counter
from typing import Any, Awaitable, Callable

from common.infrastructure.milvus import get_milvus
from common.infrastructure.postgres import get_pg
from common.infrastructure.redis import get_redis


@dataclass(frozen=True)
class ProbeResult:
    status: str
    duration_ms: float

    def as_dict(self) -> dict[str, str | float]:
        return {"status": self.status, "duration_ms": self.duration_ms}


@dataclass(frozen=True)
class ReadinessSnapshot:
    status: str
    checked_at: str
    checks: dict[str, ProbeResult]

    @property
    def http_status(self) -> int:
        return 200 if self.status == "ready" else 503

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checked_at": self.checked_at,
            "checks": {name: result.as_dict() for name, result in self.checks.items()},
        }


class ReadinessService:
    """Runs dependency checks with a timeout and a short shared cache."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._last: ReadinessSnapshot | None = None
        self._expires_at = 0.0

    async def snapshot(self, app: Any) -> ReadinessSnapshot:
        now = monotonic()
        if self._last is not None and now < self._expires_at:
            return self._last

        # Requests arriving together share one round of checks.
        async with self._lock:
            now = monotonic()
            if self._last is not None and now < self._expires_at:
                return self._last
            result = await self._check(app)
            self._last = result
            cache_seconds = max(
                0.0, min(30.0, float(self._settings.READINESS_CACHE_SECONDS))
            )
            self._expires_at = monotonic() + cache_seconds
            return result

    async def _check(self, app: Any) -> ReadinessSnapshot:
        checkpointer = getattr(app.state, "checkpointer", None)
        checkpoint_configured = bool(
            (self._settings.CHECKPOINTER_URL or self._settings.POSTGRES_URL)
            and self._settings.CHECKPOINT_TYPE != "memory"
        )
        checkpoint_ok = checkpointer is not None
        if checkpoint_configured:
            # A fallback to MemorySaver is useful for development, but it is
            # not ready to serve requests that expect durable history.
            from app.core.config import memory_saver

            checkpoint_ok = checkpoint_ok and checkpointer is not memory_saver

        checkpoint = ProbeResult(
            status="ok" if checkpoint_ok else "unavailable",
            duration_ms=0.0,
        )

        postgres, redis, milvus = await asyncio.gather(
            self._configured_probe(bool(self._settings.DATABASE_URL), self._postgres),
            self._configured_probe(bool(self._settings.REDIS_URI), self._redis),
            self._configured_probe(bool(self._settings.MILVUS_URI), self._milvus),
        )
        checks = {
            "checkpointer": checkpoint,
            "postgres": postgres,
            "redis": redis,
            "milvus_client": milvus,
        }
        status = (
            "ready"
            if all(result.status in {"ok", "skipped"} for result in checks.values())
            else "unavailable"
        )
        return ReadinessSnapshot(
            status=status,
            checked_at=datetime.now(timezone.utc).isoformat(),
            checks=checks,
        )

    async def _configured_probe(
        self, configured: bool, probe: Callable[[], Awaitable[bool]]
    ) -> ProbeResult:
        if not configured:
            return ProbeResult(status="skipped", duration_ms=0.0)
        started = perf_counter()
        timeout = max(0.1, min(10.0, float(self._settings.READINESS_TIMEOUT_SECONDS)))
        try:
            healthy = await asyncio.wait_for(probe(), timeout=timeout)
        except Exception:
            healthy = False
        return ProbeResult(
            status="ok" if healthy else "unavailable",
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )

    @staticmethod
    async def _postgres() -> bool:
        async with get_pg().acquire() as connection:
            return await connection.fetchval("SELECT 1") == 1

    @staticmethod
    async def _redis() -> bool:
        return bool(await get_redis().client.ping())

    @staticmethod
    async def _milvus() -> bool:
        # connect() sets up an HTTP client; this check confirms initialization.
        # Search errors are handled at the operation level.
        return get_milvus().is_connected()
