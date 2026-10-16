"""Four LLM-as-judge evaluators: RAG triad plus answer correctness.

G-Eval form-filling scoring: each judge is given a precise criterion
definition plus a pinned chain-of-thought procedure, reasons through the
specific case, and ends its response with a bare ``Score: X`` line
(``X`` a single digit 1-5). Rather than trusting that printed digit at face
value, we read the model's token-level logprobs at that position and compute
a probability-weighted score — ``sum(p(s) * s)`` over the valid score
digits — then rescale from ``[1, 5]`` to the ``[0, 1]`` convention the rest
of the eval system expects. This surfaces the model's actual confidence: a
case where it's 90% sure of a 5 and 10% unsure whether it's a 4 lands at
~0.975, not a flat 1.0.

If logprobs are missing or the terminal token isn't a clean digit (the
form-filling slot wasn't isolated), each judge falls back to parsing the
printed digit directly at full confidence — the pre-G-Eval behavior.

Why not RAGAS: we tried, but their library hard-imports a Vertex AI shim
that was removed from langchain-community 0.3+. Rather than carry a compat
shim for a sunsetting dependency, we own the prompts directly here. They're
the same shape RAGAS uses (claim-extraction-then-entailment for faithfulness,
question-generation-then-similarity for relevancy, per-chunk usefulness for
context precision) but executed as plain ChatOpenAI calls.

Failures degrade gracefully: any exception → score=0.0 with the message in
the comment field. Never raises — one flaky case shouldn't poison an
experiment.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import app_settings

logger = logging.getLogger(__name__)

# Default judge model. Cheaper / weaker models drift on edge cases — gpt-5.4-mini
# is the right level of capability for grading. Override via env if you need
# to swap for a different vendor or a fine-tuned scorer later.
JUDGE_MODEL = "gpt-5.4-mini"


# ---------------------------------------------------------------------------
# Shared LLM + G-Eval scoring helpers
# ---------------------------------------------------------------------------


_GEVAL_LLM_SINGLETON: Any = None


def _geval_judge_llm() -> ChatOpenAI:
    """Single cached ChatOpenAI for all judges. Saves connection setup per case.

    Configured for G-Eval form-filling: ``logprobs``/``top_logprobs`` so we
    can read the score token's confidence, and a nonzero temperature so that
    distribution isn't degenerate (temperature=0 collapses ~100% of the mass
    onto one token, defeating the point of weighting). No forced JSON — the
    score must land as a bare, isolated digit token so its logprobs are
    readable. Re-uses the same OpenAI credentials the agent uses.
    """
    global _GEVAL_LLM_SINGLETON
    if _GEVAL_LLM_SINGLETON is None:
        _GEVAL_LLM_SINGLETON = ChatOpenAI(
            model=JUDGE_MODEL,
            api_key=app_settings.OPENAI_API_KEY,
            base_url=app_settings.OPENAI_API_BASE,
            temperature=0.7,
            logprobs=True,
            top_logprobs=5,
        )
    return _GEVAL_LLM_SINGLETON


def reset_geval_llm() -> None:
    """Test hook — clear the cached LLM so monkeypatches stick between cases."""
    global _GEVAL_LLM_SINGLETON
    _GEVAL_LLM_SINGLETON = None


_VALID_SCORE_TOKENS = {"1", "2", "3", "4", "5"}
_SCORE_LINE_RE = re.compile(r"Score:\s*([1-5])\s*$")
# Trailing characters tolerated between the score digit and the true end of
# the completion (whitespace, sentence-ending punctuation) — anything else
# means the model didn't actually end on the digit, so the slot can't be
# trusted even though a bare digit token exists somewhere in the reasoning.
_TRIVIAL_TRAILING_CHARS = set(" \n\t.!?:,")


def _response_text(response: Any) -> str:
    content = getattr(response, "content", "") or ""
    if isinstance(content, list):
        # Some providers return list-of-blocks; join the text parts.
        content = "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return content


def _extract_score_logprobs(response: Any) -> dict[str, float] | None:
    """Find the terminal ``Score: X`` digit's token and its renormalized
    probability distribution over ``{1..5}``.

    Scans the response's token logprobs from the end. Tokens that are only
    whitespace/punctuation are skipped; the first bare-digit token reached
    this way is the form-filling slot. If real content (not just trivial
    trailing punctuation) is found before any digit, the model didn't end on
    the digit as instructed — we bail out to ``None`` rather than risk
    matching an unrelated digit earlier in the reasoning text (e.g. a chunk
    number or an order id mentioned after the intended "Score:" line).

    The chosen token's own logprob plus its ``top_logprobs`` alternates give
    a distribution over nearby scores; probability mass for any digit that
    appears more than once (distinct token-text variants stripping to the
    same digit) is summed rather than overwritten. Non-digit alternates are
    dropped and the remainder renormalized. Returns ``None`` if the response
    carries no usable logprobs, so callers can fall back to the printed
    digit at face value.
    """
    lp = (getattr(response, "response_metadata", None) or {}).get("logprobs") or {}
    tokens = lp.get("content") or []
    for tok in reversed(tokens):
        text = str(tok.get("token") or "").strip()
        if text in _VALID_SCORE_TOKENS:
            probs: dict[str, float] = {}
            for entry_text, entry_logprob in [(text, tok.get("logprob", 0.0))] + [
                (str(alt.get("token") or "").strip(), alt.get("logprob", 0.0))
                for alt in (tok.get("top_logprobs") or [])
            ]:
                if entry_text in _VALID_SCORE_TOKENS:
                    probs[entry_text] = probs.get(entry_text, 0.0) + math.exp(entry_logprob)
            total = sum(probs.values())
            return {k: v / total for k, v in probs.items()} if total > 0 else None
        if text and not all(c in _TRIVIAL_TRAILING_CHARS for c in text):
            return None
    return None


def _weighted_score_0_1(prob_by_digit: dict[str, float]) -> float:
    """``sum(p(s) * s)`` over ``{1..5}``, rescaled from ``[1, 5]`` to ``[0, 1]``."""
    weighted = sum(int(digit) * p for digit, p in prob_by_digit.items())
    return (weighted - 1) / 4


def _fallback_score_from_text(content: str) -> float:
    """Degrade path when logprobs are unusable: parse the printed digit and
    treat it as full-confidence (matches pre-G-Eval behavior)."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty judge response")
    match = _SCORE_LINE_RE.search(content.strip())
    if not match:
        raise ValueError(f"no 'Score: X' line in judge response: {content[:200]!r}")
    return (int(match.group(1)) - 1) / 4


async def _call_geval_judge(system: str, user: str) -> Any:
    """One-shot LLM call returning the full response (content + logprobs)."""
    return await _geval_judge_llm().ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=user),
    ])


async def _run_geval_judge(key: str, system: str, user: str) -> dict[str, Any]:
    """Shared call+score+format path for all four G-Eval judges."""
    try:
        response = await _call_geval_judge(system, user)
        content = _response_text(response)
        prob_by_digit = _extract_score_logprobs(response)
        score = (
            _weighted_score_0_1(prob_by_digit)
            if prob_by_digit is not None
            else _fallback_score_from_text(content)
        )
        rationale = content.rsplit("Score:", 1)[0].strip()
        return _result(key, score, rationale or f"score {score:.2f}")
    except Exception as e:  # noqa: BLE001
        logger.exception("%s judge failed", key)
        return _fail(key, f"{type(e).__name__}: {e}")


def _clamp01(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    if x < 0:
        return 0.0
    if x > 1:
        return 1.0
    return x


def _result(key: str, score: float, comment: str) -> dict[str, Any]:
    return {
        "key": key,
        "score": round(_clamp01(score), 4),
        "comment": comment[:1000],
    }


def _fail(key: str, reason: str) -> dict[str, Any]:
    return _result(key, 0.0, f"judge failed: {reason}")


def _extract_run_io(run) -> tuple[str, str, list[str]]:
    """Pull (query, reply, retrieved_contexts) out of a LangSmith Run.

    ``aevaluate`` traces the target by its argument name, so the example
    inputs land nested under that name: ``run.inputs == {"inputs": {...}}``
    (our target is ``_predict(inputs)``). Unwrap that one level before reading
    ``query`` — otherwise every query-dependent judge (relevancy, context
    precision) sees an empty query and scores 0.
    """
    raw = getattr(run, "inputs", None) or {}
    inner = raw.get("inputs")
    inputs = inner if isinstance(inner, dict) else raw
    outputs = getattr(run, "outputs", None) or {}
    query = str(inputs.get("query") or "")
    reply = str(outputs.get("reply") or "")
    contexts = [
        c for c in (outputs.get("retrieved_contexts") or []) if isinstance(c, str) and c.strip()
    ]
    return query, reply, contexts


_SCORE_FORMAT_INSTRUCTIONS = (
    "Walk through the steps above against the specific query, context, and "
    "reply you're given. Write your reasoning concisely, then end your "
    "response with exactly one line in this exact format, with nothing "
    "after it:\n"
    "Score: X\n"
    "where X is a single integer from 1 to 5."
)


# ---------------------------------------------------------------------------
# Faithfulness — claims in the reply must be entailed by retrieved context
# ---------------------------------------------------------------------------


_FAITHFULNESS_DEFINITION = (
    "Criterion: Faithfulness\n"
    "Definition: the degree to which every factual claim in the agent's "
    "reply is supported by the retrieved context. A claim is 'supported' if "
    "the context states it or clearly implies it; 'contradicted' if the "
    "context states otherwise; 'unverifiable' if the context is silent on "
    "it. Ignore conversational filler (greetings, apologies, transitions) — "
    "only judge claims about products, policies, orders, prices, "
    "availability, etc."
)

_FAITHFULNESS_COT_STEPS = (
    "Evaluation steps:\n"
    "1. Read the retrieved context and note every fact it explicitly states.\n"
    "2. List each discrete factual claim in the agent's reply (ignore "
    "greetings, apologies, and other filler).\n"
    "3. For each claim, classify it as: supported, contradicted, or "
    "unverifiable (context is silent).\n"
    "4. Contradictions are the most severe finding. Unverifiable claims are "
    "a lesser concern — the agent may be drawing on the query itself or "
    "common sense, not fabricating.\n"
    "5. Map to a score:\n"
    "   - 5: every claim supported, no contradictions, no unverifiable "
    "specifics\n"
    "   - 4: all claims consistent with context; at most one minor "
    "unverifiable detail (not a contradiction)\n"
    "   - 3: several unverifiable claims but no outright contradictions\n"
    "   - 2: exactly one contradiction, or fabricated specifics (price, "
    "policy detail, order fact) not present anywhere in context\n"
    "   - 1: multiple contradictions, or the reply invents specific facts "
    "wholesale"
)

_FAITHFULNESS_SYSTEM = (
    f"You evaluate whether an AI agent's reply is grounded in retrieved "
    f"context.\n\n{_FAITHFULNESS_DEFINITION}\n\n{_FAITHFULNESS_COT_STEPS}\n\n"
    f"{_SCORE_FORMAT_INSTRUCTIONS}"
)


async def faithfulness_evaluator(run, example) -> dict[str, Any]:
    """Is every factual claim in the reply backed by retrieved context?"""
    query, reply, contexts = _extract_run_io(run)
    if not reply.strip():
        return _result("faithfulness", 0.0, "empty reply")
    if not contexts:
        # No retrieval happened. Treat as vacuously 1.0 for conversational
        # turns (greetings, "yes please" confirmations) — the trajectory
        # judge catches "should have retrieved but didn't" misses.
        return _result(
            "faithfulness",
            1.0,
            "no retrieval — faithfulness vacuously satisfied (see trajectory judge)",
        )

    context_block = "\n\n---\n\n".join(contexts)
    user_msg = (
        f"User query: {query}\n\n"
        f"Retrieved context:\n{context_block}\n\n"
        f"Agent reply: {reply}\n"
    )
    return await _run_geval_judge("faithfulness", _FAITHFULNESS_SYSTEM, user_msg)


# ---------------------------------------------------------------------------
# Response relevancy — does the reply address the user's intent?
# ---------------------------------------------------------------------------


_RELEVANCY_DEFINITION = (
    "Criterion: Relevancy\n"
    "Definition: the degree to which the agent's reply addresses what the "
    "user actually asked, independent of whether the content is factually "
    "correct. For conversational turns (greetings, thanks, acknowledgements), "
    "a reply that matches the conversational register counts as fully "
    "relevant."
)

_RELEVANCY_COT_STEPS = (
    "Evaluation steps:\n"
    "1. Identify the user's specific intent in the query (or, for a "
    "conversational turn, what register match looks like).\n"
    "2. Check whether the reply actually engages with that intent, or "
    "deflects, over-generalizes, or ignores it.\n"
    "3. Distinguish 'on-topic but incomplete' from 'generic deflection' "
    "from 'fully off-topic.'\n"
    "4. Map to a score:\n"
    "   - 5: directly and fully addresses the specific intent (or "
    "perfectly matches conversational register)\n"
    "   - 4: addresses the intent with a minor omission or hedge\n"
    "   - 3: partially relevant — covers part of the intent, or is "
    "tangential\n"
    "   - 2: mostly off-topic but gestures at the general subject\n"
    "   - 1: off-topic, generic deflection, or unhelpful refusal"
)

_RELEVANCY_SYSTEM = (
    f"You evaluate whether an AI agent's reply addresses the user's "
    f"query.\n\n{_RELEVANCY_DEFINITION}\n\n{_RELEVANCY_COT_STEPS}\n\n"
    f"{_SCORE_FORMAT_INSTRUCTIONS}"
)


async def response_relevancy_evaluator(run, example) -> dict[str, Any]:
    """Does the reply address the user's query?"""
    query, reply, _ = _extract_run_io(run)
    if not reply.strip():
        return _result("response_relevancy", 0.0, "empty reply")
    if not query.strip():
        return _result("response_relevancy", 0.0, "empty query — cannot judge relevancy")

    user_msg = f"User query: {query}\n\nAgent reply: {reply}\n"
    return await _run_geval_judge("response_relevancy", _RELEVANCY_SYSTEM, user_msg)


# ---------------------------------------------------------------------------
# Context precision — were the retrieved chunks useful?
# ---------------------------------------------------------------------------


_CONTEXT_PRECISION_DEFINITION = (
    "Criterion: Context Precision\n"
    "Definition: the fraction of retrieved chunks that were useful (or "
    "could plausibly contribute) to answering the user's query. Irrelevant "
    "chunks are a retrieval-quality signal even if the agent's reply "
    "ignored them."
)

_CONTEXT_PRECISION_COT_STEPS = (
    "Evaluation steps:\n"
    "1. Read the user query to determine what information would be "
    "relevant.\n"
    "2. For each retrieved chunk, independently judge whether it is "
    "on-topic and useful, or noise — judge each chunk on its own merits, "
    "not through the lens of what the agent's reply used.\n"
    "3. Count useful chunks vs. total chunks.\n"
    "4. Map the fraction to a score:\n"
    "   - 5: all chunks useful/on-topic\n"
    "   - 4: all but one chunk useful (only applies with 3+ chunks)\n"
    "   - 3: roughly half the chunks useful, half noise\n"
    "   - 2: only one chunk useful, the rest noise\n"
    "   - 1: no chunks useful for the query"
)

_CONTEXT_PRECISION_SYSTEM = (
    f"You evaluate whether context retrieved for a user query was "
    f"useful.\n\n{_CONTEXT_PRECISION_DEFINITION}\n\n"
    f"{_CONTEXT_PRECISION_COT_STEPS}\n\n{_SCORE_FORMAT_INSTRUCTIONS}"
)


async def context_precision_evaluator(run, example) -> dict[str, Any]:
    """Were the retrieved chunks actually relevant to the query?"""
    query, reply, contexts = _extract_run_io(run)
    if not contexts:
        return _result(
            "context_precision",
            1.0,
            "no retrieval — context precision vacuously satisfied",
        )
    if not query.strip():
        return _result("context_precision", 0.0, "empty query — cannot judge precision")

    chunks_block = "\n\n---\n\n".join(f"[Chunk {i+1}]\n{c}" for i, c in enumerate(contexts))
    user_msg = (
        f"User query: {query}\n\n"
        f"Retrieved chunks:\n{chunks_block}\n\n"
        f"Agent reply: {reply or '(empty)'}\n"
    )
    return await _run_geval_judge("context_precision", _CONTEXT_PRECISION_SYSTEM, user_msg)


# ---------------------------------------------------------------------------
# Answer correctness — does the reply satisfy the per-case rubric?
# ---------------------------------------------------------------------------


_ANSWER_CORRECTNESS_DEFINITION = (
    "Criterion: Answer Correctness\n"
    "Definition: the degree to which the agent's reply satisfies a per-case "
    "rubric describing what a correct reply must contain and must not do. "
    "The rubric is not a verbatim reference answer — different phrasing is "
    "fine as long as the rubric's requirements are met."
)

# Rubric-agnostic: the specific facts to check live in the per-case rubric
# text (passed in the user message), not here — these steps describe the
# general procedure so they can be pinned once and reused across every case.
_ANSWER_CORRECTNESS_COT_STEPS = (
    "Evaluation steps:\n"
    "1. Parse the rubric into discrete requirements ('must state X') and "
    "prohibitions ('must not do Y').\n"
    "2. For each requirement, check whether the reply satisfies it, "
    "allowing for paraphrase — do not penalize different wording.\n"
    "3. Check the reply for fabricated facts not licensed by the rubric.\n"
    "4. Check for any violated prohibitions.\n"
    "5. Do not penalize: phrasing differences, extra brevity, friendlier "
    "tone, or reasonable elaboration consistent with the rubric.\n"
    "6. Map findings to a score:\n"
    "   - 5: every requirement satisfied, no prohibited behavior, no "
    "invented facts\n"
    "   - 4: all required facts present; only a minor tone/next-step/"
    "structure requirement is missed\n"
    "   - 3: most required facts present, one minor gap\n"
    "   - 2: some required facts present, others missing, OR one "
    "prohibited behavior triggered\n"
    "   - 1: required facts missing, fabricated information, or a "
    "prohibition violated outright"
)

_ANSWER_CORRECTNESS_SYSTEM = (
    f"You evaluate whether an AI agent's reply satisfies a per-case "
    f"rubric.\n\n{_ANSWER_CORRECTNESS_DEFINITION}\n\n"
    f"{_ANSWER_CORRECTNESS_COT_STEPS}\n\n{_SCORE_FORMAT_INSTRUCTIONS}"
)


async def answer_correctness_evaluator(run, example) -> dict[str, Any]:
    """Score the reply against the per-case rubric in ``example.outputs.golden_reply``.

    ``golden_reply`` is a prose rubric (not a verbatim reference): a short
    description of what a correct answer must contain and what it must avoid.
    Cases without a rubric are skipped (``score=None``) so they don't
    contribute to the experiment average — opt in by adding the field.
    """
    query, reply, _ = _extract_run_io(run)
    rubric = str((getattr(example, "outputs", None) or {}).get("golden_reply") or "").strip()
    if not rubric:
        # Skipped — no opinion. LangSmith excludes None-scored feedback from
        # aggregates, so this doesn't pull averages around.
        return {
            "key": "answer_correctness",
            "score": None,
            "comment": "no rubric provided — skipped",
        }
    if not reply.strip():
        return _result("answer_correctness", 0.0, "empty reply")

    user_msg = (
        f"User query: {query}\n\n"
        f"Rubric (what a correct reply must satisfy):\n{rubric}\n\n"
        f"Agent reply: {reply}\n"
    )
    return await _run_geval_judge("answer_correctness", _ANSWER_CORRECTNESS_SYSTEM, user_msg)
