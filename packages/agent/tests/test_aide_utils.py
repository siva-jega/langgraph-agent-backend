"""Tests for Aide widget DB helpers (Postgres mocked)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.tools.providers.aide.utils import _describe_bind, create_widget_customer, create_widget_ticket


class _AcquireCM:
    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


def _mock_pg_for_acquire_sequence(*conns: AsyncMock) -> MagicMock:
    queue = list(conns)

    def _acquire() -> _AcquireCM:
        if not queue:
            raise AssertionError("unexpected extra pool.acquire()")
        return _AcquireCM(queue.pop(0))

    mock_pg = MagicMock()
    mock_pg.acquire = MagicMock(side_effect=_acquire)
    return mock_pg


class TestDescribeBind:
    def test_null(self) -> None:
        assert _describe_bind(None) == "null"

    def test_str_preview(self) -> None:
        s = _describe_bind("hi")
        assert "str" in s and "len=2" in s

    def test_long_str_truncates(self) -> None:
        long = "x" * 200
        out = _describe_bind(long, max_str_preview=20)
        assert "len=200" in out
        assert "…" in out


@pytest.mark.asyncio
class TestCreateWidgetTicket:
    async def test_success_chat_already_exists(self) -> None:
        conn_chat = AsyncMock()
        conn_chat.fetchval = AsyncMock(return_value=1)

        conn_ticket = AsyncMock()
        conn_ticket.fetchrow = AsyncMock(return_value={"id": "ticket-uuid-1"})

        mock_pg = _mock_pg_for_acquire_sequence(conn_chat, conn_ticket)

        with patch("common.tools.providers.aide.utils.get_pg", return_value=mock_pg):
            r = await create_widget_ticket(
                widget_id="agent-1",
                chat_id="chat-1",
                platform="aide",
                data={
                    "subject": "Subj",
                    "comment": {"body": "Desc line"},
                    "ref": "ABC",
                },
            )

        assert r.success is True
        assert r.ticket_id == "ticket-uuid-1"
        conn_chat.fetchval.assert_awaited_once()
        conn_ticket.fetchrow.assert_awaited_once()
        bound = conn_ticket.fetchrow.await_args[0][1:]
        assert bound[0] == "agent-1"
        assert bound[1] == "chat-1"
        payload = json.loads(bound[2])
        assert payload["subject"] == "Subj"
        assert payload["ref"] == "ABC"
        assert payload["status"] == "OPEN"
        assert payload["priority"] == "MEDIUM"

    async def test_success_creates_chat_first(self) -> None:
        conn_ensure = AsyncMock()
        conn_ensure.fetchval = AsyncMock(return_value=None)
        conn_ensure.fetchrow = AsyncMock(return_value={"id": "new-chat"})

        conn_ticket = AsyncMock()
        conn_ticket.fetchrow = AsyncMock(return_value={"id": "tic-2"})

        mock_pg = _mock_pg_for_acquire_sequence(conn_ensure, conn_ticket)

        with patch("common.tools.providers.aide.utils.get_pg", return_value=mock_pg):
            r = await create_widget_ticket(
                widget_id="agent-1",
                chat_id="chat-new",
                platform="aide",
                data={
                    "subject": "S",
                    "requesterName": "N",
                    "requesterEmail": "e@e.co",
                    "priority": "LOW",
                },
            )

        assert r.success is True
        assert r.ticket_id == "tic-2"
        assert conn_ensure.fetchrow.await_count == 1

    async def test_insert_returns_no_row(self) -> None:
        conn_chat = AsyncMock()
        conn_chat.fetchval = AsyncMock(return_value=1)
        conn_ticket = AsyncMock()
        conn_ticket.fetchrow = AsyncMock(return_value=None)

        mock_pg = _mock_pg_for_acquire_sequence(conn_chat, conn_ticket)

        with patch("common.tools.providers.aide.utils.get_pg", return_value=mock_pg):
            r = await create_widget_ticket(
                widget_id="bad-agent",
                chat_id="c1",
                platform="aide",
                data={"subject": "S"},
            )

        assert r.success is False
        assert "invalid" in r.message.lower()

    async def test_database_not_configured(self) -> None:
        with patch(
            "common.tools.providers.aide.utils.get_pg",
            side_effect=RuntimeError("Postgres not initialized"),
        ):
            r = await create_widget_ticket(
                widget_id="a",
                chat_id="c",
                platform="aide",
                data={"subject": "S"},
            )
        assert r.success is False
        assert "not configured" in r.message.lower()


@pytest.mark.asyncio
class TestCreateWidgetCustomer:
    async def test_success(self) -> None:
        conn_chat = AsyncMock()
        conn_chat.fetchval = AsyncMock(return_value=1)
        conn_customer = AsyncMock()
        conn_customer.fetchrow = AsyncMock(return_value={"id": "customer-1"})

        mock_pg = _mock_pg_for_acquire_sequence(conn_chat, conn_customer)

        with patch("common.tools.providers.aide.utils.get_pg", return_value=mock_pg):
            r = await create_widget_customer(
                widget_id="agent-1",
                chat_id="c1",
                platform="aide",
                data={
                    "intent": "buy",
                    "name": "L",
                    "email": "l@l.co",
                    "companyName": "Co",
                    "designation": "CEO",
                    "src": "web",
                },
            )

        assert r.success is True
        assert r.customer_id == "customer-1"
        conn_customer.fetchrow.assert_awaited_once()
