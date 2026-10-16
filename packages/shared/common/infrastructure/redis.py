"""Redis client: single connection pool per app. Initialized in core.lifecycle."""

import json
import logging
from typing import Any, List, Optional, Union

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_RedisClient_instance: Optional["RedisClient"] = None


class RedisClient:
    """Redis client with connection management and common operations. Single instance per app."""

    def __init__(self, connection: aioredis.Redis) -> None:
        self._conn = connection

    @property
    def client(self) -> aioredis.Redis:
        """Raw Redis connection for full API. Pool is reused; no new conn per request."""
        return self._conn

    @property
    def connection(self) -> aioredis.Redis:
        """Alias for client."""
        return self._conn

    def is_connected(self) -> bool:
        """Check if Redis is connected (best-effort via ping in sync context)."""
        return self._conn is not None

    # Basic operations
    async def get(self, key: str) -> Optional[str]:
        """Get value by key."""
        try:
            return await self._conn.get(key)
        except Exception as e:
            logger.error("Error getting key %s: %s", key, e)
            return None

    async def set(
        self,
        key: str,
        value: Any,
        ex: Optional[int] = None,
        px: Optional[int] = None,
    ) -> bool:
        """Set key-value pair with optional expiration. Serializes dict/list to JSON."""
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            result = await self._conn.set(key, value, ex=ex, px=px)
            return bool(result)
        except Exception as e:
            logger.error("Error setting key %s: %s", key, e)
            return False

    async def delete(self, *keys: str) -> bool:
        """Delete one or more keys."""
        try:
            return bool(await self._conn.delete(*keys))
        except Exception as e:
            logger.error("Error deleting keys %s: %s", keys, e)
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        try:
            return bool(await self._conn.exists(key))
        except Exception as e:
            logger.error("Error checking existence of key %s: %s", key, e)
            return False

    async def expire(self, key: str, seconds: int) -> bool:
        """Set expiration for key."""
        try:
            return await self._conn.expire(key, seconds)
        except Exception as e:
            logger.error("Error setting expiration for key %s: %s", key, e)
            return False

    # JSON operations
    async def get_json(self, key: str) -> Optional[Union[dict, list]]:
        """Get JSON value by key."""
        value = await self.get(key)
        if value is not None:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                logger.error("Invalid JSON for key %s", key)
        return None

    async def set_json(
        self,
        key: str,
        value: Union[dict, list],
        ex: Optional[int] = None,
    ) -> bool:
        """Set JSON value by key."""
        return await self.set(key, value, ex=ex)

    # List operations
    async def lpush(self, key: str, *values: Any) -> Optional[int]:
        """Push values to the left of list."""
        try:
            return await self._conn.lpush(key, *values)
        except Exception as e:
            logger.error("Error pushing to list %s: %s", key, e)
            return None

    async def rpush(self, key: str, *values: Any) -> Optional[int]:
        """Push values to the right of list."""
        try:
            return await self._conn.rpush(key, *values)
        except Exception as e:
            logger.error("Error pushing to list %s: %s", key, e)
            return None

    async def lpop(self, key: str) -> Optional[str]:
        """Pop value from the left of list."""
        try:
            return await self._conn.lpop(key)
        except Exception as e:
            logger.error("Error popping from list %s: %s", key, e)
            return None

    async def rpop(self, key: str) -> Optional[str]:
        """Pop value from the right of list."""
        try:
            return await self._conn.rpop(key)
        except Exception as e:
            logger.error("Error popping from list %s: %s", key, e)
            return None

    async def lrange(self, key: str, start: int = 0, end: int = -1) -> List[str]:
        """Get range of list elements."""
        try:
            result = await self._conn.lrange(key, start, end)
            return list(result) if result else []
        except Exception as e:
            logger.error("Error getting range from list %s: %s", key, e)
            return []

    # Hash operations
    async def hset(self, key: str, field: str, value: Any) -> bool:
        """Set hash field."""
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            return await self._conn.hset(key, field, value)
        except Exception as e:
            logger.error("Error setting hash field %s.%s: %s", key, field, e)
            return False

    async def hget(self, key: str, field: str) -> Optional[str]:
        """Get hash field value."""
        try:
            return await self._conn.hget(key, field)
        except Exception as e:
            logger.error("Error getting hash field %s.%s: %s", key, field, e)
            return None

    async def hgetall(self, key: str) -> dict:
        """Get all hash fields."""
        try:
            result = await self._conn.hgetall(key)
            return dict(result) if result else {}
        except Exception as e:
            logger.error("Error getting all hash fields %s: %s", key, e)
            return {}

    async def hdel(self, key: str, field: str) -> bool:
        """Delete hash field."""
        try:
            return bool(await self._conn.hdel(key, field))
        except Exception as e:
            logger.error("Error deleting hash field %s.%s: %s", key, field, e)
            return False

    # Set operations
    async def sadd(self, key: str, *values: Any) -> Optional[int]:
        """Add values to set."""
        try:
            return await self._conn.sadd(key, *values)
        except Exception as e:
            logger.error("Error adding to set %s: %s", key, e)
            return None

    async def srem(self, key: str, *values: Any) -> Optional[int]:
        """Remove values from set."""
        try:
            return await self._conn.srem(key, *values)
        except Exception as e:
            logger.error("Error removing from set %s: %s", key, e)
            return None

    async def smembers(self, key: str) -> set:
        """Get all set members."""
        try:
            result = await self._conn.smembers(key)
            return set(result) if result else set()
        except Exception as e:
            logger.error("Error getting set members %s: %s", key, e)
            return set()

    async def sismember(self, key: str, value: Any) -> bool:
        """Check if value is in set."""
        try:
            return await self._conn.sismember(key, value)
        except Exception as e:
            logger.error("Error checking set membership %s: %s", key, e)
            return False

    async def close(self) -> None:
        """Close Redis connection."""
        await self._conn.aclose()


def get_redis() -> RedisClient:
    """Return the app Redis client. Use in repositories: from common.infrastructure import get_redis."""
    if _RedisClient_instance is None:
        raise RuntimeError("Redis not initialized; app lifespan may not have run.")
    return _RedisClient_instance


def set_redis(client: RedisClient) -> None:
    """Set by lifecycle on startup. Do not call from application code."""
    global _RedisClient_instance
    _RedisClient_instance = client


def clear_redis() -> None:
    """Cleared by lifecycle on shutdown."""
    global _RedisClient_instance
    _RedisClient_instance = None
