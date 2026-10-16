from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from workflow_service.modules.triggers import service
from workflow_service.modules.triggers.models import TriggerUpdate
from workflow_service.modules.triggers import repository


@pytest.fixture
def trigger_context(monkeypatch):
    triggers = Mock()
    triggers.update = AsyncMock()
    triggers.delete = AsyncMock()
    workflows = Mock()
    workflows.verify_ownership = AsyncMock(return_value=True)
    monkeypatch.setattr(service, "_trigger_repo", triggers)
    monkeypatch.setattr(service, "_workflow_repo", workflows)
    return triggers, workflows


def trigger_row():
    return {
        "id": "trigger-1",
        "workflowDefinitionId": "workflow-1",
        "type": "WEBHOOK",
        "configJson": {},
        "webhookUrl": "/webhooks/wf/example",
        "agentId": None,
        "filterJson": None,
        "isActive": False,
        "createdAt": datetime.now(timezone.utc),
    }


@pytest.mark.asyncio
async def test_update_scopes_trigger_lookup_to_workflow(trigger_context):
    triggers, _ = trigger_context
    triggers.update.return_value = trigger_row()

    result = await service.update_trigger(
        "workflow-1", "workspace-1", "trigger-1", TriggerUpdate(isActive=False)
    )

    triggers.update.assert_awaited_once_with(
        "workflow-1", "trigger-1", {"isActive": False}
    )
    assert result.id == "trigger-1"


@pytest.mark.asyncio
async def test_update_returns_not_found_for_trigger_from_another_workflow(
    trigger_context,
):
    triggers, _ = trigger_context
    triggers.update.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        await service.update_trigger(
            "workflow-1", "workspace-1", "foreign-trigger", TriggerUpdate()
        )

    assert exc_info.value.status_code == 404
    triggers.update.assert_awaited_once_with("workflow-1", "foreign-trigger", {})


@pytest.mark.asyncio
async def test_delete_scopes_trigger_to_workflow(trigger_context):
    triggers, _ = trigger_context
    triggers.delete.return_value = True

    result = await service.delete_trigger("workflow-1", "workspace-1", "trigger-1")

    triggers.delete.assert_awaited_once_with("workflow-1", "trigger-1")
    assert result == {"success": True}


@pytest.mark.asyncio
async def test_delete_returns_not_found_for_trigger_from_another_workflow(
    trigger_context,
):
    triggers, _ = trigger_context
    triggers.delete.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        await service.delete_trigger(
            "workflow-1", "workspace-1", "foreign-trigger"
        )

    assert exc_info.value.status_code == 404
    triggers.delete.assert_awaited_once_with("workflow-1", "foreign-trigger")


@pytest.mark.asyncio
async def test_update_does_not_reach_repository_without_workflow_access(
    trigger_context,
):
    triggers, workflows = trigger_context
    workflows.verify_ownership.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        await service.update_trigger(
            "workflow-1", "other-workspace", "trigger-1", TriggerUpdate(isActive=True)
        )

    assert exc_info.value.status_code == 404
    triggers.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_does_not_reach_repository_without_workflow_access(
    trigger_context,
):
    triggers, workflows = trigger_context
    workflows.verify_ownership.return_value = False

    with pytest.raises(HTTPException) as exc_info:
        await service.delete_trigger("workflow-1", "other-workspace", "trigger-1")

    assert exc_info.value.status_code == 404
    triggers.delete.assert_not_awaited()


class FakeConnection:
    def __init__(self, row=None, execute_result="DELETE 1"):
        self.row = row
        self.execute_result = execute_result
        self.query = None
        self.args = None

    async def fetchrow(self, query, *args):
        self.query, self.args = query, args
        return self.row

    async def execute(self, query, *args):
        self.query, self.args = query, args
        return self.execute_result


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return False


def install_fake_connection(monkeypatch, connection):
    pg = Mock()
    pg.acquire = lambda: FakeAcquire(connection)
    monkeypatch.setattr(repository, "get_pg", lambda: pg)


@pytest.mark.asyncio
async def test_repository_update_includes_workflow_scope(monkeypatch):
    connection = FakeConnection(row=trigger_row())
    install_fake_connection(monkeypatch, connection)

    await repository.TriggerRepository().update(
        "workflow-1", "trigger-1", {"isActive": False}
    )

    assert 'WHERE id = $2 AND "workflowDefinitionId" = $3' in connection.query
    assert connection.args == (False, "trigger-1", "workflow-1")


@pytest.mark.asyncio
async def test_empty_repository_update_still_checks_workflow_scope(monkeypatch):
    connection = FakeConnection(row=None)
    install_fake_connection(monkeypatch, connection)

    result = await repository.TriggerRepository().update(
        "workflow-1", "foreign-trigger", {}
    )

    assert result is None
    assert 'WHERE id = $1 AND "workflowDefinitionId" = $2' in connection.query
    assert connection.args == ("foreign-trigger", "workflow-1")


@pytest.mark.asyncio
async def test_repository_delete_includes_workflow_scope(monkeypatch):
    connection = FakeConnection(execute_result="DELETE 0")
    install_fake_connection(monkeypatch, connection)

    deleted = await repository.TriggerRepository().delete(
        "workflow-1", "foreign-trigger"
    )

    assert not deleted
    assert 'WHERE id = $1 AND "workflowDefinitionId" = $2' in connection.query
    assert connection.args == ("foreign-trigger", "workflow-1")
