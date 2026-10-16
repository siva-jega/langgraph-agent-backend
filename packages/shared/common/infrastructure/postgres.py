"""Postgres client: single connection pool per app. Initialized in core.lifecycle."""

from typing import Optional

import asyncpg

_PostgresClient_instance: Optional["PostgresClient"] = None


class PostgresClient:
    """Postgres client wrapping a shared connection pool. Single instance per app."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @property
    def pool(self) -> asyncpg.Pool:
        """Connection pool. Use pool.acquire() to borrow a connection (no new conn per request)."""
        return self._pool

    def acquire(self):
        """Borrow a connection from the pool. Use: async with get_pg().acquire() as conn."""
        return self._pool.acquire()

    async def close(self) -> None:
        await self._pool.close()


def get_pg() -> PostgresClient:
    """Return the app Postgres client. Use in repositories: from common.infrastructure import get_pg."""
    if _PostgresClient_instance is None:
        raise RuntimeError("Postgres not initialized; app lifespan may not have run.")
    return _PostgresClient_instance


def set_pg(client: PostgresClient) -> None:
    """Set by lifecycle on startup. Do not call from application code."""
    global _PostgresClient_instance
    _PostgresClient_instance = client


def clear_pg() -> None:
    """Cleared by lifecycle on shutdown."""
    global _PostgresClient_instance
    _PostgresClient_instance = None
