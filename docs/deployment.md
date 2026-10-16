# Deployment

## Docker

### Building the image

```bash
docker build -t langgraph-agent-backend .
```

The `Dockerfile` is a multi-stage build:

| Stage | Base | Purpose |
|---|---|---|
| `builder` | `python:3.12-slim` | Install deps with `uv` |
| `prod` | `python:3.12-slim` | Copy `.venv` + source, run uvicorn |

The production image:
- Runs uvicorn on port **9005**
- Health check: `curl http://0.0.0.0:9005/health`
- Does not include build tools or `uv` itself

### Running locally with compose

```bash
docker compose up
```

`docker-compose.yml` starts the `app` service and mounts `.env`. For WooCommerce-specific local testing:

```bash
docker compose -f docker-compose.woocommerce.yml up
```

## CI/CD

Two GitHub Actions workflows live in `.github/workflows/`.

### `unit-test.yml` — runs on every PR to `staging`

1. Checkout code
2. Install Python 3.11 + uv
3. Configure git credentials for the private `tools` package
4. `uv sync` (cached `.venv`)
5. `pytest tests/unit --cov=app --cov-fail-under=0`

### `build-and-push.yml` — runs on push to `main` or `staging`

| Job | What it does |
|---|---|
| `setup` | Determines environment (`prod` / `staging`), looks up AWS account and IAM role |
| `build` | Builds Docker image for `linux/amd64` and `linux/arm64` in parallel, pushes to ECR |
| `manifest` | Creates and pushes a multi-arch manifest tag |
| `security-scan` | Trivy vulnerability scan (MEDIUM+ severity) — prod only |
| `deploy` | Updates the Helm values file in the K8s config repo, commits, and opens a PR (or commits directly to staging) |

### Environment promotion

```
feature branch → staging (PR + CI tests)
staging        → main    (manual merge; triggers prod build + deploy)
```

## Environment variables in production

Production secrets are managed outside this repository (AWS Secrets Manager / Kubernetes Secrets). The `.env.example` file documents all required variables. Never commit `.env` files.

Required variables for a production deployment:

```
ENVIRONMENT=production
OPENAI_API_KEY=...
JWT_SECRET=...
DATABASE_URL=...
CHECKPOINTER_URL=...
MILVUS_URI=...
MILVUS_TOKEN=...
TAVILY_API_KEY=...
SENTRY_DSN=...          # recommended for production
```

## Kubernetes

The service is deployed as a Kubernetes `Deployment` managed by Helm. The `deploy` CI job updates the image tag in the Helm values file in a separate K8s config repository. The specific cluster, namespace, and Helm chart details are in that repository.

## Ports and health

| Port | Protocol | Purpose |
|---|---|---|
| `9005` | HTTP | Main API |

Liveness probe: `GET /health` returns HTTP 200 while the process is serving.
Readiness probe: `GET /ready` returns HTTP 200 when startup and configured
dependencies are ready, or HTTP 503 when a required dependency check fails.

## Logging and observability

- **Structured logs** — stdout, collected by the cluster log aggregator
- **Sentry** — set `SENTRY_DSN` to enable error capture and performance tracing
- **Langfuse** — LLM trace/cost tracking; configure `LANGFUSE_*` variables
- **OpenTelemetry** — FastAPI + asyncpg + requests instrumented automatically; configure an OTLP exporter endpoint if needed

## Rollback

To roll back to a previous image, update the image tag in the Helm values file in the K8s config repo and apply. The previous Docker image layers remain in ECR.
