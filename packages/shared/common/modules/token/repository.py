import json
import logging
from typing import Any, Dict, Optional

from common.db_queries import (
    get_app_credentials_query,
    get_app_credentials_by_slug_query,
)
from common.infrastructure import get_pg
from common.modules._protocols import RedisLike

logger = logging.getLogger(__name__)


class TokenRepository:
    def __init__(self, redis_service: Optional[RedisLike] = None):
        self._redis = redis_service

    def _ensure_redis(self) -> None:
        if not self._redis:
            raise RuntimeError("Redis service not provided to TokenRepository")

    async def get_app_credentials(
        self, user_id: str, app_id: str, cache_key_name: str
    ) -> Optional[Dict[str, Any]]:
        cached = await self._get_cached(user_id, cache_key_name)
        if cached:
            # Reset TTL so active users don't expire mid-session.
            await self.cache_credentials(user_id, cache_key_name, cached)
            return cached

        if app_id:
            db_data = await self._get_from_db(user_id, app_id)
            if db_data:
                return db_data

        db_data = await self._get_from_db_by_slug(user_id, cache_key_name)
        return db_data

    async def _get_cached(
        self, user_id: str, app_name: str
    ) -> Optional[Dict[str, Any]]:
        if not self._redis:
            return None
        try:
            key = f"{user_id}:{app_name}"
            data = await self._redis.get_json(key)
            if (
                data
                and isinstance(data, dict)
                and ("credentials" in data or "access_token" in data)
            ):
                return data
            return None
        except Exception as e:
            logger.error("Redis get error for %s: %s", app_name, e)
            return None

    async def _get_from_db(self, user_id: str, app_id: str) -> Optional[Dict[str, Any]]:
        try:
            pg = get_pg()
            query = get_app_credentials_query()
            async with pg.acquire() as conn:
                row = await conn.fetchrow(query, user_id, app_id)
          
            if not row:
                return None

            result = dict(row)
            logger.info("Fetched credentials for %s from DB: %s", app_id, result)
            # Parse JSON fields
            if result.get("credentials") and isinstance(result["credentials"], str):
                try:
                    result["credentials"] = json.loads(result["credentials"])
                except json.JSONDecodeError:
                    pass  # keep as string or handle error
            if result.get("config") and isinstance(result["config"], str):
                try:
                    result["config"] = json.loads(result["config"])
                except json.JSONDecodeError:
                    pass

            return result
        except RuntimeError:
            logger.warning("DATABASE_URL not set; cannot get from DB")
            return None
        except Exception as e:
            logger.error("DB get error for %s: %s", app_id, e)
            return None

    async def _get_from_db_by_slug(self, user_id: str, slug: str) -> Optional[Dict[str, Any]]:
        """Fallback: look up app credentials by slug when app_id is not available."""
        try:
            pg = get_pg()
            query = get_app_credentials_by_slug_query()
            async with pg.acquire() as conn:
                row = await conn.fetchrow(query, user_id, slug)

            if not row:
                return None

            result = dict(row)
            logger.info("Fetched credentials for slug=%s from DB: %s", slug, result)
            if result.get("credentials") and isinstance(result["credentials"], str):
                try:
                    result["credentials"] = json.loads(result["credentials"])
                except json.JSONDecodeError:
                    pass
            if result.get("config") and isinstance(result["config"], str):
                try:
                    result["config"] = json.loads(result["config"])
                except json.JSONDecodeError:
                    pass
            return result
        except RuntimeError:
            logger.warning("DATABASE_URL not set; cannot get from DB by slug")
            return None
        except Exception as e:
            logger.error("DB get error for slug=%s: %s", slug, e)
            return None

    async def cache_credentials(
        self, user_id: str, app_name: str, data: Dict[str, Any], ex: int = 3600
    ) -> None:
        if not self._redis:
            return
        try:
            await self._redis.set_json(f"{user_id}:{app_name}", data, ex=ex)
        except Exception as e:
            logger.error("Redis set error for %s: %s", app_name, e)

    async def delete_credentials(self, user_id: str, app_name: str) -> None:
        if not self._redis:
            return
        try:
            await self._redis.delete(f"{user_id}:{app_name}")
        except Exception as e:
            logger.error("Redis delete error for %s: %s", app_name, e)
