# Milvus Warm-Up Ops Plan

## Goal

Reduce first-retrieval latency spikes in `ai-backend` by adding a pod-local Milvus warm-up flow that:

- keeps `/health` cheap and deterministic
- warms the real dense and sparse retrieval path used by chat flows
- runs automatically on startup and periodically every 5 minutes per pod
- exposes results through Prometheus metrics
- allows manual triggering through an ops endpoint without coupling warm-up to health probes

This plan targets the current `ai-backend` Milvus implementation, which uses async REST calls via `httpx`, not the older `pymilvus` path.

## Problem Statement

The user-visible latency problem is specifically the first real retrieval search in stream chat flows, especially where both:

- dense retrieval: `OpenAI embedding -> Milvus dense search`
- sparse retrieval: `Milvus BM25 sparse search`

run together.

The current `/health` endpoint in [main.py](packages/packages/agent/app/main.py:73) only returns static JSON and must not become a blocking dependency check. Health probes from Kubernetes or uptime systems should not map 1:1 to live Milvus traffic.

## Agreed Constraints

- Do not use `/health` to run warm-up logic.
- Do not trace `/health`, `/ops/milvus-warmup`, or `/metrics`.
- Keep warm-up public for now.
- One pod currently runs one `uvicorn` process, so per-process metrics are sufficient.
- Warm-up should never fail app startup or fail the ops endpoint.
- Invalid or missing warm-up config should disable warm-up and log an error, not crash the service.

## Target Design

### 1. New Ops Module

Add a new module under `packages/agent/app/modules/ops/` containing:

- route for `POST /ops/milvus-warmup`
- shared warm-up service
- Prometheus metrics definitions
- config parsing helpers and response models if useful

Keep only:

- route registration in `main.py`
- scheduler startup/shutdown hookup in `lifecycle.py`

outside this module.

### 2. Warm-Up Trigger Endpoint

Add:

- `POST /ops/milvus-warmup`

Behavior:

- hidden from OpenAPI docs
- always returns `202 Accepted`
- returns scheduling state only, not fake latency
- supports `force=true`
- `force=true` bypasses only the stale-window throttle
- `force=true` does not bypass the `already_running` guard

Response shape:

```json
{
  "status": "scheduled",
  "forced": false,
  "environment": "staging",
  "worker_pid": 12345,
  "next_eligible_at": "2026-05-29T12:00:00Z",
  "last_started_at": "2026-05-29T11:55:00Z",
  "last_finished_at": "2026-05-29T11:55:02Z",
  "last_status": "success",
  "last_error": null
}
```

Allowed `status` values:

- `scheduled`
- `throttled`
- `already_running`
- `disabled`
- `misconfigured`

### 3. Automatic Scheduler

Start a long-lived `asyncio` task from FastAPI lifespan after shared infra initialization completes.

Scheduler behavior:

- run immediately once on startup
- continue every 5 minutes per pod
- use the same shared warm-up service as the manual endpoint
- behave like an internal forced trigger:
  - ignore stale-window gating
  - still respect `already_running`
- cancel cleanly on shutdown

This keeps warm-up aligned with real runtime state and prevents duplicate client initialization.

### 4. Shared Warm-Up Service

Create one shared service that owns:

- config resolution
- environment scope lookup
- stale-window throttle logic
- `in_progress` concurrency guard
- latest run state
- dense and sparse execution
- metrics emission
- structured logging

Manual and periodic triggers must call this same service so behavior does not drift.

### 5. Warm-Up Path

Warm the real retrieval path, not just raw Milvus metadata endpoints.

The warm-up run should:

1. Resolve current environment using `app_settings.resolved_environment_label()`.
2. Resolve the configured scope for that environment.
3. Run dense and sparse retrieval concurrently.
4. Record individual durations plus combined wall-clock duration.
5. Classify the result as `success`, `partial`, or `error`.

Dense warm-up must use the same retrieval service path used by production:

- `RetrievalService.retrieve(...)`

Sparse warm-up must use:

- `RetrievalService.retrieve_sparse(...)`

The warm-up scope should match production filter shape as closely as possible by using:

- `folder_ids=[association_id]`
- `entity_ids=[configured entity ids]`
- `collection_name="b2c"`

Use one shared synthetic query for both environments.

### 6. Concurrency Rules

Only one warm-up run may be in flight per pod.

Use a pod-local async lock and in-memory state so:

- if a run is active, new requests return `already_running`
- overlapping periodic and manual runs collapse into one active run

This is required so warm-up traffic does not distort its own latency metrics.

### 7. Run Classification

Support these overall run states:

- `success`: dense and sparse both succeeded
- `partial`: one succeeded and one failed
- `error`: both failed

Keep dense and sparse metrics separate even when the combined run is only `partial`.

## Configuration

Add new settings in `app.core.config.AppSettings`:

- `MILVUS_WARMUP_ENABLED`
- `MILVUS_WARMUP_QUERY`
- `MILVUS_WARMUP_TTL_SECONDS`
- `MILVUS_WARMUP_SCOPES_JSON`

Expected behavior:

- warm-up is enabled only when `MILVUS_WARMUP_ENABLED=true`
- and the current environment has a valid entry in `MILVUS_WARMUP_SCOPES_JSON`

JSON shape:

```json
{
  "staging": {
    "association_id": "bafebe97-ff0d-4a06-9bc6-ddc5b74f80b6",
    "entity_ids": [
      "eabbde30-89e6-4350-884b-6b229bef13d2",
      "e788ca9b-0c71-4101-b92e-3f8c96d5cb7d"
    ]
  },
  "production": {
    "association_id": "bafebe97-ff0d-4a06-9bc6-ddc5b74f80b6",
    "entity_ids": [
      "b67daa2f-f16b-4198-8af6-3fb24468e98c",
      "0530ac0c-cc6a-414d-a3d6-ebd00a7259cd"
    ]
  }
}
```

If the JSON is invalid or the environment key is missing:

- warm-up should be treated as `disabled` or `misconfigured`
- the service should log the problem
- app startup must continue

## Metrics

Add Prometheus support to `agent-service` using the official Python client and mount `/metrics`.

Expose:

- `ai_milvus_warmup_runs_total{environment,warmup_type,status}`
- `ai_milvus_warmup_duration_seconds{environment,warmup_type,status}`
- `ai_milvus_warmup_last_success_timestamp_seconds{environment,warmup_type}`
- `ai_milvus_warmup_last_duration_seconds{environment,warmup_type}`
- `ai_milvus_warmup_in_progress{environment}`
- `ai_milvus_warmup_last_status{environment,warmup_type}`

Recommended labeling:

- `environment`: exact resolved runtime environment string
- `warmup_type`: `milvus_dense`, `milvus_sparse`, `milvus_warmup_total`
- `status`: `success`, `partial`, `error`

For combined metrics:

- measure wall-clock duration for the concurrent dense+sparse batch
- do not sum dense and sparse durations

This matches user-perceived latency in the real chat path.

## Tracing And Logging

### Tracing

Keep these endpoints excluded from tracing:

- `/health`
- `/ops/milvus-warmup`
- `/metrics`

The warm-up implementation itself may still use structured logs, but should not add trace noise to the ops surface.

### Logging

On warm-up failure or partial failure, log structured fields including:

- `environment`
- `warmup_type`
- `query`
- `association_id`
- `entity_ids`
- `duration_ms`
- `error`

These logs are the debugging source when metrics show regressions.

## Implementation Steps

1. Add warm-up settings to `AppSettings`, including JSON parsing helpers.
2. Add Prometheus dependency to `packages/agent/pyproject.toml`.
3. Create `modules/ops/` route and service.
4. Mount `/metrics` in `main.py`.
5. Exclude `/ops/milvus-warmup` and `/metrics` from tracing filters.
6. Implement warm-up state machine:
   - enabled/disabled
   - stale-window
   - in-progress
   - latest state snapshot
7. Implement dense and sparse warm-up execution with `asyncio.gather(...)`.
8. Emit separate and combined metrics.
9. Add periodic scheduler startup in `lifespan`.
10. Add tests for config parsing, throttling, concurrency, endpoint responses, and metrics emission.

## Acceptance Criteria

- `/health` remains static and does not call Milvus.
- `POST /ops/milvus-warmup` always returns `202 Accepted`.
- `POST /ops/milvus-warmup` returns `already_running` when a run is active.
- `force=true` bypasses throttle but not concurrency protection.
- startup triggers one warm-up run automatically.
- the pod continues to trigger warm-up every 5 minutes.
- warm-up uses the real retrieval service path for both dense and sparse.
- `/metrics` exposes separate dense, sparse, and combined warm-up metrics.
- `/ops/milvus-warmup` and `/metrics` are excluded from tracing.
- invalid warm-up JSON does not prevent app startup.
- missing environment scope disables warm-up cleanly.
- combined run status is `success`, `partial`, or `error` according to branch outcomes.

## Rollout

1. Add config values for staging.
2. Deploy to staging.
3. Confirm startup triggers one warm-up run.
4. Scrape `/metrics` and verify:
   - counters increment
   - duration series appear
   - last success timestamp updates
   - in-progress toggles correctly
5. Manually hit `POST /ops/milvus-warmup` and verify:
   - `202 Accepted`
   - state response matches expected scheduling behavior
   - metrics update after background completion
6. Confirm `/health` trace and latency behavior remains unchanged.
7. Promote to production with production scope config.

## Risks And Non-Goals

### Risks

- The dense path still includes OpenAI embeddings, so not all latency spikes are Milvus-only.
- Public ops endpoint is an abuse surface until auth is added.
- Warm-up can mask but not eliminate deeper retrieval regressions.

### Non-Goals

- no cross-pod coordination
- no `/health` dependency checks
- no startup failure on warm-up misconfiguration
- no per-request tracing for ops endpoints
- no multiprocess Prometheus support in v1, because current deployment is one process per pod
