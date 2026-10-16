#!/bin/bash

# Reduce gRPC/abseil noise (e.g. "Other threads are currently calling into gRPC, skipping fork() handlers")
export GRPC_VERBOSITY=ERROR
export GLOG_minloglevel=2

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 1

uv run --package agent-service uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
