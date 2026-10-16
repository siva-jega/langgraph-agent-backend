"""Phase 4 tests: prompt version stamping.

Pinning these contracts:
- ``PROMPT_BUILDER_VERSION`` is a non-empty string constant
- ``get_system_prompt_with_meta`` returns ``(prompt, version, hash)`` where
  the hash is a stable SHA256 hex of the rendered prompt
- Two semantically-equivalent configs produce the same hash; a config change
  produces a different hash
- ``run_dataset`` injects the version into the experiment metadata so
  LangSmith can group experiments by prompt version
"""

from __future__ import annotations

import hashlib
import re
from unittest.mock import AsyncMock

import pytest

from app.modules.agents.graph.prompt import (
    PROMPT_BUILDER_VERSION,
    get_system_prompt,
    get_system_prompt_with_meta,
)
from app.modules.evals.runner import run_dataset


# ---------------------------------------------------------------------------
# Version constant
# ---------------------------------------------------------------------------


def test_prompt_builder_version_is_a_non_empty_semver_string() -> None:
    assert isinstance(PROMPT_BUILDER_VERSION, str)
    assert PROMPT_BUILDER_VERSION.strip()
    # Loose semver shape — major.minor.patch with optional pre-release.
    assert re.match(r"^\d+\.\d+\.\d+", PROMPT_BUILDER_VERSION), PROMPT_BUILDER_VERSION


# ---------------------------------------------------------------------------
# get_system_prompt_with_meta
# ---------------------------------------------------------------------------


def _persona_params(persona_name: str = "Riley") -> dict:
    return {
        "persona": {"personaName": persona_name, "personaDescription": "x"},
        "apps": [],
        "skills": [],
        "entityIds": [],
        "folderIds": [],
    }


def test_with_meta_returns_triple() -> None:
    prompt, version, prompt_hash = get_system_prompt_with_meta(_persona_params())
    assert isinstance(prompt, str) and prompt
    assert version == PROMPT_BUILDER_VERSION
    # Hex SHA256: 64 chars, hex only
    assert re.fullmatch(r"[0-9a-f]{64}", prompt_hash), prompt_hash


def test_hash_matches_sha256_of_prompt() -> None:
    """Pin the contract that the hash is over the *rendered* prompt — so
    semantic prompt changes (different persona, different skill list) shift
    the hash without anyone having to bump the version constant."""
    params = _persona_params()
    prompt, _, prompt_hash = get_system_prompt_with_meta(params)
    expected = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert prompt_hash == expected


def test_hash_is_deterministic_across_calls() -> None:
    """Same input → same hash. Otherwise dashboard comparisons drift."""
    params = _persona_params()
    _, _, h1 = get_system_prompt_with_meta(params)
    _, _, h2 = get_system_prompt_with_meta(params)
    assert h1 == h2


def test_hash_changes_when_persona_changes() -> None:
    _, _, h_a = get_system_prompt_with_meta(_persona_params("Alice"))
    _, _, h_b = get_system_prompt_with_meta(_persona_params("Bob"))
    assert h_a != h_b


def test_with_meta_prompt_matches_plain_get_system_prompt() -> None:
    """The meta wrapper must not alter the rendered prompt text — only add
    metadata. Otherwise eval traces diverge from production traces."""
    params = _persona_params()
    plain = get_system_prompt(params)
    meta_prompt, _, _ = get_system_prompt_with_meta(params)
    assert plain == meta_prompt


# ---------------------------------------------------------------------------
# run_dataset injects the version into experiment metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_dataset_stamps_prompt_builder_version_in_experiment_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Phase 4 contract for LangSmith: every experiment carries
    ``prompt_builder_version`` in its metadata so the UI can filter / compare
    by it."""
    captured: dict = {}

    async def fake_aevaluate(predict_fn, **kwargs):
        captured["kwargs"] = kwargs
        return None

    # Stub ``aevaluate`` at the seam where the runner imports it.
    import langsmith.evaluation as lse

    monkeypatch.setattr(lse, "aevaluate", fake_aevaluate, raising=True)

    # Stub Client so the test doesn't need credentials.
    import langsmith

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(langsmith, "Client", _FakeClient, raising=True)

    await run_dataset(
        "web_agent_template_v1",
        experiment_prefix="phase4-test",
        evaluators=[],
        metadata={"git_sha": "abc1234"},
    )

    md = captured["kwargs"]["metadata"]
    assert md["prompt_builder_version"] == PROMPT_BUILDER_VERSION
    # User-supplied metadata is preserved alongside the auto-stamped version.
    assert md["git_sha"] == "abc1234"
    # Dataset name is on every eval run for filtering.
    assert md["dataset"] == "web_agent_template_v1"
    assert md["eval_run"] is True


@pytest.mark.asyncio
async def test_run_dataset_lets_caller_override_prompt_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI runs that want to label experiments with a custom version (e.g.
    pre-release builds) can override the default via metadata."""
    captured: dict = {}

    async def fake_aevaluate(predict_fn, **kwargs):
        captured["kwargs"] = kwargs
        return None

    import langsmith.evaluation as lse

    monkeypatch.setattr(lse, "aevaluate", fake_aevaluate, raising=True)

    import langsmith

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(langsmith, "Client", _FakeClient, raising=True)

    await run_dataset(
        "ds",
        evaluators=[],
        metadata={"prompt_builder_version": "0.99.0-experimental"},
    )

    md = captured["kwargs"]["metadata"]
    assert md["prompt_builder_version"] == "0.99.0-experimental"
