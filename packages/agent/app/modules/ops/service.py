"""Shared Milvus warm-up service for manual ops triggers and background scheduling."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Literal

from app.core.config import AppSettings
from app.modules.ops import metrics
from common.integration.registry import get_retrieval

logger = logging.getLogger(__name__)

COLLECTION_NAME = "b2c"
RUN_STATUS_NEVER = "never"

RunStatus = Literal["success", "partial", "error", "never"]


@dataclass(frozen=True)
class WarmupScope:
    association_id: str
    entity_ids: tuple[str, ...]


@dataclass(frozen=True)
class WarmupBranchResult:
    warmup_type: str
    status: Literal["success", "error"]
    duration_seconds: float
    error: str | None = None


@dataclass(frozen=True)
class WarmupScheduleSnapshot:
    status: str
    forced: bool
    environment: str
    worker_pid: int
    next_eligible_at: datetime | None
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_status: RunStatus
    last_error: str | None


class MilvusWarmupService:
    """Owns Milvus warm-up config, scheduling, state, and metrics."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._state_lock = asyncio.Lock()
        self._pending_task: asyncio.Task[None] | None = None
        self._in_progress = False
        self._last_started_at: datetime | None = None
        self._last_finished_at: datetime | None = None
        self._last_status: RunStatus = RUN_STATUS_NEVER
        self._last_error: str | None = None

    async def request_run(
        self,
        *,
        force: bool,
        source: str,
    ) -> WarmupScheduleSnapshot:
        """Schedule a warm-up run if eligible, otherwise return the current state."""
        environment = self._settings.resolved_environment_label()
        now = self._utcnow()

        async with self._state_lock:
            scope, issue_status, issue_error = self._resolve_scope(environment)
            if issue_status is not None:
                if issue_error:
                    self._log_config_issue(environment, issue_status, issue_error, source)
                return self._snapshot(
                    status=issue_status,
                    forced=force,
                    environment=environment,
                    last_error=issue_error if issue_status == "misconfigured" else self._last_error,
                    now=now,
                )

            if self._has_active_task():
                return self._snapshot(
                    status="already_running",
                    forced=force,
                    environment=environment,
                    now=now,
                )

            if not force and not self._is_stale(now):
                return self._snapshot(
                    status="throttled",
                    forced=force,
                    environment=environment,
                    now=now,
                )

            self._pending_task = asyncio.create_task(
                self._run_once(scope=scope, environment=environment, source=source),
                name=f"milvus-warmup:{environment}:{source}",
            )
            self._pending_task.add_done_callback(self._consume_task_exception)

            return self._snapshot(
                status="scheduled",
                forced=force,
                environment=environment,
                now=now,
            )

    async def run_scheduler_loop(self) -> None:
        """Run warm-up once immediately, then every TTL seconds."""
        await self.request_run(force=True, source="startup")
        sleep_seconds = max(1, int(self._settings.MILVUS_WARMUP_TTL_SECONDS))
        while True:
            await asyncio.sleep(sleep_seconds)
            await self.request_run(force=True, source="periodic")

    async def shutdown(self) -> None:
        """Cancel any in-flight manual or periodic warm-up task."""
        task: asyncio.Task[None] | None = None
        async with self._state_lock:
            if self._pending_task and not self._pending_task.done():
                task = self._pending_task
                task.cancel()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def wait_for_pending(self) -> None:
        """Wait for the current warm-up task, if any. Mainly used by tests."""
        task = self._pending_task
        if task is not None:
            await asyncio.shield(task)

    def current_snapshot(
        self,
        *,
        status: str,
        forced: bool,
    ) -> WarmupScheduleSnapshot:
        """Return the current state with a caller-supplied scheduling status."""
        return self._snapshot(
            status=status,
            forced=forced,
            environment=self._settings.resolved_environment_label(),
            now=self._utcnow(),
        )

    async def _run_once(self, *, scope: WarmupScope, environment: str, source: str) -> None:
        started_at = self._utcnow()
        async with self._state_lock:
            self._in_progress = True
            self._last_started_at = started_at
            self._last_error = None
        metrics.set_in_progress(environment, True)

        query = (self._settings.MILVUS_WARMUP_QUERY or "").strip() or "black t-shirt"
        total_started = perf_counter()

        try:
            dense_result, sparse_result = await asyncio.gather(
                self._run_dense(environment=environment, scope=scope, query=query),
                self._run_sparse(environment=environment, scope=scope, query=query),
            )
            total_duration = perf_counter() - total_started
            overall_status = self._combine_status(dense_result, sparse_result)
            overall_error = self._combine_error(dense_result, sparse_result)

            metrics.record_run(
                environment=environment,
                warmup_type=metrics.WARMUP_TYPE_TOTAL,
                status=overall_status,
                duration_seconds=total_duration,
            )

            if overall_status != "success":
                logger.warning(
                    "Milvus warm-up completed with status=%s",
                    overall_status,
                    extra={
                        "environment": environment,
                        "warmup_type": metrics.WARMUP_TYPE_TOTAL,
                        "query": query,
                        "association_id": scope.association_id,
                        "entity_ids": list(scope.entity_ids),
                        "duration_ms": round(total_duration * 1000, 3),
                        "error": overall_error,
                        "source": source,
                    },
                )
            else:
                logger.info(
                    "Milvus warm-up completed successfully",
                    extra={
                        "environment": environment,
                        "warmup_type": metrics.WARMUP_TYPE_TOTAL,
                        "query": query,
                        "association_id": scope.association_id,
                        "entity_ids": list(scope.entity_ids),
                        "duration_ms": round(total_duration * 1000, 3),
                        "source": source,
                    },
                )

            async with self._state_lock:
                self._last_status = overall_status
                self._last_error = overall_error
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            total_duration = perf_counter() - total_started
            error_message = str(exc)
            metrics.record_run(
                environment=environment,
                warmup_type=metrics.WARMUP_TYPE_TOTAL,
                status="error",
                duration_seconds=total_duration,
            )
            logger.exception(
                "Milvus warm-up task crashed",
                extra={
                    "environment": environment,
                    "warmup_type": metrics.WARMUP_TYPE_TOTAL,
                    "query": query,
                    "association_id": scope.association_id,
                    "entity_ids": list(scope.entity_ids),
                    "duration_ms": round(total_duration * 1000, 3),
                    "error": error_message,
                    "source": source,
                },
            )
            async with self._state_lock:
                self._last_status = "error"
                self._last_error = error_message
        finally:
            finished_at = self._utcnow()
            metrics.set_in_progress(environment, False)
            async with self._state_lock:
                self._in_progress = False
                self._last_finished_at = finished_at

    async def _run_dense(
        self,
        *,
        environment: str,
        scope: WarmupScope,
        query: str,
    ) -> WarmupBranchResult:
        return await self._run_branch(
            environment=environment,
            warmup_type=metrics.WARMUP_TYPE_DENSE,
            scope=scope,
            query=query,
            invoke=lambda: get_retrieval().retrieve(
                text=query,
                entity_ids=list(scope.entity_ids),
                folder_ids=[scope.association_id],
                collection_name=COLLECTION_NAME,
            ),
        )

    async def _run_sparse(
        self,
        *,
        environment: str,
        scope: WarmupScope,
        query: str,
    ) -> WarmupBranchResult:
        return await self._run_branch(
            environment=environment,
            warmup_type=metrics.WARMUP_TYPE_SPARSE,
            scope=scope,
            query=query,
            invoke=lambda: get_retrieval().retrieve_sparse(
                text=query,
                entity_ids=list(scope.entity_ids),
                folder_ids=[scope.association_id],
                collection_name=COLLECTION_NAME,
            ),
        )

    async def _run_branch(
        self,
        *,
        environment: str,
        warmup_type: str,
        scope: WarmupScope,
        query: str,
        invoke,
    ) -> WarmupBranchResult:
        started = perf_counter()
        try:
            await invoke()
            duration = perf_counter() - started
            metrics.record_run(
                environment=environment,
                warmup_type=warmup_type,
                status="success",
                duration_seconds=duration,
            )
            return WarmupBranchResult(
                warmup_type=warmup_type,
                status="success",
                duration_seconds=duration,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            duration = perf_counter() - started
            error_message = str(exc)
            metrics.record_run(
                environment=environment,
                warmup_type=warmup_type,
                status="error",
                duration_seconds=duration,
            )
            logger.warning(
                "Milvus warm-up branch failed",
                extra={
                    "environment": environment,
                    "warmup_type": warmup_type,
                    "query": query,
                    "association_id": scope.association_id,
                    "entity_ids": list(scope.entity_ids),
                    "duration_ms": round(duration * 1000, 3),
                    "error": error_message,
                },
            )
            return WarmupBranchResult(
                warmup_type=warmup_type,
                status="error",
                duration_seconds=duration,
                error=error_message,
            )

    def _resolve_scope(
        self,
        environment: str,
    ) -> tuple[WarmupScope | None, Literal["disabled", "misconfigured"] | None, str | None]:
        if not self._settings.MILVUS_WARMUP_ENABLED:
            return None, "disabled", None

        raw = self._settings.MILVUS_WARMUP_SCOPES_JSON
        if not raw:
            return None, "misconfigured", "MILVUS_WARMUP_SCOPES_JSON is empty"

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, "misconfigured", f"MILVUS_WARMUP_SCOPES_JSON is invalid JSON: {exc}"

        if not isinstance(parsed, dict):
            return None, "misconfigured", "MILVUS_WARMUP_SCOPES_JSON must be a JSON object"

        env_config = parsed.get(environment)
        if env_config is None:
            return None, "disabled", f"No warm-up scope configured for environment '{environment}'"
        if not isinstance(env_config, dict):
            return None, "misconfigured", f"Warm-up scope for '{environment}' must be an object"

        association_id = str(env_config.get("association_id") or "").strip()
        raw_entity_ids = env_config.get("entity_ids")
        entity_ids = self._normalize_entity_ids(raw_entity_ids)

        if not association_id:
            return None, "misconfigured", f"Warm-up scope for '{environment}' is missing association_id"
        if not entity_ids:
            return None, "misconfigured", f"Warm-up scope for '{environment}' has no valid entity_ids"

        return WarmupScope(
            association_id=association_id,
            entity_ids=tuple(entity_ids),
        ), None, None

    @staticmethod
    def _normalize_entity_ids(raw_entity_ids: object) -> list[str]:
        if not isinstance(raw_entity_ids, list):
            return []
        normalized = []
        for item in raw_entity_ids:
            value = str(item).strip()
            if value:
                normalized.append(value)
        return normalized

    def _snapshot(
        self,
        *,
        status: str,
        forced: bool,
        environment: str,
        now: datetime,
        last_error: str | None = None,
    ) -> WarmupScheduleSnapshot:
        return WarmupScheduleSnapshot(
            status=status,
            forced=forced,
            environment=environment,
            worker_pid=os.getpid(),
            next_eligible_at=self._next_eligible_at(now),
            last_started_at=self._last_started_at,
            last_finished_at=self._last_finished_at,
            last_status=self._last_status,
            last_error=self._last_error if last_error is None else last_error,
        )

    def _next_eligible_at(self, now: datetime) -> datetime | None:
        if self._last_started_at is None:
            return now
        return self._last_started_at + timedelta(seconds=self._settings.MILVUS_WARMUP_TTL_SECONDS)

    def _is_stale(self, now: datetime) -> bool:
        next_eligible = self._next_eligible_at(now)
        if next_eligible is None:
            return True
        return now >= next_eligible

    def _has_active_task(self) -> bool:
        if self._in_progress:
            return True
        return self._pending_task is not None and not self._pending_task.done()

    @staticmethod
    def _combine_status(
        dense_result: WarmupBranchResult,
        sparse_result: WarmupBranchResult,
    ) -> Literal["success", "partial", "error"]:
        statuses = {dense_result.status, sparse_result.status}
        if statuses == {"success"}:
            return "success"
        if statuses == {"error"}:
            return "error"
        return "partial"

    @staticmethod
    def _combine_error(
        dense_result: WarmupBranchResult,
        sparse_result: WarmupBranchResult,
    ) -> str | None:
        errors = []
        if dense_result.error:
            errors.append(f"{dense_result.warmup_type}: {dense_result.error}")
        if sparse_result.error:
            errors.append(f"{sparse_result.warmup_type}: {sparse_result.error}")
        return "; ".join(errors) if errors else None

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _consume_task_exception(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unhandled exception in Milvus warm-up task")

    @staticmethod
    def _log_config_issue(
        environment: str,
        status: str,
        error: str,
        source: str,
    ) -> None:
        level = logging.WARNING if status == "misconfigured" else logging.INFO
        logger.log(
            level,
            "Milvus warm-up unavailable",
            extra={
                "environment": environment,
                "status": status,
                "error": error,
                "source": source,
            },
        )
