# LangGraph Agent Backend

A production-grade AI backend built with **FastAPI + LangGraph** that powers customer-facing chat agents, an internal Copilot, and a rich tool ecosystem with 20+ third-party integrations.

## What it does

- Streams LLM-powered agent responses over SSE (Server-Sent Events)
- Persists conversation history in PostgreSQL via LangGraph checkpointing
- Executes real-world actions (CRM lookups, email, calendar, e-commerce) through a pluggable tool registry
- Supports Human-in-the-Loop (HITL) pause/resume for sensitive operations
- Provides a hybrid RAG pipeline (dense + sparse + rerank) backed by Milvus

## Documentation

| Document | Description |
|---|---|
| [Architecture](docs/architecture.md) | System components, data flow, design decisions |
| [Local Setup](docs/setup.md) | Clone, configure, and run the service locally |
| [API Reference](docs/api.md) | HTTP endpoints, request/response schemas, SSE protocol |
| [Agent Flow](docs/agent-flow.md) | LangGraph ReAct loop, HITL, streaming internals |
| [Tool Providers](docs/tools-providers.md) | Tool registry, existing integrations, adding a new provider |
| [Infrastructure](docs/infrastructure.md) | Postgres, Redis, Milvus — clients and lifecycle |
| [Deployment](docs/deployment.md) | Docker, CI/CD pipelines, Kubernetes |
| [Contributing](CONTRIBUTING.md) | Branching, code style, testing, review process |

## Quick start

```bash
git clone https://github.com/siva-jega/langgraph-agent-backend
cd langgraph-agent-backend
cp .env.example .env      # fill in required values
docker compose up         # Postgres + Redis via compose; Milvus separate
./run.sh                  # starts uvicorn on :9005
python scripts/doctor.py --base-url http://127.0.0.1:9005
```

See [Local Setup](docs/setup.md) for the full walkthrough.

`/health` confirms the process is alive. `/ready` checks startup and configured
Postgres and Redis connections, plus Milvus client initialization. The doctor
command prints the results and exits with a nonzero status when the API is not
ready. Configure `READINESS_TIMEOUT_SECONDS` and `READINESS_CACHE_SECONDS` to
tune the dependency check timeout and cache duration.

## Tech stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI + uvicorn |
| Agent orchestration | LangGraph (ReAct graph) |
| Conversation persistence | PostgreSQL (`langgraph-checkpoint-postgres`) |
| Cache | Redis |
| Vector DB | Milvus / Zilliz |
| LLM providers | OpenAI, Google Gemini |
| Reranking | Cohere rerank-v3.5 |
| Observability | Sentry, Langfuse, OpenTelemetry |
