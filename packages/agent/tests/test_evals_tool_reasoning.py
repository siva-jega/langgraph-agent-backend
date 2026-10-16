"""Tests for the eval-only tool-selection explainer (reasoning.py).

The explainer re-invokes the agent's model. We inject a fake model so the
suite is hermetic and never hits the network.
"""

from __future__ import annotations

import pytest

from app.modules.evals.reasoning import (
    _format_tool_calls,
    explain_tool_selection,
)


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _FakeModel:
    """Stand-in for a ChatOpenAI: records the messages, returns a canned reply."""

    def __init__(self, content="", raises: Exception | None = None):
        self._content = content
        self._raises = raises
        self.last_messages = None

    async def ainvoke(self, messages):
        self.last_messages = messages
        if self._raises is not None:
            raise self._raises
        return _FakeResponse(self._content)


# ---------------------------------------------------------------------------
# _format_tool_calls
# ---------------------------------------------------------------------------


def test_format_tool_calls_empty() -> None:
    assert _format_tool_calls(None) == "(no tools were called)"
    assert _format_tool_calls([]) == "(no tools were called)"


def test_format_tool_calls_with_args() -> None:
    out = _format_tool_calls([
        {"name": "create_ticket_aide", "args": {"reason": "broken item"}},
        {"name": "web_search"},
    ])
    assert "create_ticket_aide" in out
    assert '"reason": "broken item"' in out
    assert "- web_search" in out


# ---------------------------------------------------------------------------
# explain_tool_selection — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_parses_reasoning_and_multiple_rules() -> None:
    model = _FakeModel(
        '{"reasoning": "The user reported a broken item and also asked to talk '
        'to a human, so the agent created a ticket and escalated.", '
        '"prompt_rules_influenced": ["Call when the user reports a problem, '
        'complaint, or issue that needs tracking", "Escalate when the user asks '
        'to speak with a human"]}'
    )
    out = await explain_tool_selection(
        model_name="gpt-5.4-mini",
        system_prompt="...Call when the user reports a problem... Escalate when...",
        query="my order arrived broken, let me talk to someone",
        tool_calls=[
            {"name": "create_ticket_aide", "args": {"reason": "broken order"}},
            {"name": "human_handoff_trigger", "args": {}},
        ],
        model=model,
    )
    assert out["tool_selection_reasoning"].startswith("The user reported a broken item")
    rules = out["prompt_rules_influenced"]
    assert isinstance(rules, list) and len(rules) == 2
    assert any("reports a problem" in r for r in rules)
    assert any("Escalate" in r for r in rules)
    # The system prompt, query, and (multiple) tool calls are all handed to the model.
    user_msg = model.last_messages[1].content
    assert "my order arrived broken" in user_msg
    assert "create_ticket_aide" in user_msg
    assert "human_handoff_trigger" in user_msg


@pytest.mark.asyncio
async def test_explain_single_rule_string_is_wrapped_to_list() -> None:
    # A model that returns a bare string for the rules field is normalized.
    model = _FakeModel(
        '{"reasoning": "matched the lead trigger", '
        '"prompt_rules_influenced": "Call when the user expresses purchase intent"}'
    )
    out = await explain_tool_selection(
        model_name="gpt-5.4-mini",
        system_prompt="...purchase intent...",
        query="I want a quote",
        tool_calls=[{"name": "create_lead_aide"}],
        model=model,
    )
    assert out["prompt_rules_influenced"] == ["Call when the user expresses purchase intent"]


@pytest.mark.asyncio
async def test_explain_handles_no_tool_called() -> None:
    model = _FakeModel(
        '{"reasoning": "Pure greeting, no tool trigger matched.", '
        '"prompt_rules_influenced": []}'
    )
    out = await explain_tool_selection(
        model_name="gpt-5.4-mini",
        system_prompt="...rules...",
        query="hello there",
        tool_calls=[],
        model=model,
    )
    assert out["tool_selection_reasoning"]
    assert out["prompt_rules_influenced"] == []
    assert "(no tools were called)" in model.last_messages[1].content


# ---------------------------------------------------------------------------
# explain_tool_selection — graceful degradation (never raises)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_malformed_json_degrades() -> None:
    model = _FakeModel("this is not json at all")
    out = await explain_tool_selection(
        model_name="gpt-5.4-mini",
        system_prompt="...",
        query="q",
        tool_calls=[{"name": "x"}],
        model=model,
    )
    assert out["prompt_rules_influenced"] == []
    assert out["tool_selection_reasoning"].startswith("(explainer failed")


@pytest.mark.asyncio
async def test_explain_model_exception_degrades() -> None:
    model = _FakeModel(raises=RuntimeError("boom"))
    out = await explain_tool_selection(
        model_name="gpt-5.4-mini",
        system_prompt="...",
        query="q",
        tool_calls=[{"name": "x"}],
        model=model,
    )
    assert out["tool_selection_reasoning"] == "(explainer failed: RuntimeError)"
    assert out["prompt_rules_influenced"] == []


@pytest.mark.asyncio
async def test_explain_handles_list_content_blocks() -> None:
    # Some providers return content as a list of blocks.
    model = _FakeModel(
        [{"text": '{"reasoning": "ok", '}, {"text": '"prompt_rules_influenced": ["rule"]}'}]
    )
    out = await explain_tool_selection(
        model_name="gpt-5.4-mini",
        system_prompt="...",
        query="q",
        tool_calls=[{"name": "x"}],
        model=model,
    )
    assert out["tool_selection_reasoning"] == "ok"
    assert out["prompt_rules_influenced"] == ["rule"]
