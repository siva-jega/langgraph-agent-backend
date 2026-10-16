"""Evaluators wired into ``langsmith.evaluation.aevaluate``.

Two layers:

- ``llm.py``: four gpt-5.4-mini-as-judge evaluators — the RAG triad (faithfulness,
  response relevancy, context precision) plus answer correctness, which
  scores the reply against a per-case rubric in ``golden_reply``. G-Eval
  form-filling: chain-of-thought reasoning ending in a probability-weighted
  ``Score: X`` digit, no external eval framework.
- ``programmatic.py``: deterministic, no-LLM judge — tool trajectory match
  against ``expected_tools``.

Both layers expose the same signature ``async (run, example) -> dict``, which
is what ``langsmith.evaluation.aevaluate`` calls per case. ``default_judges()``
returns the canonical bundle.
"""

from .llm import (
    answer_correctness_evaluator,
    context_precision_evaluator,
    faithfulness_evaluator,
    response_relevancy_evaluator,
)
from .programmatic import trajectory_evaluator


def default_judges() -> list:
    """Canonical bundle: the RAG triad + trajectory.

    ``answer_correctness`` is intentionally NOT in the default bundle. For the
    Shopify + KB use case the RAG triad (faithfulness, response relevancy,
    context precision) plus trajectory already cover routing, grounding, and
    on-topic-ness; the per-case rubric judge was redundant overkill here. The
    evaluator is still defined/exported, so add it back to this list if a
    future dataset wants rubric-level scoring.
    """
    return [
        trajectory_evaluator,
        faithfulness_evaluator,
        response_relevancy_evaluator,
        context_precision_evaluator,
    ]


def routing_judges() -> list:
    """Tool-routing only — trajectory judge alone.

    For skill datasets (e.g. ``skills_routing_v1``) where the only thing under
    test is whether the query routed to the right tool. Skips the LLM judges:
    no answer-quality signal needed, zero per-case cost, no flaky-judge noise.
    """
    return [trajectory_evaluator]


__all__ = [
    "answer_correctness_evaluator",
    "context_precision_evaluator",
    "default_judges",
    "faithfulness_evaluator",
    "response_relevancy_evaluator",
    "routing_judges",
    "trajectory_evaluator",
]
