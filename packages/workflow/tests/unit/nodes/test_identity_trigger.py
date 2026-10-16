"""Unit tests for IdentityTriggerExecutor."""

from __future__ import annotations

import pytest

from workflow_service.engine.nodes import STEP_EXECUTORS
from workflow_service.engine.nodes.identity_trigger import IdentityTriggerExecutor

_ALL_TRIGGER_TYPES = [
    "WEBHOOK",
    "SCHEDULED",
    "MANUAL",
    "FORM_SUBMISSION",
    "APP",
    "CONVERSATION_EVENT",
]


@pytest.mark.parametrize("trigger_type", _ALL_TRIGGER_TYPES)
def test_all_trigger_types_map_to_identity_executor(trigger_type: str) -> None:
    assert STEP_EXECUTORS[trigger_type] is IdentityTriggerExecutor


async def test_returns_input_data_verbatim() -> None:
    payload = {"orderId": "ORD-1", "amount": 99}
    ctx = {"__run__": {"inputData": payload}}
    result = await IdentityTriggerExecutor().execute({}, ctx)
    assert result == payload


async def test_non_dict_input_data_returns_empty_dict() -> None:
    ctx = {"__run__": {"inputData": "not-a-dict"}}
    result = await IdentityTriggerExecutor().execute({}, ctx)
    assert result == {}


async def test_missing_run_context_returns_empty_dict() -> None:
    result = await IdentityTriggerExecutor().execute({}, {})
    assert result == {}


async def test_none_input_data_returns_empty_dict() -> None:
    ctx = {"__run__": {"inputData": None}}
    result = await IdentityTriggerExecutor().execute({}, ctx)
    assert result == {}
