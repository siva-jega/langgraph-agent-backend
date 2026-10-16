#!/bin/bash
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 1

uv run --package workflow-service uvicorn workflow_service.main:app --host 0.0.0.0 --port 8001 --reload
