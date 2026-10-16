# Build context = repository root (see docker-compose build.context).
# This image contains both API packages; Kubernetes/compose choose the process with command/args.

FROM public.ecr.aws/docker/library/python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    libffi-dev \
    && pip install uv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Workspace root (members: agent, shared, workflow)
COPY pyproject.toml uv.lock* README.md /app/
COPY packages/shared /app/packages/shared
COPY packages/agent /app/packages/agent
COPY packages/workflow /app/packages/workflow

RUN uv venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
RUN uv sync --frozen --all-packages --no-install-package milvus-lite

FROM public.ecr.aws/docker/library/python:3.12-slim AS prod

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv .venv
ENV PATH="/app/.venv/bin:$PATH"

# Needed at runtime when workspace deps are installed in editable mode
COPY packages/shared/common /app/common

# Application code (shared package is already installed in the venv from the builder)
COPY packages/agent/app /app/app
COPY packages/workflow/workflow_service /app/workflow_service

EXPOSE 9005 9006

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9005"]
