"""Unit tests for WaitStepExecutor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from workflow_service.engine.nodes.wait import (
    WaitStepExecutor,
    MAX_WAIT_SECONDS,
    PAUSE_THRESHOLD_SECONDS,
    PauseRequired,
)


async def test_duration_ms_sleeps_correct_seconds(no_sleep) -> None:
    await WaitStepExecutor().execute({"durationMs": 5000}, {})
    assert no_sleep == [5.0]


async def test_duration_ms_over_pause_threshold_raises_pause_required(no_sleep) -> None:
    over_threshold = (PAUSE_THRESHOLD_SECONDS + 60) * 1000  # 4 minutes in ms
    with pytest.raises(PauseRequired) as exc_info:
        await WaitStepExecutor().execute({"durationMs": over_threshold}, {})
    assert exc_info.value.resume_at is not None
    assert no_sleep == []  # no sleep occurred


async def test_output_contains_waited_true_and_duration_ms(no_sleep) -> None:
    result = await WaitStepExecutor().execute({"durationMs": 3000}, {})
    assert result["waited"] is True
    assert result["durationMs"] == 3000


async def test_resume_at_past_does_not_sleep(no_sleep) -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await WaitStepExecutor().execute({"resumeAt": past}, {})
    assert no_sleep == [0]


async def test_resume_at_future_time(monkeypatch, no_sleep) -> None:
    future = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    await WaitStepExecutor().execute({"resumeAt": future}, {})
    # Allow ±2s tolerance around expected 5s
    assert len(no_sleep) == 1
    assert 3.0 <= no_sleep[0] <= 7.0


async def test_resume_at_with_z_suffix_parsed(no_sleep) -> None:
    future = (datetime.now(timezone.utc) + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await WaitStepExecutor().execute({"resumeAt": future}, {})
    assert len(no_sleep) == 1
    assert 3.0 <= no_sleep[0] <= 7.0


async def test_no_config_sleeps_zero(no_sleep) -> None:
    await WaitStepExecutor().execute({}, {})
    assert no_sleep == [0]


async def test_resume_at_far_future_time(no_sleep) -> None:
    far_future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    with pytest.raises(PauseRequired) as exc_info:
        await WaitStepExecutor().execute({"resumeAt": far_future}, {})
    assert exc_info.value.resume_at is not None
    assert no_sleep == []  # no sleep occurred


async def test_duration_ms_resume_at_reflects_full_duration_not_cap(no_sleep) -> None:
    forty_five_min_ms = 45 * 60 * 1000
    now = datetime.now(timezone.utc)
    with pytest.raises(PauseRequired) as exc_info:
        await WaitStepExecutor().execute({"durationMs": forty_five_min_ms}, {})
    resume_at = exc_info.value.resume_at
    delta = (resume_at - now).total_seconds()
    assert 44 * 60 <= delta <= 46 * 60  # ~45 min, not capped to 30
