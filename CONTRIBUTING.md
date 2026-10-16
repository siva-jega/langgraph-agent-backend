# Contributing to LangGraph Agent Backend

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- Docker (for Postgres and Redis locally)
- Access to a Milvus / Zilliz instance
- API keys listed in `.env.example`

## Branching

| Branch | Purpose |
|---|---|
| `main` | Production |
| `staging` | Pre-production; PRs target this branch |
| `feature/*` | New features |
| `fix/*` | Bug fixes |

Open PRs against `staging`. CI runs unit tests automatically. Merges to `main` trigger a Docker build and Helm deploy.

## Local setup

```bash
git clone https://github.com/siva-jega/langgraph-agent-backend
cd ai-backend
cp .env.example .env
# edit .env with real values

docker compose up -d        # Postgres + Redis
./run.sh                    # uvicorn reload on :9005
```

Full setup details: [docs/setup.md](docs/setup.md).

## Running tests

```bash
uv run pytest tests/unit --cov=app
```

Tests live in `tests/unit/`. Integration tests are not yet automated — test manually against a staging environment.

## Code style

- Formatter: **ruff** (via pyproject.toml)
- Type hints required on all public functions
- Pydantic models for all HTTP request/response schemas
- No bare `except` clauses; catch specific exceptions

## Adding a feature

1. Read [Architecture](docs/architecture.md) to understand where the change belongs.
2. For a new tool integration, follow [Tool Providers](docs/tools-providers.md).
3. For a new API endpoint, add a route in the relevant module's `route.py` and register it in `app/modules/routes.py`.
4. Write unit tests for any new service/utility logic.
5. Update the relevant `docs/` page if the public interface changes.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(tools): add HubSpot deal creation tool
fix(agents): retry on 429 from OpenAI
docs: add Milvus setup steps
```

## Environment variables

All config lives in `app/core/config.py` (`AppSettings`). Add new variables there with a sensible default or mark them required. Never hard-code secrets.

## Review checklist

- [ ] No secrets in code or logs
- [ ] New env vars documented in `.env.example`
- [ ] Unit tests pass (`pytest tests/unit`)
- [ ] Relevant doc page updated
