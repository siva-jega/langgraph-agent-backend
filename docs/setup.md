# Local Development Setup

## Requirements

| Tool | Version |
|---|---|
| Python | 3.11 or 3.12 |
| [uv](https://github.com/astral-sh/uv) | latest |
| Docker + Docker Compose | for Postgres and Redis |
| Milvus / Zilliz Cloud | vector database (see below) |

## Repository structure

This is a **uv workspace monorepo** with three packages:

```
ai-backend/
├── packages/
│   ├── shared/      # Shared library (importable as `common.*`)
│   ├── agent/       # Agent service — FastAPI on port 8000
│   └── workflow/    # Workflow service — FastAPI on port 8001
├── pyproject.toml   # Root workspace definition
├── uv.lock          # Single lockfile for all packages
└── docker-compose.yml
```

## 1. Clone and install dependencies

```bash
git clone https://github.com/siva-jega/langgraph-agent-backend
cd langgraph-agent-backend
uv sync          # creates .venv, installs all workspace packages from uv.lock
```

The `tools` package is sourced from a private git repository. `uv sync` resolves it via the lockfile; if you hit auth errors, ensure your SSH key or HTTPS credentials have access.

## 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in all required values. Key variables:

| Variable | Description |
|---|---|
| `ENVIRONMENT` | `development` or `production` |
| `OPENAI_API_KEY` | OpenAI API key for LLM + embeddings |
| `OPENAI_API_BASE` | LiteLLM or OpenAI-compatible base URL |
| `JWT_SECRET` | Secret for validating incoming JWTs |
| `DATABASE_URL` | asyncpg-compatible Postgres URL (app data) |
| `CHECKPOINTER_URL` | Postgres URL for LangGraph checkpointing (can be the same DB) |
| `MILVUS_URI` | Milvus / Zilliz endpoint |
| `MILVUS_TOKEN` | Milvus auth token |
| `TAVILY_API_KEY` | Web search tool |
| `NESTJS_URL` | NestJS user-backend URL (default: `http://localhost:3000`) |
| `NESTJS_INTERNAL_SECRET` | Must match user-backend `WS_INTERNAL_SECRET` |
| `SENTRY_DSN` | Optional; leave blank to disable Sentry |

Optional integrations (only needed if those tools will be tested):

```
FIRECRAWL_API_KEY
RIZA_API_KEY
CALCOM_CLIENT_ID / CALCOM_CLIENT_SECRET
AWS_ACCESS_KEY / AWS_KEY_SECRET / AWS_DEFAULT_REGION
WHATSAPP_SYSTEM_TOKEN
ENCRYPTION_KEY
INTERNAL_WEBHOOK_SECRET
```

## 3. Start backing services

```bash
docker compose up -d    # Postgres + Redis
```

The compose file exposes Postgres on the default port and Redis on 6379. Update `DATABASE_URL` and `CHECKPOINTER_URL` to point at the compose Postgres instance.

### Milvus

Milvus is not included in `docker-compose.yml`. Options:

- **Zilliz Cloud** (recommended for dev): create a free cluster and copy the endpoint + token.
- **Local Milvus**: run `docker run -p 19530:19530 milvusdb/milvus` (standalone mode).

After connecting for the first time, bootstrap the collection:

```bash
uv run python scripts/milvus-script.py
```

This creates the collection with the correct schema (3072-dim dense + sparse BM25 fields).

## 4. Run the services

### Both services at once

```bash
./run.sh
```

This starts the workflow service in the background and the agent service in the foreground.

### Individual services

**Agent service** (port 8000):

```bash
./packages/agent/run.sh
```

Runs `uv run --package agent-service uvicorn app.main:app --port 8000 --reload`.

**Workflow service** (port 8001):

```bash
./packages/workflow/run.sh
```

Runs `uv run --package workflow-service uvicorn workflow_service.main:app --port 8001 --reload`.

### Using Docker Compose

To run both services as containers:

```bash
docker compose up --build
```

This builds and starts:
- **agent** on port 9005
- **workflow** on port 9006

## 5. Verify

```bash
# Agent service
curl http://localhost:8000/health
# {"status": "ok"}

curl http://localhost:8000/api/tools
# {"providers": ["web", "shopify", ...], "tools": [...]}

# Workflow service
curl http://localhost:8001/health
```

Interactive docs: `http://localhost:8000/docs` (disabled in production).

## Running tests

```bash
uv run pytest                      # all tests (agent package)
uv run pytest packages/agent/tests # explicit path
```

Test configuration is in the root `pyproject.toml` under `[tool.pytest.ini_options]`.

## Common issues

### `asyncpg` connection refused
Postgres is not running or `DATABASE_URL` is wrong. Confirm with `docker ps` and check the port.

### `MilvusException: collection not found`
Run the bootstrap script: `uv run python scripts/milvus-script.py`.

### `tools` package import error
The `tools` package is a private dependency. Ensure your git credentials are configured and the lockfile resolves it. Run `uv sync --reinstall-package tools` to force a re-fetch.

### LangGraph checkpointer fails to connect
`CHECKPOINTER_URL` must point to a running Postgres with the LangGraph schema initialized. On first run, LangGraph auto-creates its tables if the user has DDL permissions.

### Redis `ConnectionError`
Confirm Redis is running (`docker ps`) and the `REDIS_URL` or equivalent env var is set correctly.

### Wrong package context
If you get import errors when running a service, make sure you're using `uv run --package <package-name>` (the `run.sh` scripts handle this automatically).
