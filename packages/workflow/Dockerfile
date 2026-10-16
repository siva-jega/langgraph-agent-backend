# Build context = repository root. Minimal COPY: shared + workflow + enough of agent for workspace lockfile resolution.

FROM public.ecr.aws/docker/library/python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    libffi-dev \
    && pip install uv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock* README.md /app/
COPY packages/shared /app/packages/shared
COPY packages/workflow /app/packages/workflow
COPY packages/agent/pyproject.toml /app/packages/agent/pyproject.toml
COPY packages/agent/app /app/packages/agent/app

RUN uv venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
RUN uv lock --upgrade-package tools || true
RUN uv sync --frozen --package workflow-service --no-install-package milvus-lite

FROM public.ecr.aws/docker/library/python:3.12-slim AS prod

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv .venv
ENV PATH="/app/.venv/bin:$PATH"

# Needed at runtime when workspace deps are installed in editable mode
COPY packages/shared/common /app/common

COPY packages/workflow/workflow_service /app/workflow_service

HEALTHCHECK --interval=120s --timeout=3s \
    CMD curl -f http://0.0.0.0:9006/health || exit 1

EXPOSE 9006

CMD ["uvicorn", "workflow_service.main:app", "--host", "0.0.0.0", "--port", "9006"]
