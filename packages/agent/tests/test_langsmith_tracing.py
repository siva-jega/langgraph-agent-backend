"""Tests for the LangSmith tracing helpers.

These helpers are used by the eval runner only — the production agent graph
does NOT auto-trace. The runner imports them when it invokes ``evaluate(...)``
so each scored case has a clickable LangSmith trace alongside its scores.

When ``LANGSMITH_TRACING`` is off (the default), the helpers return no-op
values, so accidentally wiring them somewhere later won't surprise anyone.
"""

from __future__ import annotations

import pytest

from app.modules.agents.graph.tracing import (
    build_langsmith_callbacks,
    build_trace_metadata,
    langsmith_enabled,
)


# ---------------------------------------------------------------------------
# Default state — tracing off
# ---------------------------------------------------------------------------


def test_langsmith_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env / no flag, langsmith_enabled() must be False."""
    from app.core.config import app_settings

    monkeypatch.setattr(app_settings, "LANGSMITH_TRACING", False, raising=False)
    monkeypatch.setattr(app_settings, "LANGSMITH_API_KEY", None, raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    assert langsmith_enabled() is False


def test_build_langsmith_callbacks_returns_empty_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import app_settings

    monkeypatch.setattr(app_settings, "LANGSMITH_TRACING", False, raising=False)
    monkeypatch.setattr(app_settings, "LANGSMITH_API_KEY", None, raising=False)
    assert build_langsmith_callbacks() == []


def test_flag_only_without_key_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting the flag but forgetting the key must not crash — it stays off."""
    from app.core.config import app_settings

    monkeypatch.setattr(app_settings, "LANGSMITH_TRACING", True, raising=False)
    monkeypatch.setattr(app_settings, "LANGSMITH_API_KEY", None, raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    assert langsmith_enabled() is False
    assert build_langsmith_callbacks() == []


# ---------------------------------------------------------------------------
# Enabled state
# ---------------------------------------------------------------------------


def test_build_langsmith_callbacks_returns_tracer_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import app_settings

    monkeypatch.setattr(app_settings, "LANGSMITH_TRACING", True, raising=False)
    monkeypatch.setattr(app_settings, "LANGSMITH_API_KEY", "ls-test-key", raising=False)
    monkeypatch.setattr(app_settings, "LANGSMITH_PROJECT", "web-agent-test", raising=False)

    callbacks = build_langsmith_callbacks()
    assert len(callbacks) == 1
    # The tracer is a LangChainTracer instance; we don't import the class here
    # to avoid coupling the test to the import path, but it must expose the
    # project_name we passed in.
    assert getattr(callbacks[0], "project_name", None) == "web-agent-test"


def test_enabled_state_propagates_env_for_langchain_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LangChain's tracer reads LANGCHAIN_* env vars internally; the helper
    must mirror our settings into those names so the SDK picks them up."""
    import os

    from app.core.config import app_settings

    monkeypatch.setattr(app_settings, "LANGSMITH_TRACING", True, raising=False)
    monkeypatch.setattr(app_settings, "LANGSMITH_API_KEY", "ls-test-key", raising=False)
    monkeypatch.setattr(app_settings, "LANGSMITH_PROJECT", "web-agent-test", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)

    build_langsmith_callbacks()

    assert os.environ.get("LANGCHAIN_API_KEY") == "ls-test-key"
    assert os.environ.get("LANGCHAIN_PROJECT") == "web-agent-test"
    assert os.environ.get("LANGCHAIN_TRACING_V2") == "true"


# ---------------------------------------------------------------------------
# Trace metadata
# ---------------------------------------------------------------------------


def test_build_trace_metadata_strips_empty_fields() -> None:
    md = build_trace_metadata(
        agent_id="agent-1",
        chat_id=None,           # dropped
        session_id="",          # dropped
        model="gpt-4o-mini",
        channel=None,           # defaults to "web"
        is_preview=True,
    )
    assert md["agent_id"] == "agent-1"
    assert md["model"] == "gpt-4o-mini"
    assert md["channel"] == "web"
    assert md["is_preview"] is True
    assert "chat_id" not in md
    assert "session_id" not in md


def test_build_trace_metadata_accepts_extra_dimensions() -> None:
    """Phase 4 will pass prompt_builder_version + prompt_hash via ``extra``;
    the metadata helper must surface those without losing the canonical fields."""
    md = build_trace_metadata(
        agent_id="agent-1",
        chat_id="chat-1",
        session_id="sess-1",
        model="gpt-4o-mini",
        extra={"prompt_builder_version": "0.1.0", "prompt_hash": "abc123"},
    )
    assert md["prompt_builder_version"] == "0.1.0"
    assert md["prompt_hash"] == "abc123"
    assert md["agent_id"] == "agent-1"
