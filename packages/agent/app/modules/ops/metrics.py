"""Prometheus metrics for Milvus warm-up operations."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

WARMUP_TYPE_DENSE = "milvus_dense"
WARMUP_TYPE_SPARSE = "milvus_sparse"
WARMUP_TYPE_TOTAL = "milvus_warmup_total"

_RUNS_TOTAL = Counter(
    "ai_milvus_warmup_runs_total",
    "Total number of Milvus warm-up runs by environment, type, and status.",
    ("environment", "warmup_type", "status"),
)

_DURATION_SECONDS = Histogram(
    "ai_milvus_warmup_duration_seconds",
    "Milvus warm-up duration by environment, type, and status.",
    ("environment", "warmup_type", "status"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
)

_LAST_SUCCESS_TIMESTAMP_SECONDS = Gauge(
    "ai_milvus_warmup_last_success_timestamp_seconds",
    "Unix timestamp of the last successful Milvus warm-up by environment and type.",
    ("environment", "warmup_type"),
)

_LAST_DURATION_SECONDS = Gauge(
    "ai_milvus_warmup_last_duration_seconds",
    "Duration in seconds of the latest Milvus warm-up by environment and type.",
    ("environment", "warmup_type"),
)

_IN_PROGRESS = Gauge(
    "ai_milvus_warmup_in_progress",
    "Whether a Milvus warm-up is currently running in this process (1 or 0).",
    ("environment",),
)

_LAST_STATUS = Gauge(
    "ai_milvus_warmup_last_status",
    "Latest Milvus warm-up status code by environment and type (1=success, 0.5=partial, 0=error).",
    ("environment", "warmup_type"),
)


def _status_code(status: str) -> float:
    if status == "success":
        return 1.0
    if status == "partial":
        return 0.5
    return 0.0


def set_in_progress(environment: str, value: bool) -> None:
    _IN_PROGRESS.labels(environment=environment).set(1 if value else 0)


def record_run(environment: str, warmup_type: str, status: str, duration_seconds: float) -> None:
    _RUNS_TOTAL.labels(
        environment=environment,
        warmup_type=warmup_type,
        status=status,
    ).inc()
    _DURATION_SECONDS.labels(
        environment=environment,
        warmup_type=warmup_type,
        status=status,
    ).observe(duration_seconds)
    _LAST_DURATION_SECONDS.labels(
        environment=environment,
        warmup_type=warmup_type,
    ).set(duration_seconds)
    _LAST_STATUS.labels(
        environment=environment,
        warmup_type=warmup_type,
    ).set(_status_code(status))
    if status == "success":
        _LAST_SUCCESS_TIMESTAMP_SECONDS.labels(
            environment=environment,
            warmup_type=warmup_type,
        ).set_to_current_time()
