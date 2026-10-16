"""Unit tests for EndStepExecutor."""

from __future__ import annotations

from workflow_service.engine.nodes import STEP_EXECUTORS, EndStepExecutor


def test_end_executor_registered() -> None:
    assert STEP_EXECUTORS["END"] is EndStepExecutor


async def test_end_returns_empty_dict() -> None:
    result = await EndStepExecutor().execute({}, {})
    assert result == {}


async def test_end_ignores_config_and_context() -> None:
    result = await EndStepExecutor().execute({"anything": "ignored"}, {"ctx": "ignored"})
    assert result == {}
