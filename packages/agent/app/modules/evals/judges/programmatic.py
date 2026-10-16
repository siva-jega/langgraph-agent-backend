"""Deterministic, no-LLM evaluators.

One judge:
  - ``trajectory_evaluator``: did the agent call the expected tools? Compares
    actual tool names (from ``run.outputs["tool_calls"]``) to expected tools
    (from ``example.outputs["expected_tools"]``). Set semantics by default,
    so order doesn't matter — the agent often calls a lookup tool before the
    target one and we don't want to punish that.

This judge lives here (not in ``llm.py``) so a) cost stays at zero per case
and b) flaky LLM judges never block it from running.
"""

from __future__ import annotations

from typing import Any


def _outputs(obj) -> dict:
    """Safe accessor for ``run.outputs`` / ``example.outputs``."""
    return getattr(obj, "outputs", None) or {}


def _result(key: str, score: float, comment: str, **extra) -> dict[str, Any]:
    """Build the LangSmith evaluator result dict.

    ``EvaluationResult`` is a pydantic model with ``extra="forbid"`` — any
    top-level key outside its fixed schema (key/score/value/metadata/comment/
    correction/...) raises a ``ValidationError`` that ``aevaluate()`` surfaces
    as a generic "Expected an EvaluationResult object..." error, silently
    dropping this judge's score to None. Diagnostic fields (expected/actual/
    missing/unexpected) must go in ``metadata``, the field built for exactly
    this, not splatted at the top level.
    """
    base: dict[str, Any] = {
        "key": key,
        "score": round(max(0.0, min(1.0, float(score))), 4),
        "comment": comment[:1000],
    }
    if extra:
        base["metadata"] = extra
    return base


# ---------------------------------------------------------------------------
# Trajectory — tool routing accuracy
# ---------------------------------------------------------------------------


async def trajectory_evaluator(run, example) -> dict[str, Any]:
    """Score on [0, 1] for how well the agent's tool calls match the expected set.

    Scoring policy:
      - No expected tools, no actual tools  → 1.0
      - No expected, some actual            → 0.0 (over-trigger)
      - All expected present, no extras     → 1.0
      - Partial expected present            → fraction of expected found, minus 0.1 per unexpected (capped at 0.5 penalty)
      - All expected present, some extras   → 1.0 - 0.1 per extra (capped)

    Set semantics by default. ``expected_tools_strict_order`` on the case
    flips to prefix-equality (out of scope for the LangSmith plumbing right
    now since LangSmith doesn't surface that field; reintroduce when needed).
    """
    expected: list[str] = list(_outputs(example).get("expected_tools") or [])
    expected_set = set(expected)

    actual_calls = _outputs(run).get("tool_calls") or []
    actual: list[str] = [
        tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
        for tc in actual_calls
    ]
    actual = [n for n in actual if n]
    actual_set = set(actual)

    if not expected_set:
        if not actual_set:
            return _result(
                "trajectory",
                1.0,
                "no tools expected, agent made none",
                expected=[],
                actual=[],
            )
        return _result(
            "trajectory",
            0.0,
            f"no tools expected but agent called {sorted(actual_set)}",
            expected=[],
            actual=sorted(actual_set),
            unexpected=sorted(actual_set),
        )

    missing = expected_set - actual_set
    unexpected = actual_set - expected_set
    base = len(expected_set & actual_set) / len(expected_set)
    penalty = min(0.5, 0.1 * len(unexpected))
    score = max(0.0, base - penalty)

    if not missing and not unexpected:
        comment = "matched"
    else:
        parts = []
        if missing:
            parts.append(f"missing {sorted(missing)}")
        if unexpected:
            parts.append(f"unexpected {sorted(unexpected)}")
        comment = "; ".join(parts)

    return _result(
        "trajectory",
        score,
        comment,
        expected=sorted(expected_set),
        actual=sorted(actual_set),
        missing=sorted(missing),
        unexpected=sorted(unexpected),
    )
