"""App lifespan: init and teardown Redis, Postgres, Milvus clients. Use get_* from common.infrastructure in repos."""

import asyncio
import logging
from contextlib import asynccontextmanager
from contextlib import suppress
from pathlib import Path
from typing import AsyncGenerator

import geoip2.database
import redis.asyncio as aioredis
from psycopg_pool import AsyncConnectionPool
import asyncpg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.modules.agents.graph import init_agent
from app.modules.channels.consumers import start_consumer, stop_consumer
from app.modules.channels.ai import init_channel_runtime
from app.modules.copilot.graph import init_copilot
from app.modules.channels.consumers import start_consumer as start_subscriber, stop_consumer as stop_subscriber
from app.modules.ops.service import MilvusWarmupService
from app.core.config import app_settings, memory_saver
from common.infrastructure.redis import (
    RedisClient,
    get_redis,
    set_redis,
    clear_redis,
)
from common.infrastructure.postgres import (
    PostgresClient,
    set_pg,
    clear_pg,
    get_pg,
)
from common.infrastructure.milvus import (
    MilvusClient,
    get_milvus,
    set_milvus,
    clear_milvus,
)

logger = logging.getLogger(__name__)


def _get_milvus_warmup_service(app) -> MilvusWarmupService | None:
    return getattr(app.state, "milvus_warmup_service", None)


@asynccontextmanager
async def lifespan(app) -> AsyncGenerator[None, None]:
    """Startup: init Redis, Postgres, Milvus clients (single instance each). Shutdown: close them."""
    s = app_settings

    # Redis: single connection pool
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

    # Postgres: single connection pool (for app DB, not checkpointer)
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

    # GeoIP: open MaxMind reader once (used for new-chat location enrichment)
    app.state.geoip_reader = None
    try:
        mmdb_path = (
            Path(__file__).resolve().parents[1] / "db" / "geo" / "GeoLite2-City.mmdb"
        )
        if mmdb_path.exists():
            app.state.geoip_reader = geoip2.database.Reader(str(mmdb_path))
            logger.info("GeoIP reader initialized")
        else:
            logger.warning("GeoIP mmdb not found at %s; location enrichment disabled", mmdb_path)
    except Exception as e:
        logger.warning("GeoIP init skipped: %s", e)

    # Milvus: single MilvusClient instance (async REST via httpx)
    if s.MILVUS_URI:
        try:
            milvus_client = MilvusClient(app_settings)
            await milvus_client.connect()
            set_milvus(milvus_client)
            logger.info("Milvus client initialized")
        except Exception as e:
            logger.warning("Milvus init skipped: %s", e)
    else:
        logger.info("Milvus skipped (no MILVUS_URI)")

    # Checkpointer: same pattern as before — use POSTGRES_URL or CHECKPOINTER_URL, yield inside pool context
    scheduler_task = None
    postgres_url = s.CHECKPOINTER_URL or s.POSTGRES_URL
    if postgres_url and s.CHECKPOINT_TYPE != "memory":
        try:
            async with AsyncConnectionPool(
                conninfo=postgres_url,
                kwargs={"autocommit": True},
            ) as pool:
                checkpointer = AsyncPostgresSaver(pool)
                await checkpointer.setup()
                logger.info("PostgreSQL checkpointer initialized successfully")
                app.state.checkpointer = checkpointer
                try:
                    init_agent(checkpointer)
                    init_channel_runtime(checkpointer)
                    init_copilot()
                    logger.info("Agents initialized successfully")
                except Exception as e:
                    logger.error("Failed to setup agent: %s", e)
                    app.state.checkpointer = memory_saver
                    init_agent(memory_saver)
                    init_channel_runtime(memory_saver)
                    init_copilot()
                warmup_service = _get_milvus_warmup_service(app)
                if warmup_service is not None:
                    scheduler_task = app.state.milvus_warmup_scheduler_task = asyncio.create_task(
                        warmup_service.run_scheduler_loop(),
                        name="milvus-warmup-scheduler",
                    )
                start_consumer()
                yield
        except Exception as e:
            logger.error("Failed to initialize PostgreSQL checkpointer: %s", e)
            logger.info("Falling back to MemorySaver")
            app.state.checkpointer = memory_saver
            init_agent(memory_saver)
            init_channel_runtime(memory_saver)
            init_copilot()
            warmup_service = _get_milvus_warmup_service(app)
            if warmup_service is not None:
                scheduler_task = app.state.milvus_warmup_scheduler_task = asyncio.create_task(
                    warmup_service.run_scheduler_loop(),
                    name="milvus-warmup-scheduler",
                )
            start_consumer()
            yield
    else:
        logger.info("PostgreSQL URL not configured - using MemorySaver")
        app.state.checkpointer = memory_saver
        init_agent(memory_saver)
        init_channel_runtime(memory_saver)
        init_copilot()
        warmup_service = _get_milvus_warmup_service(app)
        if warmup_service is not None:
            scheduler_task = app.state.milvus_warmup_scheduler_task = asyncio.create_task(
                warmup_service.run_scheduler_loop(),
                name="milvus-warmup-scheduler",
            )
        start_consumer()
        yield

    # Shutdown: stop channel bus subscriber, then close infra clients
    if scheduler_task is not None:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task

    warmup_service = _get_milvus_warmup_service(app)
    if warmup_service is not None:
        await warmup_service.shutdown()

    await stop_consumer()

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

    try:
        milvus_client = get_milvus()
        clear_milvus()
        await milvus_client.disconnect()
    except RuntimeError:
        pass
    except Exception as e:
        logger.debug("Milvus disconnect: %s", e)

    # Close GeoIP reader
    try:
        geoip_reader = getattr(app.state, "geoip_reader", None)
        if geoip_reader is not None:
            geoip_reader.close()
    except Exception as e:
        logger.debug("GeoIP close: %s", e)

    logger.info("Infrastructure clients closed")
