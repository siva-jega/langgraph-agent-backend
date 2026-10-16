"""Eval system for the web agent.

See ``/EVAL_PLAN.md`` at repo root for the phased plan. This module owns:

- ``dataset.py``: ``Case`` model, JSON loader, LangSmith dataset upload
- ``tool_stubs.py``: ``stubbed_write_tools()`` context manager — patches the
  tool registry so write-side tools (ticket / lead / handoff / email) capture
  their call args without producing side effects during evals
- ``runner.py``: ``predict(...)`` runs one case through the real ``Graph``;
  ``run_dataset(...)`` wraps ``langsmith.evaluation.aevaluate``
- ``cli.py`` (Phase 5): single command surface for upload/run/list/compare

Phase 3 will add ``judges/`` for RAGAS + programmatic scorers.
"""

from .dataset import Case, load_cases
from .tool_stubs import CapturedToolCalls, WRITE_TOOLS, stubbed_write_tools

__all__ = [
    "Case",
    "CapturedToolCalls",
    "WRITE_TOOLS",
    "load_cases",
    "stubbed_write_tools",
]
