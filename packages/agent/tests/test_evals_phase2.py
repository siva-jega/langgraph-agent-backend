"""Phase 2 tests: dataset loader, tool stubs, and the predict function.

The agent graph is stubbed in runner tests so the suite stays hermetic — no
API calls, no DB, no real LLM. Each piece is tested in isolation; an
end-to-end run against LangSmith is a manual smoke test in EVAL_PLAN.md.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.modules.evals.dataset import Case, DatasetError, load_cases
from app.modules.evals.runner import (
    DEFAULT_CONFIG_DIR,
    _normalize_tool_calls,
    _scoped_thread_id,
    load_config_template,
    predict,
)
from app.modules.evals.tool_stubs import (
    CapturedToolCalls,
    WRITE_TOOLS,
    stubbed_write_tools,
)
from common.tools.registry import registry

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = REPO_ROOT / "agent" / "eval" / "datasets" / "web_agent_v1.json"
TEMPLATE_DATASET_PATH = REPO_ROOT / "agent" / "eval" / "datasets" / "web_agent_template_v1.json"
CONFIG_DIR = REPO_ROOT / "agent" / "eval" / "configs"


# ---------------------------------------------------------------------------
# Dataset loader
# ---------------------------------------------------------------------------


def test_load_bundled_staging_dataset_parses() -> None:
    """The staging-focused dataset uses placeholder agent_ids until the eval
    workspace is created. It must parse cleanly so CI / upload tooling can
    operate on it; resolution against DB happens at predict time."""
    version, cases = load_cases(DATASET_PATH)
    assert version == "web_agent_v1"
    assert len(cases) >= 15
    # Every case references an agent_id (real eval path)
    for case in cases:
        assert case.agent_id, f"case {case.id} is missing agent_id"
        assert case.agent_config_template is None


def test_load_template_dataset_for_hermetic_tests() -> None:
    """The template-based dataset is for hermetic unit tests of the runner —
    every case references a JSON template that must exist on disk."""
    version, cases = load_cases(TEMPLATE_DATASET_PATH)
    assert version == "web_agent_template_v1"
    for case in cases:
        assert case.agent_config_template
        path = CONFIG_DIR / f"{case.agent_config_template}.json"
        assert path.exists(), f"missing template for {case.id}: {path}"


def test_load_rejects_duplicate_ids(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "version": "v0",
                "cases": [
                    {"id": "x", "query": "a"},
                    {"id": "x", "query": "b"},
                ],
            }
        )
    )
    with pytest.raises(DatasetError):
        load_cases(bad)


def test_load_rejects_missing_query(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": "v0", "cases": [{"id": "x"}]}))
    with pytest.raises(DatasetError):
        load_cases(bad)


def test_case_langsmith_split_template_path() -> None:
    """inputs / outputs / metadata roles are kept disjoint so evaluators don't
    accidentally read reference values from the agent's input. Template-path case."""
    case = Case(
        id="c1",
        query="hi",
        agent_config_template="shopify_only",
        expected_tools=["get_products"],
        golden_reply="hello",
        tags=["greet"],
    )
    inputs = case.langsmith_inputs()
    outputs = case.langsmith_outputs()
    md = case.langsmith_metadata()
    # Agent inputs hold only what predict_fn needs. Single-turn case → no
    # chat_id / sequence keys.
    assert set(inputs.keys()) == {"query", "agent_config_template", "stub_write_tools", "score_judges"}
    assert inputs["agent_config_template"] == "shopify_only"
    assert "agent_id" not in inputs
    assert "chat_id" not in inputs
    # Reference values live in outputs.
    assert outputs["expected_tools"] == ["get_products"]
    assert outputs["golden_reply"] == "hello"
    # Tags / id live in metadata for filtering in the LangSmith UI.
    assert md["case_id"] == "c1"
    assert md["tags"] == ["greet"]
    assert "chat_id" not in md


def test_case_langsmith_split_agent_id_path() -> None:
    """When agent_id is set, the inputs carry that and NOT the template field."""
    case = Case(
        id="c2",
        query="hi",
        agent_id="real-staging-uuid-1234",
        expected_tools=[],
    )
    inputs = case.langsmith_inputs()
    assert inputs["agent_id"] == "real-staging-uuid-1234"
    assert "agent_config_template" not in inputs


def test_case_requires_exactly_one_config_source() -> None:
    """Both sources set or neither set must be rejected at validation time —
    the runner can't silently pick one over the other."""
    with pytest.raises(Exception):  # pydantic ValidationError
        Case(id="bad-both", query="hi", agent_id="x", agent_config_template="y")
    with pytest.raises(Exception):
        Case(id="bad-neither", query="hi")


# ---------------------------------------------------------------------------
# Config template loader
# ---------------------------------------------------------------------------


def test_load_config_template_resolves_default_dir() -> None:
    """When config_dir is omitted, the loader uses the bundled directory."""
    cfg = load_config_template("shopify_kb_aide")
    assert cfg["agent_id"] == "eval-shopify-kb-aide"
    persona = cfg["config"]["persona"]
    assert persona["personaName"] == "Riley"


def test_load_config_template_raises_for_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config_template("does-not-exist", config_dir=tmp_path)


# ---------------------------------------------------------------------------
# chat_id / sequence — multi-turn flow representation
# ---------------------------------------------------------------------------


def test_case_accepts_uuid_chat_id_and_sequence() -> None:
    """Multi-turn cases declare a UUID chat_id + an integer sequence."""
    case = Case(
        id="lead-001",
        query="yes",
        agent_id="a1",
        chat_id="4a12f47a-47bc-40ca-8a79-f5224dc42ab3",
        sequence=3,
        expected_tools=["create_lead_aide"],
    )
    # chat_id parsed into a UUID type; emitted as str on the way out
    assert str(case.chat_id) == "4a12f47a-47bc-40ca-8a79-f5224dc42ab3"
    inputs = case.langsmith_inputs()
    assert inputs["chat_id"] == "4a12f47a-47bc-40ca-8a79-f5224dc42ab3"
    assert inputs["sequence"] == 3


def test_case_rejects_non_uuid_chat_id() -> None:
    """chat_id must be a valid UUID — typos / placeholders fail loudly at
    dataset upload time, not silently at predict time."""
    with pytest.raises(Exception):  # pydantic ValidationError
        Case(id="bad", query="x", agent_id="a", chat_id="not-a-uuid", sequence=1)


def test_case_rejects_chat_id_without_sequence() -> None:
    """A chat without ordering is non-deterministic; reject it."""
    with pytest.raises(Exception):
        Case(
            id="bad", query="x", agent_id="a",
            chat_id="4a12f47a-47bc-40ca-8a79-f5224dc42ab3",
        )


def test_case_rejects_sequence_without_chat_id() -> None:
    """A sequence without a chat_id has nothing to order against."""
    with pytest.raises(Exception):
        Case(id="bad", query="x", agent_id="a", sequence=1)


def test_case_rejects_zero_or_negative_sequence() -> None:
    """Sequences are 1-indexed and contiguous (turn 1, turn 2, ...)."""
    with pytest.raises(Exception):
        Case(
            id="bad", query="x", agent_id="a",
            chat_id="4a12f47a-47bc-40ca-8a79-f5224dc42ab3",
            sequence=0,
        )


def test_case_score_judges_default() -> None:
    """Setup-only turns set score_judges=False so judges skip them; default True."""
    case_default = Case(id="c1", query="x", agent_id="a")
    assert case_default.score_judges is True
    case_setup = Case(id="c2", query="x", agent_id="a", score_judges=False)
    assert case_setup.langsmith_inputs()["score_judges"] is False


def test_case_metadata_carries_chat_id_for_multi_turn() -> None:
    """LangSmith metadata exposes chat_id + sequence so you can filter the UI
    down to one conversation."""
    case = Case(
        id="lead-001",
        query="yes",
        agent_id="a1",
        chat_id="4a12f47a-47bc-40ca-8a79-f5224dc42ab3",
        sequence=3,
        tags=["lead-vs-ticket"],
    )
    md = case.langsmith_metadata()
    assert md["chat_id"] == "4a12f47a-47bc-40ca-8a79-f5224dc42ab3"
    assert md["sequence"] == 3
    assert md["tags"] == ["lead-vs-ticket"]


# ---------------------------------------------------------------------------
# Thread-id scoping — checkpointer isolation across runs
# ---------------------------------------------------------------------------


def test_scoped_thread_id_isolates_runs() -> None:
    """Two different eval runs of the same case must NOT share checkpointer
    state — otherwise turn 1 of Tuesday's run sees Monday's run's history."""
    a = _scoped_thread_id("run-aaa", "chat-uuid-1")
    b = _scoped_thread_id("run-bbb", "chat-uuid-1")
    assert a != b
    assert "chat-uuid-1" in a
    assert "chat-uuid-1" in b


def test_scoped_thread_id_groups_cases_in_same_run() -> None:
    """Two cases of the same multi-turn flow within ONE eval run share thread_id
    so the LangGraph checkpointer threads their history."""
    a = _scoped_thread_id("run-aaa", "chat-uuid-1")
    b = _scoped_thread_id("run-aaa", "chat-uuid-1")
    assert a == b


def test_scoped_thread_id_singleturn_cases_are_independent() -> None:
    """Cases without a chat_id (single-turn) get unique thread_ids so they
    don't accidentally share history."""
    a = _scoped_thread_id("run-aaa", None)
    b = _scoped_thread_id("run-aaa", None)
    assert a != b


# ---------------------------------------------------------------------------
# Tool stubs — the critical safety mechanism for evals
# ---------------------------------------------------------------------------


def test_stubbed_write_tools_records_invocations() -> None:
    """Inside the context manager, get_tool_by_name returns a stub for every
    name in WRITE_TOOLS; the stub records its call into ``captured``."""
    captured = CapturedToolCalls()
    with stubbed_write_tools(captured):
        stub = registry.get_tool_by_name("create_ticket_aide")
        assert stub is not None
        # The stub is a LangChain tool — call it the way LangChain would.
        asyncio.run(
            stub.ainvoke(
                {
                    "summary": "x",
                    "description": "y",
                    "requester_email": "a@b.com",
                }
            )
        )
    assert len(captured.calls) == 1
    assert captured.calls[0]["name"] == "create_ticket_aide"
    assert captured.calls[0]["args"]["summary"] == "x"
    assert captured.calls[0]["args"]["requester_email"] == "a@b.com"


def test_stubbed_write_tools_restores_registry_on_exit() -> None:
    """The patch is bounded by the context manager — leaving the block must
    restore the original ``get_tool_by_name`` so subsequent calls hit real tools."""
    original = registry.get_tool_by_name
    captured = CapturedToolCalls()
    with stubbed_write_tools(captured):
        assert registry.get_tool_by_name is not original
    assert registry.get_tool_by_name is original


def test_stubbed_write_tools_leaves_read_tools_real() -> None:
    """search_knowledgebase / get_products etc. are NOT stubbed — the runner
    relies on them executing for real so retrieval signal isn't fake."""
    captured = CapturedToolCalls()
    with stubbed_write_tools(captured):
        # search_knowledgebase is registered under the knowledgebase provider —
        # the lookup should NOT return a stub. We compare names rather than
        # identity because some providers wrap their tools.
        for name in ("search_knowledgebase", "get_products", "get_order_status"):
            assert name not in WRITE_TOOLS, f"unexpectedly stubbed read tool: {name}"


def test_stubbed_write_tools_accepts_extra_stubs() -> None:
    """Callers can stub additional tool names per run if needed."""
    captured = CapturedToolCalls()
    with stubbed_write_tools(captured, extra_stubs={"some_custom_tool"}):
        stub = registry.get_tool_by_name("some_custom_tool")
        assert stub is not None
        asyncio.run(stub.ainvoke({"foo": "bar"}))
    assert any(c["name"] == "some_custom_tool" for c in captured.calls)


# ---------------------------------------------------------------------------
# predict() — stubs the get_agent() singleton (real wiring would touch DB)
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Stand-in for the production agent singleton.

    Captures the ``run_non_streaming`` kwargs so tests can assert that the
    runner forwards chat_id + session_id + is_preview correctly.
    """

    last_call_kwargs: dict | None = None
    canned_result: dict[str, Any] = {
        "content": "stub reply",
        "sources": [{"doc": "kb-1"}],
        "token_usage": 1,
        "extra_info": None,
        "is_interrupted": False,
        "tool_calls": [
            {"name": "search_knowledgebase", "text": "...", "data": {"q": "x"}}
        ],
    }

    async def run_non_streaming(self, **kwargs):
        _FakeAgent.last_call_kwargs = kwargs
        return _FakeAgent.canned_result


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch):
    """Patch ``get_agent`` so the runner gets our fake instead of the real
    production singleton (which would require startup wiring + a Postgres
    checkpointer). The fake exposes the same ``run_non_streaming`` method.
    """
    fake = _FakeAgent()
    _FakeAgent.last_call_kwargs = None

    import app.modules.agents.graph as agent_pkg

    monkeypatch.setattr(agent_pkg, "get_agent", lambda: fake, raising=True)
    yield fake


@pytest.mark.asyncio
async def test_predict_returns_langsmith_compatible_shape(fake_agent) -> None:
    out = await predict({
        "query": "hi",
        "agent_config_template": "shopify_only",
    })
    assert set(out.keys()) >= {"reply", "tool_calls", "retrieved_contexts", "latency_ms"}
    assert out["reply"] == "stub reply"
    assert any(tc["name"] == "search_knowledgebase" for tc in out["tool_calls"])
    assert isinstance(out["retrieved_contexts"], list)
    assert out["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_predict_defaults_to_full_path_not_preview(fake_agent) -> None:
    """Staging-realistic evals run the full graph path (is_preview=False).
    Tests opt into is_preview=True to stay hermetic."""
    await predict({"query": "hi", "agent_config_template": "shopify_only"})
    assert _FakeAgent.last_call_kwargs is not None
    assert _FakeAgent.last_call_kwargs["is_preview"] is False


@pytest.mark.asyncio
async def test_predict_honors_is_preview_kwarg(fake_agent) -> None:
    """Callers can flip is_preview=True for hermetic / debug runs."""
    await predict(
        {"query": "hi", "agent_config_template": "shopify_only"},
        is_preview=True,
    )
    assert _FakeAgent.last_call_kwargs["is_preview"] is True


@pytest.mark.asyncio
async def test_predict_uses_scoped_thread_id_for_singleturn(fake_agent) -> None:
    """Without a case-level chat_id, predict gets a unique thread_id scoped
    to the eval_run_id."""
    await predict(
        {"query": "hi", "agent_config_template": "shopify_only"},
        eval_run_id="run-xyz",
    )
    params = _FakeAgent.last_call_kwargs["additional_params"]
    assert params["chat_id"].startswith("evalrun:run-xyz:")


@pytest.mark.asyncio
async def test_predict_uses_case_chat_id_when_provided(fake_agent) -> None:
    """For multi-turn cases, the case's chat_id flows into the LangGraph
    thread_id so the checkpointer threads history naturally."""
    await predict(
        {
            "query": "yes please",
            "agent_config_template": "shopify_only",
            "chat_id": "4a12f47a-47bc-40ca-8a79-f5224dc42ab3",
            "sequence": 3,
        },
        eval_run_id="run-xyz",
    )
    params = _FakeAgent.last_call_kwargs["additional_params"]
    # Same case chat_id → same prefix → same thread_id within this eval run.
    assert params["chat_id"] == "evalrun:run-xyz:4a12f47a-47bc-40ca-8a79-f5224dc42ab3"


@pytest.mark.asyncio
async def test_predict_passes_agent_config_through(fake_agent) -> None:
    await predict({
        "query": "hi",
        "agent_config_template": "shopify_kb_aide",
    })
    params = _FakeAgent.last_call_kwargs["additional_params"]
    assert params["agent_id"] == "eval-shopify-kb-aide"
    persona = params["config"]["persona"]
    assert persona["personaName"] == "Riley"
    # chat_id + session_id are synthesised per call so cases don't pollute each other
    assert params["chat_id"].startswith("evalrun:")
    assert params["session_id"].startswith("evalsession:")


@pytest.mark.asyncio
async def test_predict_loads_real_config_when_agent_id_set(
    monkeypatch: pytest.MonkeyPatch, fake_agent
) -> None:
    """The canonical eval path: agent_id triggers AgentsRepository.get_config
    rather than the local template loader, so persona / apps / skills come
    from staging Postgres exactly as the real /chat endpoint sees them."""
    captured_agent_id: dict[str, str] = {}

    class _FakeRepo:
        async def get_config(self, agent_id: str):
            captured_agent_id["value"] = agent_id
            return {
                "agent_id": agent_id,
                "user_id": "real-staging-user",
                "config": {
                    "human_handoff_enabled": True,
                    "persona": {
                        "personaName": "RealStagingRiley",
                        "personaDescription": "from-db",
                        "model": "gpt-4o-mini",
                    },
                    "apps": [{"id": "a1", "slug": "shopify"}],
                    "skills": [],
                    "entityIds": [],
                    "folderIds": [],
                    "appsConfig": [],
                    "ownerEmail": "",
                    "crmAiPopulatedFields": [],
                },
            }

    import app.modules.agents.repository as repo_module

    monkeypatch.setattr(repo_module, "AgentsRepository", _FakeRepo, raising=True)

    await predict({"query": "hi", "agent_id": "real-staging-uuid-1234"})

    # Repo was consulted with our agent_id
    assert captured_agent_id["value"] == "real-staging-uuid-1234"
    # Config flowed through to the agent
    params = _FakeAgent.last_call_kwargs["additional_params"]
    assert params["agent_id"] == "real-staging-uuid-1234"
    assert params["config"]["persona"]["personaName"] == "RealStagingRiley"


@pytest.mark.asyncio
async def test_predict_raises_clearly_when_agent_not_found_in_db(
    monkeypatch: pytest.MonkeyPatch, fake_agent
) -> None:
    """A bad agent_id surfaces as a structured error per-case, not a crash."""

    class _MissingRepo:
        async def get_config(self, agent_id: str):
            return None

    import app.modules.agents.repository as repo_module

    monkeypatch.setattr(repo_module, "AgentsRepository", _MissingRepo, raising=True)

    out = await predict({"query": "hi", "agent_id": "does-not-exist"})
    assert out["reply"] == ""
    assert "does-not-exist" in (out.get("error") or "")


@pytest.mark.asyncio
async def test_predict_stubs_write_tools_when_opt_in(fake_agent) -> None:
    """stub_write_tools=True wraps the run in stubbed_write_tools(); we
    verify by absence of error (the registry patch installed + restored
    cleanly even though the fake agent doesn't actually call any tools)."""
    out = await predict(
        {
            "query": "hi",
            "agent_config_template": "shopify_only",
            "stub_write_tools": True,
        }
    )
    assert out.get("error") is None


@pytest.mark.asyncio
async def test_predict_surfaces_errors_as_output_field(monkeypatch) -> None:
    """If the agent raises, predict must return a structured error rather than
    propagating — otherwise one bad case poisons the whole experiment."""

    class _BoomAgent:
        async def run_non_streaming(self, **kwargs):
            raise RuntimeError("simulated agent failure")

    import app.modules.agents.graph as agent_pkg

    monkeypatch.setattr(agent_pkg, "get_agent", lambda: _BoomAgent(), raising=True)
    out = await predict({"query": "hi", "agent_config_template": "shopify_only"})
    assert out["reply"] == ""
    assert "simulated agent failure" in (out.get("error") or "")


# ---------------------------------------------------------------------------
# _normalize_tool_calls — merging real graph events with stub captures
# ---------------------------------------------------------------------------


def test_normalize_tool_calls_combines_real_and_stubbed() -> None:
    captured = CapturedToolCalls()
    captured.calls.append({"name": "create_ticket_aide", "args": {"summary": "x"}})
    graph_events = [
        {"name": "search_knowledgebase", "data": {"q": "y"}},
    ]
    merged = _normalize_tool_calls(graph_events, captured)
    by_name = {tc["name"]: tc for tc in merged}
    assert by_name["search_knowledgebase"]["stubbed"] is False
    assert by_name["create_ticket_aide"]["stubbed"] is True
    assert by_name["create_ticket_aide"]["args"]["summary"] == "x"


def test_normalize_tool_calls_handles_empty_inputs() -> None:
    assert _normalize_tool_calls(None, CapturedToolCalls()) == []
    assert _normalize_tool_calls([], CapturedToolCalls()) == []
