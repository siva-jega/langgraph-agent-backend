"""HTTP trigger for eval runs.

``POST /evals/runs`` kicks off an eval against a LangSmith dataset; the run
executes in a background task and the LangSmith experiment URL is logged.
``GET /evals/runs/{run_id}`` returns the run's current status from an in-memory
registry (fine for v1; promote to Redis later if there's a need to survive
process restarts).

Auth: ``x-internal-secret`` header matching the existing
``resolved_nestjs_internal_secret()`` — same shared secret used by
``product_faq_generation/route.py`` so LangSmith webhooks, the GitHub Action,
and curl-from-an-engineer's-laptop all use one credential pair.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, Field

from common.core.nestjs_staging_defaults import resolved_nestjs_internal_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evals", tags=["evals"])


# ---------------------------------------------------------------------------
# In-memory run registry
# ---------------------------------------------------------------------------


RunStatus = Literal["pending", "running", "succeeded", "failed"]


class _RunRecord:
    """Lightweight status carrier — process-local, no persistence."""

    def __init__(self, dataset: str, experiment_prefix: str):
        self.id = uuid.uuid4().hex
        self.dataset = dataset
        self.experiment_prefix = experiment_prefix
        self.status: RunStatus = "pending"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at: Optional[str] = None
        self.experiment_url: Optional[str] = None
        self.error: Optional[str] = None


_RUN_REGISTRY: dict[str, _RunRecord] = {}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TriggerEvalBody(BaseModel):
    dataset: str = Field(..., min_length=1, description="LangSmith dataset name")
    experiment_prefix: str = Field(default="eval", min_length=1)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra experiment metadata. Stamped on the LangSmith experiment alongside prompt_builder_version.",
    )
    is_preview: bool = Field(
        default=False,
        description="Forwards to Graph.run_non_streaming. False = full path against the staging eval account.",
    )
    no_judges: bool = Field(
        default=False,
        description="Skip RAGAS/programmatic scoring; useful for smoke runs.",
    )
    routing_only: bool = Field(
        default=False,
        description="Score with the trajectory judge alone (tool-routing skill datasets). "
        "Ignored when no_judges is set.",
    )
    explain_tool_choice: bool = Field(
        default=False,
        description="Add tool_selection_reasoning + prompt_rule_followed columns by asking the "
        "agent's own model why it chose each tool. One extra model call per case; opt-in.",
    )


class TriggerEvalResponse(BaseModel):
    run_id: str
    dataset: str
    status: RunStatus
    started_at: str


class RunStatusResponse(BaseModel):
    run_id: str
    dataset: str
    experiment_prefix: str
    status: RunStatus
    started_at: str
    finished_at: Optional[str] = None
    experiment_url: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_internal_secret(request: Request) -> None:
    """Same auth model as ``product_faq_generation/route.py`` — one shared
    secret for all internal/webhook callers."""
    expected = (resolved_nestjs_internal_secret() or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Internal auth not configured")
    got = (request.headers.get("x-internal-secret") or "").strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _experiment_url(client, experiment_name: str) -> Optional[str]:
    """Best-effort URL builder — LangSmith ``aevaluate`` doesn't surface the
    experiment id directly, so we construct the URL from project + name. If
    the SDK changes shape, log and move on."""
    try:
        from app.core.config import app_settings

        endpoint = app_settings.LANGSMITH_ENDPOINT.rstrip("/")
        # API endpoint -> web URL conversion
        web = endpoint.replace("api.smith.langchain.com", "smith.langchain.com")
        project = app_settings.LANGSMITH_PROJECT
        # Experiment names appear under /projects/.../experiments
        return f"{web}/o/-/projects/p/{project}/experiments?search={experiment_name}"
    except Exception:  # noqa: BLE001
        return None


async def _run_eval_in_background(record: _RunRecord, body: TriggerEvalBody) -> None:
    """Wraps ``run_dataset`` with status bookkeeping."""
    from app.modules.evals.runner import run_dataset

    record.status = "running"
    try:
        if body.no_judges:
            evaluators: list | None = []
        elif body.routing_only:
            from app.modules.evals.judges import routing_judges

            evaluators = routing_judges()  # trajectory only — skill routing datasets
        else:
            evaluators = None
        result = await run_dataset(
            body.dataset,
            experiment_prefix=body.experiment_prefix,
            evaluators=evaluators,
            metadata=body.metadata,
            is_preview=body.is_preview,
            explain_tool_choice=body.explain_tool_choice,
        )
        record.status = "succeeded"
        # If the SDK gave back an experiment name / id, surface it; otherwise
        # the URL builder falls back to a search URL.
        experiment_name = getattr(result, "experiment_name", None) or body.experiment_prefix
        record.experiment_url = _experiment_url(None, experiment_name)
        logger.info(
            "eval run %s succeeded (dataset=%s experiment=%s)",
            record.id, body.dataset, experiment_name,
        )
    except Exception as e:  # noqa: BLE001
        record.status = "failed"
        record.error = f"{type(e).__name__}: {e}"
        logger.exception("eval run %s failed (dataset=%s)", record.id, body.dataset)
    finally:
        record.finished_at = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=TriggerEvalResponse)
async def trigger_eval(
    body: TriggerEvalBody,
    background_tasks: BackgroundTasks,
    request: Request,
) -> TriggerEvalResponse:
    """Kick off an eval. Returns immediately with ``202`` + a run_id.

    Poll ``GET /evals/runs/{run_id}`` for status. The LangSmith experiment URL
    appears on the status response once the run finishes.
    """
    _require_internal_secret(request)

    record = _RunRecord(dataset=body.dataset, experiment_prefix=body.experiment_prefix)
    _RUN_REGISTRY[record.id] = record

    # Run in the background so the caller doesn't block. ``BackgroundTasks``
    # is FastAPI's primitive for fire-and-forget work tied to the request.
    background_tasks.add_task(_run_eval_in_background, record, body)

    logger.info(
        "eval run %s queued (dataset=%s experiment_prefix=%s)",
        record.id, body.dataset, body.experiment_prefix,
    )
    return TriggerEvalResponse(
        run_id=record.id,
        dataset=record.dataset,
        status=record.status,
        started_at=record.started_at,
    )


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_eval_run(run_id: str, request: Request) -> RunStatusResponse:
    """Look up the status of a previously-triggered eval run."""
    _require_internal_secret(request)

    record = _RUN_REGISTRY.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

    return RunStatusResponse(
        run_id=record.id,
        dataset=record.dataset,
        experiment_prefix=record.experiment_prefix,
        status=record.status,
        started_at=record.started_at,
        finished_at=record.finished_at,
        experiment_url=record.experiment_url,
        error=record.error,
    )


# ---------------------------------------------------------------------------
# Test helpers — never imported from production code paths.
# ---------------------------------------------------------------------------


def _reset_registry_for_tests() -> None:
    _RUN_REGISTRY.clear()
