# Infrastructure

The service uses three persistent stores, each wrapped in a singleton client initialized at startup and torn down on shutdown. All clients are async.

## Lifecycle

Initialization and teardown are managed in `app/core/lifecycle.py` via a FastAPI `lifespan` context manager:

```
startup:
  1. Create Redis client
  2. Create Postgres connection pool
  3. Connect Milvus client
  4. Load MaxMind GeoIP database
  5. Initialize LangGraph checkpointer (Postgres) → fall back to MemorySaver
  6. Compile agent and copilot graphs

shutdown:
  Close Redis, Postgres pool, Milvus client (in reverse order)
```

## Postgres

**Client:** `app/infrastructure/postgres.py` — `PostgresClient` wrapping `asyncpg.Pool`

**Usage:**

```python
from app.infrastructure.postgres import get_postgres_client

pg = get_postgres_client()
async with pg.acquire() as conn:
    rows = await conn.fetch("SELECT * FROM ...")
```

**Singleton API:**
- `set_postgres_client(pool)` — called at startup
- `get_postgres_client()` → `PostgresClient`
- `clear_postgres_client()` — called at shutdown

**Databases:**
- `DATABASE_URL` — application data (agents, chats, messages, credentials)
- `CHECKPOINTER_URL` — LangGraph conversation state (can be the same DB, different schema)

**Queries** live in `app/db/queries/*.sql` and are loaded as strings at runtime. This keeps SQL readable without an ORM. Key files:

| File | Purpose |
|---|---|
| `agent.sql` | Full agent config JOIN (agent + persona + apps + skills + knowledge) |
| `messages.sql` | Batch upsert conversation messages (JSONB) |
| `chat.sql` | Create or fetch a chat session |
| `usage.sql` | Deduct token credits from user balance |
| `app_credentials.sql` | Fetch encrypted OAuth credentials |
| `human_handoff.sql` | Mark chat as escalated to human |
| `lead.sql` | Insert a captured lead record |
| `ticket.sql` | Insert a support ticket |

## Redis

**Client:** `app/infrastructure/redis.py` — `RedisClient` wrapping `aioredis.Redis`

**Usage:**

```python
from app.infrastructure.redis import get_redis_client

redis = get_redis_client()
await redis.set_json("agent:config:abc123", config_dict, ttl=300)
value = await redis.get_json("agent:config:abc123")
```

**Singleton API:**
- `set_redis_client(client)` — called at startup
- `get_redis_client()` → `RedisClient`
- `clear_redis_client()` — called at shutdown

**Helper methods** (in addition to raw Redis commands):
- `set_json(key, value, ttl?)` / `get_json(key)` — automatic serialization
- Standard string, list, hash, set operations

**What is cached:**
- Agent configuration (key: `agent:config:<agent_id>`, TTL: 5 min)
- OAuth access tokens (key: `token:<agent_id>:<provider>`, TTL: dynamic)
- Any other hot-path read that hits Postgres

## Milvus

**Client:** `app/infrastructure/milvus.py` — `MilvusClient` wrapping `AsyncMilvusClient`

**Usage:**

```python
from app.infrastructure.milvus import get_milvus_client

milvus = get_milvus_client()
results = await milvus.hybrid_search(collection, dense_vector, sparse_vector, ...)
```

**Singleton API:**
- `set_milvus_client(client)` — called at startup
- `get_milvus_client()` → `MilvusClient`
- `clear_milvus_client()` — called at shutdown

**Collection schema** (created by `scripts/milvus-script.py`):
- `id`: primary key
- `dense`: 3072-dim float vector (OpenAI `text-embedding-3-large`, COSINE metric)
- `sparse`: sparse float vector (BM25)
- `text`: raw chunk text
- `entityId`: knowledge entity scope filter
- `associationId`: folder scope filter

**Search pipeline** (`app/modules/common/retrieval/service.py`):
1. Generate dense embedding via OpenAI API
2. Generate sparse BM25 vector
3. Filter by `entityId` and/or `associationId`
4. Run hybrid search (weighted fusion of dense + sparse scores)
5. Deduplicate by chunk ID
6. Optionally rerank with Cohere `rerank-v3.5`
7. Return top-k text chunks

**Bootstrap:** Run `scripts/milvus-script.py` once to create the collection. Only needed on a fresh Milvus instance.

## GeoIP

At startup, `lifecycle.py` loads `app/db/geo/GeoLite2-City.mmdb` (MaxMind binary database) into a `geoip2.database.Reader`. On the first message of a new chat, the client IP is resolved to `(latitude, longitude, country, city)` and stored on the `AgentChats` row.

No external call is made — the lookup is fully local.
