"""Eval case model + JSON loader + LangSmith dataset upload.

A **case** is one (query, history, agent config) triple plus a rubric that
describes what "correct" looks like. Cases are authored in a JSON file under
``packages/agent/eval/datasets/`` and uploaded to LangSmith once via
``upload_to_langsmith`` so the LangSmith UI owns the canonical store.

Why split inputs vs outputs in the LangSmith upload: the SDK convention is
``inputs`` = what ``predict_fn`` receives, ``outputs`` = reference values the
evaluators compare against. Keeping this split clean means our evaluators can
read ``run.outputs`` (the agent's reply + tool calls) and
``example.outputs`` (the expected_tools + golden_reply) without overlap.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Case schema
# ---------------------------------------------------------------------------


class Case(BaseModel):
    """One eval case.

    ## Identity
    Exactly one of ``agent_id`` or ``agent_config_template`` must be set:

    - ``agent_id`` (preferred): a real staging agent uuid. The runner pulls
      config from Postgres via ``AgentsRepository.get_config`` — same code
      path production uses. Real persona, real KB, real connected apps.
      Requires ``DATABASE_URL`` pointing at staging.
    - ``agent_config_template``: a JSON template under
      ``packages/agent/eval/configs/<name>.json``. Used only by hermetic
      unit tests of the runner; not the canonical eval path.

    ## History
    Multi-turn conversations are expressed by **sharing a ``chat_id`` (UUID)
    across cases** and ordering them with ``sequence``. The runner injects a
    fresh ``MemorySaver`` per eval run; the agent's LangGraph checkpointer
    handles history naturally — same code path as production.

    - Single-turn case: leave ``chat_id`` and ``sequence`` unset; the runner
      generates a unique chat_id per case.
    - Multi-turn flow: every turn declares the same ``chat_id`` UUID; each
      gets a distinct ``sequence`` (1, 2, 3, …). Cases run serially within
      a chat_id; different chat_ids can run in parallel.

    ## Stubs
    ``stub_write_tools``: opt-in safety. When True, the runner wraps the
    agent call in ``stubbed_write_tools()`` so non-interrupt write tools
    (``send_email``, slack send, HubSpot writes, etc.) capture their args
    without firing. Web-agent interrupt-style writes (aide / handoff) are
    naturally captured by the interrupt and don't need this flag.

    ## Scoring
    ``score_judges``: set False for setup-only turns where the agent's reply
    isn't the thing under test (e.g. "what's your name and email?" prompts
    on a multi-turn flow). The case still runs, history accumulates, but
    judges skip it so they don't drag the experiment average.

    ``golden_reply``: a prose RUBRIC describing what a correct reply must
    contain and what it must avoid — NOT a verbatim expected answer. The
    ``answer_correctness`` LLM judge scores the actual reply against it.
    Leave unset to skip that judge (no impact on other scores). Catalog data
    drifts, so write rubrics in terms of *requirements* ("lists at least one
    matching product with name and price"; "does not invent SKUs"; "asks for
    name and email before creating a ticket") rather than expected strings.
    """

    id: str
    query: str
    agent_id: Optional[str] = None
    agent_config_template: Optional[str] = None
    chat_id: Optional[UUID] = None
    sequence: Optional[int] = Field(default=None, ge=1)
    stub_write_tools: bool = False
    score_judges: bool = True
    expected_tools: list[str] = Field(default_factory=list)
    golden_reply: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one_config_source(self) -> "Case":
        has_id = bool(self.agent_id)
        has_template = bool(self.agent_config_template)
        if has_id == has_template:
            raise ValueError(
                f"Case {self.id}: exactly one of 'agent_id' or 'agent_config_template' must be set "
                f"(got agent_id={self.agent_id!r}, agent_config_template={self.agent_config_template!r})"
            )
        return self

    @model_validator(mode="after")
    def _chat_id_and_sequence_co_set(self) -> "Case":
        """Multi-turn cases require both fields together. A chat without
        ordering would be non-deterministic; a sequence without a chat has
        nothing to order against."""
        if (self.chat_id is None) != (self.sequence is None):
            raise ValueError(
                f"Case {self.id}: chat_id and sequence must be set together "
                f"(got chat_id={self.chat_id!r}, sequence={self.sequence!r})"
            )
        return self

    def langsmith_inputs(self) -> dict[str, Any]:
        """What ``predict_fn`` will receive. Predict resolves agent config +
        wires the conversation thread from ``chat_id`` at run time."""
        out: dict[str, Any] = {
            "query": self.query,
            "stub_write_tools": self.stub_write_tools,
            "score_judges": self.score_judges,
        }
        if self.agent_id:
            out["agent_id"] = self.agent_id
        if self.agent_config_template:
            out["agent_config_template"] = self.agent_config_template
        if self.chat_id is not None:
            # UUID → str so JSON serialization in the LangSmith dataset works.
            out["chat_id"] = str(self.chat_id)
            out["sequence"] = self.sequence
        return out

    def langsmith_outputs(self) -> dict[str, Any]:
        """Reference values evaluators score against. Phase 3 judges read this."""
        return {
            "expected_tools": self.expected_tools,
            "golden_reply": self.golden_reply,
        }

    def langsmith_metadata(self) -> dict[str, Any]:
        """Trace-level metadata. Tags drive the per-tag breakdown in the
        LangSmith UI; chat_id lets you filter to all turns of one conversation."""
        md: dict[str, Any] = {"case_id": self.id, "tags": self.tags}
        if self.chat_id is not None:
            md["chat_id"] = str(self.chat_id)
            md["sequence"] = self.sequence
        return md


# ---------------------------------------------------------------------------
# JSON loader
# ---------------------------------------------------------------------------


class DatasetError(ValueError):
    """Raised when a dataset file is malformed."""


def load_cases(path: str | Path) -> tuple[str, list[Case]]:
    """Load a dataset JSON file. Returns ``(version, cases)``. Raises
    ``DatasetError`` for malformed input — we'd rather fail at startup than
    silently skip cases.
    """
    p = Path(path)
    if not p.exists():
        raise DatasetError(f"Dataset file not found: {p}")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise DatasetError(f"Invalid JSON in {p}: {e}") from e
    if not isinstance(raw, dict):
        raise DatasetError(f"Dataset root must be an object: {p}")

    version = str(raw.get("version") or "unversioned")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DatasetError(f"Dataset {p} has no cases")

    cases: list[Case] = []
    seen: set[str] = set()
    for entry in raw_cases:
        if not isinstance(entry, dict):
            raise DatasetError(f"Case entry must be an object: {entry!r}")
        try:
            case = Case.model_validate(entry)
        except Exception as e:  # pydantic ValidationError, surface clearly
            raise DatasetError(f"Invalid case {entry.get('id', '?')}: {e}") from e
        if case.id in seen:
            raise DatasetError(f"Duplicate case id: {case.id}")
        seen.add(case.id)
        cases.append(case)
    return version, cases


# ---------------------------------------------------------------------------
# LangSmith dataset upload
# ---------------------------------------------------------------------------


def upload_to_langsmith(
    dataset_name: str,
    cases: list[Case],
    *,
    description: str = "",
    client: Any = None,
) -> str:
    """Push cases to LangSmith. Idempotent for both the dataset and its
    examples: re-uploading matches existing examples by ``case_id``
    (stored in metadata) and updates them in place, only creating examples
    for genuinely new case ids. Cases removed from the JSON file are left
    alone here — deleting stale examples is a separate, explicit step.

    Returns the LangSmith dataset id. ``client`` is optional so tests can
    inject a stub without monkey-patching.
    """
    if client is None:
        # Lazy import — keeps the dependency invisible until you actually need it.
        from langsmith import Client

        client = Client()

    # Idempotent dataset create: re-uploading an existing name is a common
    # workflow as cases are added over time.
    try:
        ds = client.read_dataset(dataset_name=dataset_name)
        logger.info("LangSmith dataset %r already exists (id=%s)", dataset_name, ds.id)
    except Exception:
        ds = client.create_dataset(
            dataset_name=dataset_name,
            description=description or f"Web agent eval cases ({dataset_name})",
        )
        logger.info("Created LangSmith dataset %r (id=%s)", dataset_name, ds.id)

    existing_by_case_id = {
        ex.metadata.get("case_id"): ex.id
        for ex in client.list_examples(dataset_id=ds.id)
        if ex.metadata and ex.metadata.get("case_id")
    }

    new_cases = [c for c in cases if c.id not in existing_by_case_id]
    updated_cases = [c for c in cases if c.id in existing_by_case_id]

    if new_cases:
        client.create_examples(
            dataset_id=ds.id,
            inputs=[c.langsmith_inputs() for c in new_cases],
            outputs=[c.langsmith_outputs() for c in new_cases],
            metadata=[c.langsmith_metadata() for c in new_cases],
        )
    if updated_cases:
        from langsmith.schemas import ExampleUpdate

        client.update_examples(
            dataset_id=ds.id,
            updates=[
                ExampleUpdate(
                    id=existing_by_case_id[c.id],
                    inputs=c.langsmith_inputs(),
                    outputs=c.langsmith_outputs(),
                    metadata=c.langsmith_metadata(),
                )
                for c in updated_cases
            ],
        )
    logger.info(
        "Uploaded %d new + updated %d existing examples in dataset %r",
        len(new_cases), len(updated_cases), dataset_name,
    )
    return str(ds.id)
