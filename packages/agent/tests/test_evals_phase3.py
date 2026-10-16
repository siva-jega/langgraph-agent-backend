"""Phase 3 tests: programmatic judges + custom LLM judges.

LLM judges call gpt-5.4-mini via a G-Eval form-filling prompt (chain-of-thought
reasoning ending in a bare ``Score: X`` line, weighted by that token's
logprobs). We stub the one call each judge makes via ``monkeypatch`` so the
suite is hermetic and doesn't hit the network.
"""

from __future__ import annotations

import pytest

from app.modules.evals.judges import (
    answer_correctness_evaluator,
    context_precision_evaluator,
    default_judges,
    faithfulness_evaluator,
    response_relevancy_evaluator,
    trajectory_evaluator,
)


# ---------------------------------------------------------------------------
# LangSmith Run / Example test doubles
# ---------------------------------------------------------------------------


class _Run:
    def __init__(self, inputs: dict | None = None, outputs: dict | None = None):
        self.inputs = inputs or {}
        self.outputs = outputs or {}


class _Example:
    def __init__(self, outputs: dict | None = None):
        self.outputs = outputs or {}


def _run(reply: str = "", tool_calls: list[dict] | None = None,
         retrieved: list[str] | None = None, query: str = "") -> _Run:
    return _Run(
        inputs={"query": query},
        outputs={
            "reply": reply,
            "tool_calls": tool_calls or [],
            "retrieved_contexts": retrieved or [],
        },
    )


def _example(expected_tools: list[str] | None = None,
             golden_reply: str | None = None) -> _Example:
    return _Example(outputs={
        "expected_tools": expected_tools or [],
        "golden_reply": golden_reply,
    })


# ---------------------------------------------------------------------------
# Trajectory judge — programmatic, no LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trajectory_matched_exact() -> None:
    out = await trajectory_evaluator(
        _run(tool_calls=[{"name": "get_products"}]),
        _example(expected_tools=["get_products"]),
    )
    assert out["key"] == "trajectory"
    assert out["score"] == 1.0
    assert out["comment"] == "matched"


@pytest.mark.asyncio
async def test_trajectory_no_tools_expected_and_none_called() -> None:
    out = await trajectory_evaluator(_run(tool_calls=[]), _example(expected_tools=[]))
    assert out["score"] == 1.0


@pytest.mark.asyncio
async def test_trajectory_no_tools_expected_but_some_called() -> None:
    out = await trajectory_evaluator(
        _run(tool_calls=[{"name": "search_knowledgebase"}]),
        _example(expected_tools=[]),
    )
    assert out["score"] == 0.0
    assert "search_knowledgebase" in out["comment"]


@pytest.mark.asyncio
async def test_trajectory_missing_one_of_two_expected() -> None:
    out = await trajectory_evaluator(
        _run(tool_calls=[{"name": "get_products"}]),
        _example(expected_tools=["get_products", "search_knowledgebase"]),
    )
    assert out["score"] == 0.5


@pytest.mark.asyncio
async def test_trajectory_unexpected_extra_drags_score_down() -> None:
    out = await trajectory_evaluator(
        _run(tool_calls=[{"name": "get_products"}, {"name": "search_knowledgebase"}]),
        _example(expected_tools=["get_products"]),
    )
    # Expected found (base=1.0), one unexpected (penalty=0.1) → 0.9
    assert out["score"] == 0.9


@pytest.mark.asyncio
async def test_trajectory_skips_malformed_tool_call_entries() -> None:
    out = await trajectory_evaluator(
        _run(tool_calls=[{"name": ""}, {"not_a_name": "x"}, {"name": "get_products"}]),
        _example(expected_tools=["get_products"]),
    )
    assert out["score"] == 1.0


# ---------------------------------------------------------------------------
# LLM judges — _call_geval_judge is monkey-patched so no API call happens
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_geval_llm() -> None:
    """Clear the cached ChatOpenAI between tests so monkeypatches stick."""
    from app.modules.evals.judges import llm

    llm.reset_geval_llm()
    yield
    llm.reset_geval_llm()


class _FakeJudgeResponse:
    """Stand-in for the ``AIMessage`` a real ``ainvoke`` call returns."""

    def __init__(self, content: str, logprobs_content: list[dict] | None = None):
        self.content = content
        self.response_metadata = (
            {"logprobs": {"content": logprobs_content}} if logprobs_content is not None else {}
        )


def _score_digit_token(digit: str, alt_logprobs: dict[str, float] | None = None) -> dict:
    """One logprobs ``content`` entry for a bare score digit, with optional
    alternate-digit logprobs for testing the weighted-average math."""
    top_logprobs = [{"token": digit, "logprob": 0.0}]
    for alt_digit, lp in (alt_logprobs or {}).items():
        top_logprobs.append({"token": alt_digit, "logprob": lp})
    return {"token": digit, "logprob": 0.0, "top_logprobs": top_logprobs}


def _stub_geval_call(
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    logprobs_content: list[dict] | None = None,
):
    """Patch ``_call_geval_judge`` to return a canned response. Captures what
    the judge sent so tests can assert on prompt shape."""
    captured: dict = {}

    async def fake(system: str, user: str) -> _FakeJudgeResponse:
        captured["system"] = system
        captured["user"] = user
        return _FakeJudgeResponse(content, logprobs_content)

    from app.modules.evals.judges import llm

    monkeypatch.setattr(llm, "_call_geval_judge", fake)
    return captured


# ----- shared G-Eval scoring helpers -----


def test_extract_score_rejects_trailing_content() -> None:
    """A model that appends text after 'Score: 5' (against instructions) must
    not have some unrelated later digit (e.g. an order number) mistaken for
    the score — that would silently produce a wrong-but-confident result."""
    from app.modules.evals.judges import llm

    tokens = [
        {"token": "Score", "logprob": -0.01, "top_logprobs": []},
        {"token": ":", "logprob": -0.0, "top_logprobs": []},
        {"token": " 5", "logprob": -0.02, "top_logprobs": [{"token": " 5", "logprob": -0.02}]},
        {"token": ".", "logprob": -0.01, "top_logprobs": []},
        {"token": " order", "logprob": -0.3, "top_logprobs": []},
        {"token": " #", "logprob": -0.2, "top_logprobs": []},
        {"token": " 4", "logprob": -0.05, "top_logprobs": [{"token": " 4", "logprob": -0.05}]},
        {"token": " shipped", "logprob": -0.2, "top_logprobs": []},
        {"token": ".", "logprob": -0.1, "top_logprobs": []},
    ]
    response = _FakeJudgeResponse("reasoning...\nScore: 5. order #4 shipped.", tokens)
    assert llm._extract_score_logprobs(response) is None


def test_extract_score_accepts_trailing_punctuation() -> None:
    """A trailing period/newline after the digit is fine — only real content
    should disqualify the slot."""
    from app.modules.evals.judges import llm

    tokens = [
        {"token": "Score", "logprob": -0.01, "top_logprobs": []},
        {"token": ":", "logprob": -0.0, "top_logprobs": []},
        {"token": " 5", "logprob": -0.02, "top_logprobs": [{"token": " 5", "logprob": -0.02}]},
        {"token": ".", "logprob": -0.01, "top_logprobs": []},
        {"token": "\n", "logprob": -0.01, "top_logprobs": []},
    ]
    response = _FakeJudgeResponse("reasoning...\nScore: 5.\n", tokens)
    probs = llm._extract_score_logprobs(response)
    assert probs == {"5": 1.0}


def test_extract_score_combines_token_variants() -> None:
    """Two distinct token-text variants that both strip to the same digit
    (e.g. differing byte encodings) should have their probability mass
    combined, not have one silently overwrite the other."""
    from app.modules.evals.judges import llm

    tokens = [{
        "token": " 4",
        "logprob": -0.7,
        "top_logprobs": [
            {"token": " 4", "logprob": -0.7},
            {"token": "4", "logprob": -0.7},  # same digit, different token text
            {"token": " 3", "logprob": -3.0},
        ],
    }]
    response = _FakeJudgeResponse("Score: 4", tokens)
    probs = llm._extract_score_logprobs(response)
    assert probs is not None
    assert probs["4"] > probs["3"]
    assert abs(sum(probs.values()) - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_judge_fails_soft_on_totally_malformed_output(monkeypatch) -> None:
    """No logprobs and no parseable 'Score: X' line anywhere — both the
    weighted path and the fallback path must fail, and the judge must
    degrade to score=0.0 rather than raise."""
    _stub_geval_call(monkeypatch, "I'm not sure how to score this one.")
    out = await faithfulness_evaluator(
        _run(reply="x", retrieved=["y"], query="q"),
        _example(),
    )
    assert out["score"] == 0.0
    assert "judge failed" in out["comment"]


# ----- faithfulness -----


@pytest.mark.asyncio
async def test_faithfulness_falls_back_to_printed_digit_without_logprobs(monkeypatch) -> None:
    """No usable logprobs → parse the printed digit at face value."""
    _stub_geval_call(monkeypatch, "all claims are supported by the context.\nScore: 4")
    out = await faithfulness_evaluator(
        _run(reply="The hoodie is ₹2,499.", retrieved=["Midnight Hoodie ₹2,499"], query="how much?"),
        _example(),
    )
    assert out["key"] == "faithfulness"
    assert out["score"] == 0.75  # (4 - 1) / 4
    assert "all claims are supported" in out["comment"]


@pytest.mark.asyncio
async def test_faithfulness_weights_score_logprobs(monkeypatch) -> None:
    """A `Score: 5` completion with residual probability on `4` should land
    just under the top anchor, not flatten to a hard 1.0 — this is the
    entire point of reading logprobs instead of trusting the printed digit."""
    tokens = [_score_digit_token("5", alt_logprobs={"4": -3.66, "3": -7.5})]
    tokens[0]["logprob"] = -0.03
    _stub_geval_call(
        monkeypatch,
        "every claim checks out against the context.\nScore: 5",
        logprobs_content=tokens,
    )
    out = await faithfulness_evaluator(
        _run(reply="The hoodie is ₹2,499.", retrieved=["Midnight Hoodie ₹2,499"], query="how much?"),
        _example(),
    )
    assert out["key"] == "faithfulness"
    assert 0.97 < out["score"] < 1.0


@pytest.mark.asyncio
async def test_faithfulness_vacuously_passes_without_retrieval() -> None:
    """No retrieval → 1.0 with explanatory comment. The trajectory judge is
    responsible for catching "should have retrieved" misses."""
    out = await faithfulness_evaluator(
        _run(reply="hey, how can I help?", retrieved=[]),
        _example(),
    )
    assert out["score"] == 1.0
    assert "no retrieval" in out["comment"].lower()


@pytest.mark.asyncio
async def test_faithfulness_empty_reply_is_zero() -> None:
    out = await faithfulness_evaluator(_run(reply=""), _example())
    assert out["score"] == 0.0
    assert "empty reply" in out["comment"].lower()


@pytest.mark.asyncio
async def test_faithfulness_judge_failure_returns_zero(monkeypatch) -> None:
    """LLM exceptions must not bubble — the run still completes with score=0."""

    async def boom(system: str, user: str):
        raise RuntimeError("rate limited")

    from app.modules.evals.judges import llm

    monkeypatch.setattr(llm, "_call_geval_judge", boom)
    out = await faithfulness_evaluator(
        _run(reply="x", retrieved=["y"], query="q"),
        _example(),
    )
    assert out["score"] == 0.0
    assert "rate limited" in out["comment"]


@pytest.mark.asyncio
async def test_faithfulness_prompt_carries_query_reply_and_context(monkeypatch) -> None:
    """The judge prompt must contain all three signals it needs to grade."""
    captured = _stub_geval_call(monkeypatch, "looks fine.\nScore: 5")
    await faithfulness_evaluator(
        _run(reply="The price is ₹2,499.", retrieved=["Midnight Hoodie ₹2,499"], query="how much?"),
        _example(),
    )
    assert "how much?" in captured["user"]
    assert "Midnight Hoodie" in captured["user"]
    assert "₹2,499" in captured["user"]


# ----- response_relevancy -----


@pytest.mark.asyncio
async def test_response_relevancy_returns_score_from_judge(monkeypatch) -> None:
    _stub_geval_call(monkeypatch, "directly addresses the query.\nScore: 4")
    out = await response_relevancy_evaluator(
        _run(reply="Yes — Midnight Hoodie ₹2,499", query="got any black hoodies?"),
        _example(),
    )
    assert out["key"] == "response_relevancy"
    assert out["score"] == 0.75  # (4 - 1) / 4


@pytest.mark.asyncio
async def test_response_relevancy_empty_reply_is_zero() -> None:
    out = await response_relevancy_evaluator(_run(reply="", query="hi"), _example())
    assert out["score"] == 0.0


@pytest.mark.asyncio
async def test_response_relevancy_empty_query_is_zero() -> None:
    out = await response_relevancy_evaluator(_run(reply="hi", query=""), _example())
    assert out["score"] == 0.0


# ----- context_precision -----


@pytest.mark.asyncio
async def test_context_precision_returns_score_from_judge(monkeypatch) -> None:
    _stub_geval_call(monkeypatch, "roughly half the chunks were useful.\nScore: 3")
    out = await context_precision_evaluator(
        _run(reply="answer", retrieved=["chunk1", "chunk2"], query="q"),
        _example(),
    )
    assert out["key"] == "context_precision"
    assert out["score"] == 0.5  # (3 - 1) / 4


@pytest.mark.asyncio
async def test_context_precision_vacuously_passes_without_retrieval() -> None:
    out = await context_precision_evaluator(_run(reply="hi", retrieved=[]), _example())
    assert out["score"] == 1.0


@pytest.mark.asyncio
async def test_context_precision_prompt_labels_each_chunk(monkeypatch) -> None:
    """Multiple chunks must be enumerated so the judge can score per-chunk usefulness."""
    captured = _stub_geval_call(monkeypatch, "all chunks useful.\nScore: 5")
    await context_precision_evaluator(
        _run(reply="answer", retrieved=["alpha", "beta"], query="q"),
        _example(),
    )
    assert "Chunk 1" in captured["user"]
    assert "Chunk 2" in captured["user"]
    assert "alpha" in captured["user"]
    assert "beta" in captured["user"]


# ----- answer_correctness -----


@pytest.mark.asyncio
async def test_answer_correctness_returns_score_from_judge(monkeypatch) -> None:
    _stub_geval_call(monkeypatch, "every requirement in the rubric is met.\nScore: 5")
    out = await answer_correctness_evaluator(
        _run(reply="30 days, 14 for EU.", query="what's your return window?"),
        _example(golden_reply="must state a 30 day window, 14 for EU"),
    )
    assert out["key"] == "answer_correctness"
    assert out["score"] == 1.0  # (5 - 1) / 4


@pytest.mark.asyncio
async def test_answer_correctness_skipped_without_rubric() -> None:
    out = await answer_correctness_evaluator(
        _run(reply="30 days", query="what's your return window?"),
        _example(golden_reply=None),
    )
    assert out["score"] is None
    assert "skipped" in out["comment"]


@pytest.mark.asyncio
async def test_answer_correctness_empty_reply_is_zero() -> None:
    out = await answer_correctness_evaluator(
        _run(reply="", query="q"),
        _example(golden_reply="must say something"),
    )
    assert out["score"] == 0.0


# ---------------------------------------------------------------------------
# default_judges bundle
# ---------------------------------------------------------------------------


def test_default_judges_bundle_lists_five_evaluators() -> None:
    judges = default_judges()
    names = [j.__name__ for j in judges]
    assert names == [
        "trajectory_evaluator",
        "faithfulness_evaluator",
        "response_relevancy_evaluator",
        "context_precision_evaluator",
        "answer_correctness_evaluator",
    ]


@pytest.mark.asyncio
async def test_default_judges_all_return_langsmith_evaluator_shape(monkeypatch) -> None:
    """Every judge in the default bundle must return ``{key, score, comment}``
    so LangSmith ``aevaluate()`` can attach the scores to the experiment."""
    _stub_geval_call(monkeypatch, "middling case.\nScore: 3")

    run = _run(
        reply="some reply",
        tool_calls=[{"name": "get_products"}],
        retrieved=["chunk"],
        query="some query",
    )
    example = _example(
        expected_tools=["get_products"],
        golden_reply="some reply",
    )

    for judge in default_judges():
        result = await judge(run, example)
        assert set(result.keys()) >= {"key", "score", "comment"}, judge.__name__
        assert isinstance(result["score"], float), judge.__name__
        assert 0.0 <= result["score"] <= 1.0, judge.__name__
