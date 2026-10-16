"""Infrastructure clients: Redis, Postgres, Milvus. Set at startup via app lifespan."""

from common.infrastructure.redis import RedisClient, get_redis
from common.infrastructure.postgres import PostgresClient, get_pg
from common.infrastructure.milvus import MilvusClient, get_milvus

__all__ = [
    "RedisClient",
    "PostgresClient",
    "MilvusClient",
    "get_redis",
    "get_pg",
    "get_milvus",
]
