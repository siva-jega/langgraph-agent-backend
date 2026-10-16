import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
import redis.asyncio as aioredis

from common.infrastructure.postgres import PostgresClient, clear_pg, get_pg, set_pg
from common.infrastructure.redis import RedisClient, clear_redis, get_redis, set_redis
from workflow_service.core.config import app_settings
from workflow_service.db.init_schema import ensure_schema

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app) -> AsyncGenerator[None, None]:
    s = app_settings

    # Redis
    if s.REDIS_URI:
        try:
            redis_conn = aioredis.from_url(
                s.REDIS_URI,
                decode_responses=s.REDIS_DECODE_RESPONSES,
                socket_timeout=s.REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=s.REDIS_SOCKET_CONNECT_TIMEOUT,
                max_connections=s.REDIS_MAX_CONNECTIONS,
            )
            set_redis(RedisClient(redis_conn))
            logger.info("Redis client initialized")
        except Exception as e:
            logger.warning("Redis init skipped: %s", e)
    else:
        logger.info("Redis skipped (no REDIS_URI)")

    # Postgres
    if s.DATABASE_URL:
        try:
            pg_pool = await asyncpg.create_pool(
                s.DATABASE_URL,
                min_size=1,
                max_size=10,
                command_timeout=60,
            )
            set_pg(PostgresClient(pg_pool))
            logger.info("Postgres client initialized")
        except Exception as e:
            logger.warning("Postgres init skipped: %s", e)
    else:
        logger.info("Postgres skipped (no DATABASE_URL)")

    # Schema init
    try:
        await ensure_schema()
    except Exception as e:
        logger.error("Schema init failed: %s", e)

    # Start background workers
    from workflow_service.engine.worker import start_worker
    from workflow_service.events.subscriber import start_subscriber

    start_worker()
    start_subscriber()

    yield

    # Shutdown
    from workflow_service.engine.worker import stop_worker
    from workflow_service.events.subscriber import stop_subscriber

    await stop_worker()
    await stop_subscriber()

    try:
        redis_client = get_redis()
        clear_redis()
        await redis_client.close()
    except RuntimeError:
        pass
    except Exception as e:
        logger.debug("Redis close: %s", e)

    try:
        pg_client = get_pg()
        clear_pg()
        await pg_client.close()
    except RuntimeError:
        pass
    except Exception as e:
        logger.debug("Postgres close: %s", e)

    logger.info("Infrastructure clients closed")
