"""Integration-style tests for Aide LangChain tools (interrupt + DB mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from common.tools.providers.aide.models import CreateCustomerResult, CreateTicketResult
from common.tools.providers.aide.tools import (
    create_lead_aide,
    create_lead_aide_channel,
    create_ticket_aide,
    create_ticket_aide_channel,
    get_aide_tools,
)


@pytest.mark.asyncio
class TestCreateTicketAide:
    async def test_user_declined(self) -> None:
        with patch(
            "common.tools.providers.aide.tools.interrupt",
            return_value={"decisions": [{"type": "reject"}]},
        ):
            out = await create_ticket_aide.ainvoke(
                {
                    "summary": "S",
                    "description": "D",
                    "widget_id": "00000000-0000-4000-8000-000000000001",
                    "chat_id": "00000000-0000-4000-8000-000000000002",
                },
                config={"configurable": {"user_id": "u1"}},
            )
        # LangChain ainvoke returns content only (artifact not surfaced here).
        assert isinstance(out, str)
        assert "declined" in out.lower()

    async def test_user_approved_creates_ticket(self) -> None:
        approved = {
            "decisions": [{"type": "approve"}],
            "fields": {"phone": "+1999"},
        }
        skill_cfg = {
            "create_ticket_aide": {
                "form": {
                    "fields": [
                        {
                            "id": "phone",
                            "label": "Phone",
                            "mapsTo": "custom.phone",
                            "required": True,
                        }
                    ]
                }
            }
        }
        with (
            patch(
                "common.tools.providers.aide.tools.interrupt",
                return_value=approved,
            ),
            patch(
                "common.tools.providers.aide.tools.create_widget_ticket",
                new_callable=AsyncMock,
                return_value=CreateTicketResult(
                    success=True,
                    message="ok",
                    ticket_id="tic-x",
                ),
            ) as mock_create,
        ):
            out = await create_ticket_aide.ainvoke(
                {
                    "summary": "Need help",
                    "description": "Details here",
                    "widget_id": "00000000-0000-4000-8000-000000000001",
                    "chat_id": "00000000-0000-4000-8000-000000000002",
                },
                config={
                    "configurable": {
                        "user_id": "u1",
                        "skill_configs": skill_cfg,
                    }
                },
            )

        mock_create.assert_awaited_once()
        call_kw = mock_create.await_args.kwargs
        data = call_kw["data"]
        assert data["phone"] == "+1999"
        assert data["subject"] == "Need help"
        assert data["status"] == "OPEN"
        assert data["priority"] == "MEDIUM"

        assert out == "ok"

    async def test_user_approved_missing_required_skips_db(self) -> None:
        approved = {
            "decisions": [{"type": "approve"}],
            "fields": {},
        }
        skill_cfg = {
            "create_ticket_aide": {
                "form": {
                    "fields": [
                        {
                            "id": "phone",
                            "label": "Phone",
                            "required": True,
                        }
                    ]
                }
            }
        }
        with (
            patch(
                "common.tools.providers.aide.tools.interrupt",
                return_value=approved,
            ),
            patch(
                "common.tools.providers.aide.tools.create_widget_ticket",
                new_callable=AsyncMock,
            ) as mock_create,
        ):
            out = await create_ticket_aide.ainvoke(
                {
                    "summary": "S",
                    "description": "D",
                    "widget_id": "00000000-0000-4000-8000-000000000001",
                    "chat_id": "00000000-0000-4000-8000-000000000002",
                },
                config={
                    "configurable": {
                        "user_id": "u1",
                        "skill_configs": skill_cfg,
                    }
                },
            )

        mock_create.assert_not_called()
        assert "Missing required field" in out
        assert "Phone" in out

    async def test_ai_populated_keys_removed_from_interrupt_form(self) -> None:
        """Stale skill config must not surface CRM keys the DB marks as model-filled."""
        approved = {
            "decisions": [{"type": "approve"}],
            "fields": {"phone": "+1999"},
        }
        skill_cfg = {
            "create_ticket_aide": {
                "form": {
                    "fields": [
                        {
                            "id": "priority",
                            "label": "Priority",
                            "mapsTo": "priority",
                            "required": True,
                        },
                        {
                            "id": "phone",
                            "label": "Phone",
                            "mapsTo": "custom.phone",
                            "required": True,
                        },
                    ]
                }
            }
        }
        crm_ai = [
            {
                "entity": "TICKET",
                "key": "priority",
                "label": "Priority",
                "type": "SELECT",
                "required": True,
            }
        ]
        with (
            patch(
                "common.tools.providers.aide.tools.interrupt",
                return_value=approved,
            ) as mock_interrupt,
            patch(
                "common.tools.providers.aide.tools.create_widget_ticket",
                new_callable=AsyncMock,
                return_value=CreateTicketResult(
                    success=True,
                    message="ok",
                    ticket_id="tic-x",
                ),
            ),
        ):
            await create_ticket_aide.ainvoke(
                {
                    "summary": "Need help",
                    "description": "Details here",
                    "widget_id": "00000000-0000-4000-8000-000000000001",
                    "chat_id": "00000000-0000-4000-8000-000000000002",
                },
                config={
                    "configurable": {
                        "user_id": "u1",
                        "skill_configs": skill_cfg,
                        "crmAiPopulatedFields": crm_ai,
                    }
                },
            )

        mock_interrupt.assert_called_once()
        iv = mock_interrupt.call_args.kwargs["value"]
        form_fields = (iv.get("tool_args") or {}).get("form_schema", {}).get(
            "fields", []
        )
        assert len(form_fields) == 1
        assert form_fields[0].get("id") == "phone"

    async def test_whatsapp_channel_creates_ticket_without_interrupt(self) -> None:
        with patch(
            "common.tools.providers.aide.tools.create_widget_ticket",
            new_callable=AsyncMock,
            return_value=CreateTicketResult(
                success=True,
                message="ok",
                ticket_id="tic-meta",
            ),
        ) as mock_create:
            out = await create_ticket_aide_channel.ainvoke(
                {
                    "summary": "Need human help",
                    "description": "Customer wants support",
                    "requester_name": "Alice",
                    "requester_email": "alice@example.com",
                    "reason": "WhatsApp support follow-up requested",
                },
                config={
                    "configurable": {
                        "channel": "whatsapp",
                        "user_id": "u1",
                        "agent_id": "f7b69040-4bf8-4592-bad7-c413016a6f22",
                        "chat_id": "379377c6-675a-4a24-b829-4833487aa6de:+15551234567",
                        "session_id": "00000000-0000-4000-8000-000000000002",
                        "sender_phone": "+15551234567",
                        "sender_display_name": "Alice",
                        "whatsapp_phone_number_id": "1107888972403693",
                    }
                },
            )

        mock_create.assert_awaited_once()
        call_kw = mock_create.await_args.kwargs
        data = call_kw["data"]
        assert data["subject"] == "Need human help"
        assert data["description"] == "Customer wants support"
        assert data["reason"] == "WhatsApp support follow-up requested"
        assert data["requesterName"] == "Alice"
        assert data["requesterPhone"] == "+15551234567"
        assert data["requesterEmail"] == "alice@example.com"
        assert data["channel"] == "whatsapp"
        assert data["chatId"] == "379377c6-675a-4a24-b829-4833487aa6de:+15551234567"
        assert data["sessionId"] == "00000000-0000-4000-8000-000000000002"
        assert out == "ok"

    async def test_whatsapp_channel_persists_llm_supplied_name(self) -> None:
        """LLM-supplied requester_name is the only source — runtime context is
        no longer auto-merged in. Phone is still auto-pulled from the runtime."""
        with patch(
            "common.tools.providers.aide.tools.create_widget_ticket",
            new_callable=AsyncMock,
            return_value=CreateTicketResult(
                success=True,
                message="ok",
                ticket_id="tic-meta",
            ),
        ) as mock_create:
            out = await create_ticket_aide_channel.ainvoke(
                {
                    "summary": "Need human help",
                    "description": "Customer wants support",
                    "requester_name": "Bob",
                    "requester_email": "bob@example.com",
                    "reason": "Customer asked for help",
                },
                config={
                    "configurable": {
                        "channel": "whatsapp",
                        "user_id": "u1",
                        "agent_id": "f7b69040-4bf8-4592-bad7-c413016a6f22",
                        "chat_id": "chat-id",
                        "session_id": "00000000-0000-4000-8000-000000000002",
                        "sender_phone": "+15551234567",
                        "whatsapp_phone_number_id": "1107888972403693",
                    }
                },
            )

        data = mock_create.await_args.kwargs["data"]
        assert data["reason"] == "Customer asked for help"
        assert data["requesterName"] == "Bob"
        assert data["requesterPhone"] == "+15551234567"
        assert data["requesterEmail"] == "bob@example.com"
        assert out == "ok"


@pytest.mark.asyncio
class TestCreateLeadAide:
    async def test_user_approved_maps_custom(self) -> None:
        approved = {
            "decisions": [{"type": "approve"}],
            "fields": {"company_field": "MegaCorp"},
        }
        skill_cfg = {
            "create_lead_aide": {
                "form": {
                    "fields": [
                        {
                            "id": "company_field",
                            "mapsTo": "lead.companyName",
                            "required": True,
                        }
                    ]
                }
            }
        }
        with (
            patch(
                "common.tools.providers.aide.tools.interrupt",
                return_value=approved,
            ),
            patch(
                "common.tools.providers.aide.tools.create_widget_customer",
                new_callable=AsyncMock,
                return_value=CreateCustomerResult(
                    success=True,
                    message="ok",
                    customer_id="lead-x",
                ),
            ) as mock_create,
        ):
            out = await create_lead_aide.ainvoke(
                {
                    "intent": "Pricing",
                    "widget_id": "00000000-0000-4000-8000-000000000001",
                    "chat_id": "00000000-0000-4000-8000-000000000002",
                },
                config={
                    "configurable": {
                        "user_id": "u1",
                        "skill_configs": skill_cfg,
                    }
                },
            )

        mock_create.assert_awaited_once()
        assert mock_create.await_args.kwargs["data"]["companyName"] == "MegaCorp"
        assert mock_create.await_args.kwargs["data"]["status"] == "NEW"
        assert out == "ok"

    async def test_ai_populated_customer_keys_removed_from_interrupt_form(self) -> None:
        approved = {
            "decisions": [{"type": "approve"}],
            "fields": {"company_field": "Acme"},
        }
        skill_cfg = {
            "create_lead_aide": {
                "form": {
                    "fields": [
                        {
                            "id": "intent",
                            "label": "Intent",
                            "mapsTo": "intent",
                            "required": True,
                        },
                        {
                            "id": "status",
                            "label": "Status",
                            "mapsTo": "status",
                            "required": True,
                        },
                        {
                            "id": "company_field",
                            "label": "Company",
                            "mapsTo": "companyName",
                            "required": True,
                        },
                    ]
                }
            }
        }
        crm_ai = [
            {"entity": "CUSTOMER", "key": "intent", "label": "I", "type": "TEXT", "required": False},
            {"entity": "CUSTOMER", "key": "status", "label": "S", "type": "SELECT", "required": True},
        ]
        with (
            patch(
                "common.tools.providers.aide.tools.interrupt",
                return_value=approved,
            ) as mock_interrupt,
            patch(
                "common.tools.providers.aide.tools.create_widget_customer",
                new_callable=AsyncMock,
                return_value=CreateCustomerResult(
                    success=True,
                    message="ok",
                    customer_id="lead-x",
                ),
            ),
        ):
            await create_lead_aide.ainvoke(
                {
                    "intent": "Pricing",
                    "widget_id": "00000000-0000-4000-8000-000000000001",
                    "chat_id": "00000000-0000-4000-8000-000000000002",
                },
                config={
                    "configurable": {
                        "user_id": "u1",
                        "skill_configs": skill_cfg,
                        "crmAiPopulatedFields": crm_ai,
                    }
                },
            )

        mock_interrupt.assert_called_once()
        iv = mock_interrupt.call_args.kwargs["value"]
        form_fields = (iv.get("tool_args") or {}).get("form_schema", {}).get(
            "fields", []
        )
        assert len(form_fields) == 1
        assert form_fields[0].get("id") == "company_field"


def test_get_aide_tools_names() -> None:
    """Web graph aide registry exposes interrupt-based tools only."""
    tools = get_aide_tools()
    names = sorted(t.name for t in tools)
    assert names == ["create_lead_aide", "create_ticket_aide"]
