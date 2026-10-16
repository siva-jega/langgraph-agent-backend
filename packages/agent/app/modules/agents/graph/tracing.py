"""LangSmith tracing helpers — used by the **eval runner only**.

By design, the production agent path in ``graph.py`` does NOT auto-trace. We
don't want every staging / prod request hitting LangSmith — only eval runs
triggered by the team or CI/CD should be traced. The eval runner (Phase 2 of
EVAL_PLAN.md) imports these helpers and attaches the tracer when it invokes
``evaluate(predict_fn, ...)`` so each scored case has a clickable trace
alongside its scores.

If you ever want to trace live traffic too, add the callbacks list to
``graph.py``'s ``callbacks = [UsageMetadataCallbackHandler()]`` line — but
that's a separate decision from running evals.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from app.core.config import app_settings

logger = logging.getLogger(__name__)


def langsmith_enabled() -> bool:
    """True when both the flag and an API key are present.

    A flag-only opt-in (no key) is treated as "off" rather than a crash so a
    misconfigured local dev environment never blocks the agent.
    """
    if not app_settings.LANGSMITH_TRACING:
        return False
    api_key = app_settings.LANGSMITH_API_KEY or os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
    return bool(api_key)


def _propagate_env() -> None:
    """LangChain's tracer reads LANGCHAIN_* env vars. Mirror our settings into
    those names so the SDK picks them up without callers needing to know which
    var names are canonical.
    """
    if app_settings.LANGSMITH_API_KEY:
        os.environ.setdefault("LANGCHAIN_API_KEY", app_settings.LANGSMITH_API_KEY)
        os.environ.setdefault("LANGSMITH_API_KEY", app_settings.LANGSMITH_API_KEY)
    if app_settings.LANGSMITH_PROJECT:
        os.environ.setdefault("LANGCHAIN_PROJECT", app_settings.LANGSMITH_PROJECT)
        os.environ.setdefault("LANGSMITH_PROJECT", app_settings.LANGSMITH_PROJECT)
    if app_settings.LANGSMITH_ENDPOINT:
        os.environ.setdefault("LANGCHAIN_ENDPOINT", app_settings.LANGSMITH_ENDPOINT)
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")


def build_langsmith_callbacks(project: Optional[str] = None) -> list[Any]:
    """Return ``[LangChainTracer(...)]`` when tracing is on, else ``[]``.

    Safe to call unconditionally — empty list is a no-op for the graph's
    callback list. Imports are lazy so non-tracing runs don't pay the cost.
    """
    if not langsmith_enabled():
        return []
    try:
        _propagate_env()
        from langchain_core.tracers import LangChainTracer

        tracer = LangChainTracer(project_name=project or app_settings.LANGSMITH_PROJECT)
        return [tracer]
    except Exception as e:  # noqa: BLE001 — tracing failure must not break the agent
        logger.warning("LangSmith tracing setup failed (%s); continuing without it", e)
        return []


def build_trace_metadata(
    *,
    agent_id: Optional[str],
    chat_id: Optional[str],
    session_id: Optional[str],
    model: Optional[str],
    channel: Optional[str] = None,
    is_preview: bool = False,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the ``metadata`` dict surfaced on every LangSmith trace.

    These are the dimensions you'll filter by in the LangSmith UI: by agent,
    by model, by channel, by preview-vs-prod. Phase 4 will add
    ``prompt_builder_version`` + ``prompt_hash`` here.
    """
    md: dict[str, Any] = {
        "agent_id": agent_id or "",
        "chat_id": chat_id or "",
        "session_id": session_id or "",
        "model": model or "",
        "channel": channel or "web",
        "is_preview": bool(is_preview),
    }
    if extra:
        md.update(extra)
    # Strip empty values to keep the trace metadata tidy.
    return {k: v for k, v in md.items() if v not in ("", None)}
