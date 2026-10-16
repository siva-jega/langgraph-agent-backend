"""Unit tests for ConnectorStepExecutor."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from workflow_service.engine.nodes.connector import ConnectorStepExecutor


def _make_tool(return_value=None):
    tool = MagicMock()
    tool.name = "test_action"
    tool.ainvoke = AsyncMock(return_value=return_value or {"result": "ok"})
    return tool


def _make_registry(tool=None):
    reg = MagicMock()
    reg.get_tool_by_name.return_value = tool
    reg.get_tools_by_provider.return_value = []
    reg.get_provider_names.return_value = ["test-provider"]
    return reg


def _make_config(params=None, workspace_id="ws-1", user_id="u-1"):
    return {
        "provider": "test-provider",
        "action": "test_action",
        "params": params or {},
        "workspaceId": workspace_id,
        "userId": user_id,
    }


async def test_params_resolved_and_passed_to_tool(monkeypatch) -> None:
    tool = _make_tool({"data": "value"})
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", _make_registry(tool))
    monkeypatch.setattr(
        "workflow_service.engine.nodes.connector._fetch_workspace_apps",
        AsyncMock(return_value=[]),
    )

    ctx = {"trigger": {"output": {"orderId": "ORD-1"}}}
    config = _make_config(params={"order_id": "{{ $trigger.output.orderId }}"})

    await ConnectorStepExecutor().execute(config, ctx)

    tool.ainvoke.assert_called_once()
    call_params = tool.ainvoke.call_args[0][0]
    assert call_params["order_id"] == "ORD-1"


async def test_runnable_config_assembled_with_workspace_and_user(monkeypatch) -> None:
    tool = _make_tool()
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", _make_registry(tool))
    monkeypatch.setattr(
        "workflow_service.engine.nodes.connector._fetch_workspace_apps",
        AsyncMock(return_value=[]),
    )

    await ConnectorStepExecutor().execute(_make_config(workspace_id="ws-abc", user_id="u-xyz"), {})

    runnable_config = tool.ainvoke.call_args[0][1]
    assert runnable_config["configurable"]["workspace_id"] == "ws-abc"
    assert runnable_config["configurable"]["user_id"] == "u-xyz"


async def test_apps_fetched_when_absent_from_config(monkeypatch) -> None:
    tool = _make_tool()
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", _make_registry(tool))
    fetch_apps = AsyncMock(return_value=[{"slug": "shopify", "id": "app-1"}])
    monkeypatch.setattr("workflow_service.engine.nodes.connector._fetch_workspace_apps", fetch_apps)

    config = _make_config(workspace_id="ws-1")
    # No 'apps' key in config → should trigger fetch
    await ConnectorStepExecutor().execute(config, {})

    fetch_apps.assert_called_once_with("ws-1")


async def test_apps_not_fetched_when_provided_in_config(monkeypatch) -> None:
    tool = _make_tool()
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", _make_registry(tool))
    fetch_apps = AsyncMock(return_value=[])
    monkeypatch.setattr("workflow_service.engine.nodes.connector._fetch_workspace_apps", fetch_apps)

    config = {**_make_config(), "apps": [{"slug": "shopify"}]}
    await ConnectorStepExecutor().execute(config, {})

    fetch_apps.assert_not_called()


async def test_dict_result_returned_directly(monkeypatch) -> None:
    tool = _make_tool({"status": "shipped"})
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", _make_registry(tool))
    monkeypatch.setattr(
        "workflow_service.engine.nodes.connector._fetch_workspace_apps", AsyncMock(return_value=[])
    )

    result = await ConnectorStepExecutor().execute(_make_config(), {})
    assert result == {"status": "shipped"}


async def test_tuple_result_normalized(monkeypatch) -> None:
    tool = _make_tool(("content text", {"artifact": "data"}))
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", _make_registry(tool))
    monkeypatch.setattr(
        "workflow_service.engine.nodes.connector._fetch_workspace_apps", AsyncMock(return_value=[])
    )

    result = await ConnectorStepExecutor().execute(_make_config(), {})
    assert "content" in result
    assert "artifact" in result


async def test_list_result_normalized(monkeypatch) -> None:
    tool = _make_tool([1, 2, 3])
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", _make_registry(tool))
    monkeypatch.setattr(
        "workflow_service.engine.nodes.connector._fetch_workspace_apps", AsyncMock(return_value=[])
    )

    result = await ConnectorStepExecutor().execute(_make_config(), {})
    assert result == {"items": [1, 2, 3]}


async def test_str_result_normalized(monkeypatch) -> None:
    tool = _make_tool("plain string")
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", _make_registry(tool))
    monkeypatch.setattr(
        "workflow_service.engine.nodes.connector._fetch_workspace_apps", AsyncMock(return_value=[])
    )

    result = await ConnectorStepExecutor().execute(_make_config(), {})
    assert result == {"result": "plain string"}


async def test_pydantic_model_inside_tuple_normalized(monkeypatch) -> None:
    """Pydantic model returned as part of a (content, artifact) tuple is serialized via model_dump."""
    from pydantic import BaseModel

    class FakeModel(BaseModel):
        value: int = 42

    tool = _make_tool(("text content", FakeModel()))
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", _make_registry(tool))
    monkeypatch.setattr(
        "workflow_service.engine.nodes.connector._fetch_workspace_apps", AsyncMock(return_value=[])
    )

    result = await ConnectorStepExecutor().execute(_make_config(), {})
    assert result["content"] == "text content"
    assert result["artifact"] == {"value": 42}


async def test_missing_provider_or_action_raises(monkeypatch) -> None:
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", _make_registry())
    monkeypatch.setattr(
        "workflow_service.engine.nodes.connector._fetch_workspace_apps", AsyncMock(return_value=[])
    )

    with pytest.raises(ValueError, match="provider"):
        await ConnectorStepExecutor().execute({"provider": "", "action": ""}, {})


async def test_unknown_action_raises(monkeypatch) -> None:
    reg = _make_registry(tool=None)
    monkeypatch.setattr("workflow_service.engine.nodes.connector.registry", reg)
    monkeypatch.setattr(
        "workflow_service.engine.nodes.connector._fetch_workspace_apps", AsyncMock(return_value=[])
    )

    with pytest.raises(ValueError, match="not found"):
        await ConnectorStepExecutor().execute(
            {"provider": "shopify", "action": "nonexistent_action"},
            {},
        )
