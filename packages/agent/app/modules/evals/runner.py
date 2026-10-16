"""Eval runner: load a case, run it through the real ``Graph``, return the
shape LangSmith evaluators expect.

The runner exposes two seams:

- ``predict(inputs)`` — pure function that LangSmith will call once per case
  inside ``aevaluate(...)``. Returns ``{"reply", "tool_calls", "retrieved_contexts"}``.
- ``run_dataset(...)`` — wraps ``aevaluate``, attaches the LangSmith tracer
  for the duration of the run (Phase 1's helper), and returns the experiment
  metadata. Phase 3 will add ``evaluators=[...]``; here in Phase 2 the call
  succeeds without scoring so we can verify traces flow end-to-end.

History handling: ``Graph.run_non_streaming`` only takes ``input_text`` —
prior messages come from the LangGraph checkpointer keyed by chat_id, not
from an arg. For evals we render history into a single labelled preamble so
the model sees the prior turns. This is a deliberate simplification; if it
biases scores noticeably we can revisit in Phase 3 by seeding a per-case
``MemorySaver``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from app.core.config import app_settings
from app.modules.agents.graph.tracing import (
    build_trace_metadata,
    langsmith_enabled,
)
from app.modules.evals.tool_stubs import CapturedToolCalls, stubbed_write_tools

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool-selection explainer helpers (eval-only observability)
# ---------------------------------------------------------------------------


def _render_system_prompt(additional_params: dict[str, Any]) -> str:
    """Reconstruct the rendered system prompt the agent saw for this case.

    Mirrors the param mapping in ``Graph.run`` (graph.py: ``config_dict`` →
    ``prompt_params`` + resolved ``human_handoff_enabled``) so the explainer
    cites the exact prompt the model used. Eval-only — production builds this
    inside the graph itself; if that mapping changes, update this to match.
    """
    from app.modules.agents.graph.graph import _resolve_human_handoff_enabled
    from app.modules.agents.graph.prompt import get_system_prompt_with_meta

    config_dict = additional_params.get("config", {})
    if not isinstance(config_dict, dict):
        config_dict = {}
    skills = config_dict.get("skills", []) or []
    handoff_skill = next(
        (
            s
            for s in skills
            if isinstance(s, dict) and (s.get("toolName") or "") == "human_handoff_trigger"
        ),
        None,
    )
    db_handoff_on = bool(
        config_dict.get("human_handoff_enabled", False)
        or config_dict.get("humanHandoffEnabled", False)
    )
    prompt_params = dict(config_dict)
    prompt_params["human_handoff_enabled"] = _resolve_human_handoff_enabled(
        db_handoff_on=db_handoff_on,
        has_handoff_skill=handoff_skill is not None,
    )
    system_prompt, _version, _hash = get_system_prompt_with_meta(additional_params=prompt_params)
    return system_prompt


def _resolve_model_name(additional_params: dict[str, Any]) -> str:
    """Resolve the model id the agent used, with the same precedence as
    ``llm_node`` (nodes.py): ``config.persona.model`` → ``DEFAULT_AGENT_MODEL``.
    There is no per-request ``model`` override in the eval path.
    """
    config_dict = additional_params.get("config", {})
    if not isinstance(config_dict, dict):
        config_dict = {}
    persona = config_dict.get("persona", {})
    model = (persona.get("model") if isinstance(persona, dict) else "") or ""
    return model or app_settings.DEFAULT_AGENT_MODEL


# Default location for agent_config templates. Cases reference templates by
# name (``agent_config_template: "shopify_kb_aide"``) and the runner resolves
# the name to ``<config_dir>/<name>.json``.
#
# This file lives at packages/agent/app/modules/evals/runner.py, so parents[3]
# is packages/agent — the configs sit at packages/agent/eval/configs/.
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "eval" / "configs"


# ---------------------------------------------------------------------------
# Config template loading
# ---------------------------------------------------------------------------


def load_config_template(name: str, config_dir: Path | str | None = None) -> dict:
    """Load an agent_config template by name.

    Templates mirror the shape returned by ``AgentsRepository.get_config`` —
    a dict with ``agent_id`` and a nested ``config`` carrying persona, skills,
    apps, entityIds, folderIds. The runner passes these straight through to
    ``Graph.run_non_streaming`` via ``additional_params``.
    """
    base = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR
    path = base / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Agent config template not found: {path}")
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict):
        raise ValueError(f"Config template root must be an object: {path}")
    return cfg


# ---------------------------------------------------------------------------
# History rendering
# ---------------------------------------------------------------------------


# History handling note
# ---------------------
# We deliberately do NOT render history into the input text. The agent uses
# LangGraph's checkpointer (Postgres in staging) to look up prior turns by
# thread_id. As long as turn 1 and turn 2 of a conversation share a chat_id
# (and thus a thread_id), turn 2 sees turn 1's messages automatically — same
# as a real customer conversation. See ``_scoped_thread_id`` below.


# ---------------------------------------------------------------------------
# Predict — the function LangSmith calls per case
# ---------------------------------------------------------------------------


def _normalize_tool_calls(
    graph_tool_calls: list[Any] | None,
    captured: CapturedToolCalls,
) -> list[dict]:
    """Build a unified tool_calls list from both real graph events and stub captures.

    The graph emits one ``{name, text, data}`` entry per real tool fired (see
    ``Graph.run_non_streaming``). Stubbed write tools never make it into that
    list (the graph node sees the stub return value as the tool result and
    moves on), so we append them from ``captured.calls``. Each entry on the
    way out is ``{name, args, stubbed}``.
    """
    out: list[dict] = []
    seen_names: set[str] = set()
    for tc in graph_tool_calls or []:
        if not isinstance(tc, dict):
            continue
        name = str(tc.get("name") or "")
        if not name:
            continue
        out.append({
            "name": name,
            "args": tc.get("data") if isinstance(tc.get("data"), dict) else {},
            "stubbed": False,
        })
        seen_names.add(name)
    for call in captured.calls:
        name = call.get("name") or ""
        if not name:
            continue
        out.append({
            "name": name,
            "args": call.get("args") or {},
            "stubbed": True,
        })
    return out


def _extract_retrieved_contexts(sources: list[Any] | None) -> list[str]:
    """Compact stringification of tool artifacts so Phase 3 RAG judges have
    something to score for context relevance / faithfulness.

    Each entry is one tool's output, passed through untruncated.
    """
    out: list[str] = []
    for src in sources or []:
        if src is None:
            continue
        if isinstance(src, str):
            out.append(src)
            continue
        try:
            out.append(json.dumps(src, default=str))
        except Exception:
            out.append(str(src))
    return out


async def _resolve_agent_config(
    *,
    agent_id: Optional[str],
    template_name: Optional[str],
    config_dir: Path | str | None,
) -> dict:
    """Two-mode config resolution.

    ``agent_id`` is the canonical eval path: load via the same repository the
    production ``/chat`` endpoint uses, so persona / apps / skills / KB
    attachments are real. ``template_name`` is the hermetic-tests fallback.
    """
    if agent_id:
        # Lazy import so tests that exercise only the template path don't pay
        # the asyncpg import cost.
        from app.modules.agents.repository import AgentsRepository

        repo = AgentsRepository()
        cfg = await repo.get_config(agent_id)
        if not cfg:
            raise RuntimeError(
                f"AgentsRepository.get_config returned None for agent_id={agent_id!r}. "
                "Is DATABASE_URL pointed at staging and does this agent exist?"
            )
        return cfg
    if template_name:
        return load_config_template(template_name, config_dir=config_dir)
    raise ValueError("predict requires either 'agent_id' or 'agent_config_template' in inputs")


def _scoped_thread_id(eval_run_id: str, case_chat_id: Optional[str]) -> str:
    """Map a case's chat_id to the actual LangGraph thread_id for this run.

    The case's chat_id is a UUID that identifies a conversation *within the
    dataset*. Two turns sharing that UUID must share history during one eval
    run, but **must not** inherit state from yesterday's run. So we prefix
    with the eval_run_id; the production checkpointer keys on the prefixed
    value and rows from prior runs are inert.

    Single-turn cases (case_chat_id is None) get a unique synthetic chat_id
    per call so they're independent from each other.
    """
    if case_chat_id:
        return f"evalrun:{eval_run_id}:{case_chat_id}"
    return f"evalrun:{eval_run_id}:turn:{uuid.uuid4().hex[:12]}"


async def predict(
    inputs: dict[str, Any],
    *,
    config_dir: Path | str | None = None,
    is_preview: bool = False,
    eval_run_id: Optional[str] = None,
    explain_tool_choice: bool = False,
) -> dict[str, Any]:
    """Run one case through the production agent singleton. Signature matches
    ``langsmith.evaluation.aevaluate``: a callable that takes inputs and
    returns outputs.

    Uses ``get_agent()`` — the same singleton wired with ``AsyncPostgresSaver``
    at app startup. That means the checkpointer is the real one staging /
    production uses; turns within the same ``chat_id`` see each other's
    history naturally because LangGraph reads/writes the checkpointer keyed
    by ``thread_id``. No manual history rendering required.

    Defaults: ``is_preview=False`` so the agent runs the full path. Tests
    inject a fake ``get_agent`` and pass ``is_preview=True`` if they want.

    ``inputs`` carries (from the case):
      - ``query`` (required)
      - exactly one of ``agent_id`` or ``agent_config_template``
      - optional ``chat_id`` + ``sequence`` (multi-turn flows)
      - optional ``stub_write_tools`` (bool)

    Returns ``{reply, tool_calls, retrieved_contexts, latency_ms, ...}``.
    """
    query = inputs.get("query") or ""
    agent_id = inputs.get("agent_id")
    template_name = inputs.get("agent_config_template")
    case_chat_id = inputs.get("chat_id")  # str(UUID) from the dataset, or None
    stub_writes = bool(inputs.get("stub_write_tools", False))

    # eval_run_id scopes thread_ids so cross-run state doesn't leak. The
    # CLI / HTTP trigger generates one per run; tests can pass their own.
    eval_run_id = eval_run_id or "ad-hoc"

    started = time.perf_counter()
    captured = CapturedToolCalls()

    try:
        agent_config = await _resolve_agent_config(
            agent_id=agent_id,
            template_name=template_name,
            config_dir=config_dir,
        )

        # Thread_id = the LangGraph checkpointer key. Cases sharing a
        # chat_id within this run share history; everything else is fresh.
        thread_id = _scoped_thread_id(eval_run_id, case_chat_id)
        session_id = f"evalsession:{eval_run_id}:{uuid.uuid4().hex[:12]}"

        additional_params: dict[str, Any] = dict(agent_config)
        additional_params["chat_id"] = thread_id
        additional_params["session_id"] = session_id
        additional_params["agent_id"] = agent_config.get("agent_id") or "eval-agent"
        additional_params.setdefault("user_id", agent_config.get("user_id") or "eval-user")

        # Stubs are opt-in. Default for staging-realistic evals: no stubs.
        # Web-agent write tools (aide / handoff) are interrupt-style and don't
        # fire end-to-end. Non-interrupt writes (send_email, Slack, HubSpot,
        # Calendly) hit the eval user's connected apps which should be
        # sandbox-only.
        stub_ctx = stubbed_write_tools(captured) if stub_writes else contextlib.nullcontext()

        with stub_ctx:
            # Use the production agent singleton — same Graph instance, same
            # checkpointer wiring as the live /chat endpoint. Lazy import so
            # tests that stub get_agent never load asyncpg.
            from app.modules.agents.graph import get_agent

            agent = get_agent()
            result = await agent.run_non_streaming(
                input_text=query,
                additional_params=additional_params,
                is_preview=is_preview,
            )
    except Exception as e:  # noqa: BLE001 — surface per-case errors via output
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.exception("predict failed for case query=%r", query)
        return {
            "reply": "",
            "tool_calls": [],
            "retrieved_contexts": [],
            "latency_ms": latency_ms,
            "error": f"{type(e).__name__}: {e}",
        }
    latency_ms = int((time.perf_counter() - started) * 1000)

    tool_calls = _normalize_tool_calls(result.get("tool_calls"), captured)
    output: dict[str, Any] = {
        "reply": result.get("content") or "",
        "tool_calls": tool_calls,
        "retrieved_contexts": _extract_retrieved_contexts(result.get("sources")),
        "latency_ms": latency_ms,
    }

    # Eval-only: ask the agent's own model why it picked these tools and which
    # prompt rule drove it. ``explain_tool_selection`` is a ``@traceable`` span,
    # so the reasoning + cited rule are read from each query's TRACE tree (not an
    # experiment column). We don't merge it into the output dict on purpose.
    # Best-effort — a failure here must never break the case.
    if explain_tool_choice:
        try:
            from app.modules.evals.reasoning import explain_tool_selection

            await explain_tool_selection(
                model_name=_resolve_model_name(additional_params),
                system_prompt=_render_system_prompt(additional_params),
                query=query,
                tool_calls=tool_calls,
            )
        except Exception as e:  # noqa: BLE001 — observability only
            logger.warning("tool-selection explainer skipped: %s", e)

    return output


# ---------------------------------------------------------------------------
# run_dataset — entry point used by the CLI (Phase 5) and CI
# ---------------------------------------------------------------------------


def _enable_tracing_env() -> None:
    """Flip LangChain's tracing env vars ON for the duration of this process.

    The eval runner intentionally turns tracing on at runtime — production
    paths never set it. Mirrors the propagation in ``graph/tracing.py`` so
    LangChain's SDK picks up the API key + project.
    """
    from app.core.config import app_settings

    if not app_settings.LANGSMITH_API_KEY:
        logger.warning("LANGSMITH_API_KEY not set — eval traces won't reach LangSmith")
        return
    os.environ.setdefault("LANGCHAIN_API_KEY", app_settings.LANGSMITH_API_KEY)
    os.environ.setdefault("LANGSMITH_API_KEY", app_settings.LANGSMITH_API_KEY)
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ.setdefault("LANGCHAIN_PROJECT", app_settings.LANGSMITH_PROJECT)
    os.environ.setdefault("LANGSMITH_PROJECT", app_settings.LANGSMITH_PROJECT)
    if app_settings.LANGSMITH_ENDPOINT:
        os.environ.setdefault("LANGCHAIN_ENDPOINT", app_settings.LANGSMITH_ENDPOINT)


async def run_dataset(
    dataset_name: str,
    *,
    experiment_prefix: str = "eval",
    evaluators: Optional[list[Any]] = None,
    max_concurrency: int = 1,
    metadata: Optional[dict[str, Any]] = None,
    config_dir: Path | str | None = None,
    client: Any = None,
    is_preview: bool = False,
    eval_run_id: Optional[str] = None,
    explain_tool_choice: bool = False,
) -> Any:
    """Run an eval dataset through ``langsmith.evaluation.aevaluate``.

    Defaults to the canonical 5-judge bundle (trajectory, faithfulness,
    response_relevancy, context_precision, answer_correctness) when
    ``evaluators`` is None. Pass ``evaluators=[]`` for an unscored smoke run.

    **Concurrency** defaults to 1. Multi-turn cases share a ``chat_id`` and
    need their turns to run in order, which aevaluate doesn't guarantee at
    concurrency>1. Bump only if your dataset is single-turn only.

    Args:
        dataset_name: LangSmith dataset name (created via ``upload_to_langsmith``).
        experiment_prefix: prefix for the LangSmith experiment name.
        evaluators: ``None`` → default judges; ``[]`` → unscored smoke.
        max_concurrency: cases run in parallel; default 1 for multi-turn safety.
        metadata: extra experiment metadata (e.g. ``git_sha``).
        config_dir: override for the agent_config template directory.
        client: optional ``langsmith.Client`` for tests.
        is_preview: forwarded to ``predict``. Default False = real staging path.
        eval_run_id: scopes the LangGraph thread_id so chat history from this
            run is isolated from yesterday's. Defaults to a fresh UUID.
        explain_tool_choice: when True, emit a ``tool_selection_reasoning``
            trace span (per query) carrying the reasoning + the
            ``prompt_rules_influenced`` list, by asking the agent's own model to
            explain its tool selection. One extra model call per case; opt-in.
    """
    _enable_tracing_env()
    if client is None:
        from langsmith import Client

        client = Client()
    from langsmith.evaluation import aevaluate  # lazy import

    if evaluators is None:
        from app.modules.evals.judges import default_judges

        evaluators = default_judges()

    # One fresh run id per dataset run. predict() uses it to prefix the
    # thread_id so cross-run checkpointer state doesn't leak.
    run_id = eval_run_id or uuid.uuid4().hex

    async def _predict(inputs: dict) -> dict:
        return await predict(
            inputs,
            config_dir=config_dir,
            is_preview=is_preview,
            eval_run_id=run_id,
            explain_tool_choice=explain_tool_choice,
        )

    # Stamp every experiment with the current prompt_builder_version so the
    # LangSmith comparison view can group / diff experiments by prompt version.
    # The hash isn't pinned here (it varies per case with the rendered prompt)
    # but the version is constant across the experiment, which is what we want.
    extra_md = dict(metadata or {})
    from app.modules.agents.graph.prompt import PROMPT_BUILDER_VERSION

    extra_md.setdefault("prompt_builder_version", PROMPT_BUILDER_VERSION)

    md = build_trace_metadata(
        agent_id=None,
        chat_id=None,
        session_id=None,
        model=None,
        extra=extra_md,
    )
    md.setdefault("dataset", dataset_name)
    md.setdefault("eval_run", True)
    md.setdefault("eval_run_id", run_id)

    logger.info(
        "running eval dataset %r (experiment_prefix=%r, evaluators=%d, "
        "concurrency=%d, run_id=%s, langsmith=%s)",
        dataset_name,
        experiment_prefix,
        len(evaluators),
        max_concurrency,
        run_id,
        langsmith_enabled(),
    )
    return await aevaluate(
        _predict,
        data=dataset_name,
        evaluators=evaluators,
        experiment_prefix=experiment_prefix,
        max_concurrency=max_concurrency,
        client=client,
        metadata=md,
    )
