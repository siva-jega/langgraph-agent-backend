#!/bin/bash
ROOT="$(cd "$(dirname "$0")" && pwd)"

"$ROOT/packages/workflow/run.sh" &
exec "$ROOT/packages/agent/run.sh" "$@"
