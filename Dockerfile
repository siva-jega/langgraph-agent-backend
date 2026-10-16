# Unified Dockerfile for agent-service and workflow-service.
# Build context = repository root.
# Entrypoint is set at runtime via docker-compose command override.

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
COPY packages/agent /app/packages/agent
COPY packages/workflow /app/packages/workflow

RUN uv venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
RUN uv lock --upgrade-package tools || true
RUN uv sync --frozen --package agent-service --package workflow-service --no-install-package milvus-lite

FROM public.ecr.aws/docker/library/python:3.12-slim AS prod

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv .venv
ENV PATH="/app/.venv/bin:$PATH"

# Needed at runtime when workspace deps are installed in editable mode
COPY packages/shared/common /app/common

# Both application code dirs
COPY packages/agent/app /app/app
COPY packages/workflow/workflow_service /app/workflow_service

EXPOSE 9005 9006
