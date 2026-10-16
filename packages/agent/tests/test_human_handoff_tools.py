"""Tests for human handoff interrupt form (HANDOFF CRM aiPopulated filtering)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from common.tools.providers.human_handoff.tools import human_handoff_trigger


@pytest.mark.asyncio
class TestHumanHandoffTrigger:
    async def test_user_rejected(self) -> None:
        with patch(
            "common.tools.providers.human_handoff.tools.interrupt",
            return_value={"decisions": [{"type": "reject"}]},
        ):
            out = await human_handoff_trigger.ainvoke(
                {"priority": "MEDIUM", "summary": "Need help"},
                config={
                    "configurable": {
                        "chat_id": "c1",
                        "agent_id": "a1",
                        "session_id": "s1",
                    }
                },
            )
        assert isinstance(out, str)
        assert "reject" in out.lower()

    async def test_ai_populated_handoff_keys_removed_from_form_schema(self) -> None:
        approved = {
            "decisions": [{"type": "approve"}],
            "fields": {"visitorName": "Ada", "visitorEmail": "ada@example.com"},
        }
        skill_form = {
            "form": {
                "fields": [
                    {
                        "id": "priority",
                        "label": "Priority",
                        "mapsTo": "priority",
                        "required": False,
                    },
                    {
                        "id": "reason",
                        "label": "Reason",
                        "mapsTo": "reason",
                        "required": False,
                    },
                    {
                        "id": "visitorName",
                        "label": "Name",
                        "mapsTo": "visitorName",
                        "required": True,
                    },
                    {
                        "id": "visitorEmail",
                        "label": "Email",
                        "mapsTo": "visitorEmail",
                        "required": True,
                    },
                ]
            }
        }
        crm_ai = [
            {"entity": "HANDOFF", "key": "priority", "label": "P", "type": "SELECT", "required": False},
            {"entity": "HANDOFF", "key": "reason", "label": "R", "type": "TEXT", "required": False},
        ]
        with (
            patch(
                "common.tools.providers.human_handoff.tools.interrupt",
                return_value=approved,
            ) as mock_interrupt,
            patch(
                "common.tools.providers.human_handoff.tools.trigger_handoff_api",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await human_handoff_trigger.ainvoke(
                {"priority": "HIGH", "summary": "Billing issue"},
                config={
                    "configurable": {
                        "chat_id": "00000000-0000-4000-8000-000000000001",
                        "agent_id": "00000000-0000-4000-8000-000000000002",
                        "session_id": "00000000-0000-4000-8000-000000000003",
                        "user_id": "u1",
                        "skill_config": skill_form,
                        "crmAiPopulatedFields": crm_ai,
                    }
                },
            )

        mock_interrupt.assert_called_once()
        iv = mock_interrupt.call_args.kwargs["value"]
        form_fields = (iv.get("tool_args") or {}).get("form_schema", {}).get(
            "fields", []
        )
        ids = {f.get("id") for f in form_fields}
        assert ids == {"visitorName", "visitorEmail"}
        assert "priority" not in ids
        assert "reason" not in ids
        assert iv.get("tool_name") == "human_handoff_trigger"
        ta = iv.get("tool_args") or {}
        assert "priority" not in ta and "summary" not in ta

    async def test_all_skill_fields_ai_only_falls_back_to_defaults(self) -> None:
        """If filtering removes everything, keep default name/email collect flow."""
        approved = {
            "decisions": [{"type": "approve"}],
            "fields": {"visitorName": "Bob", "visitorEmail": "bob@example.com"},
        }
        skill_form = {
            "form": {
                "fields": [
                    {
                        "id": "priority",
                        "label": "Priority",
                        "mapsTo": "priority",
                        "required": False,
                    },
                ]
            }
        }
        crm_ai = [
            {"entity": "HANDOFF", "key": "priority", "label": "P", "type": "SELECT", "required": False},
        ]
        with (
            patch(
                "common.tools.providers.human_handoff.tools.interrupt",
                return_value=approved,
            ) as mock_interrupt,
            patch(
                "common.tools.providers.human_handoff.tools.trigger_handoff_api",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await human_handoff_trigger.ainvoke(
                {"priority": "LOW", "summary": "x"},
                config={
                    "configurable": {
                        "chat_id": "00000000-0000-4000-8000-000000000001",
                        "agent_id": "00000000-0000-4000-8000-000000000002",
                        "session_id": "00000000-0000-4000-8000-000000000003",
                        "skill_config": skill_form,
                        "crmAiPopulatedFields": crm_ai,
                    }
                },
            )

        iv = mock_interrupt.call_args.kwargs["value"]
        form_fields = (iv.get("tool_args") or {}).get("form_schema", {}).get(
            "fields", []
        )
        ids = {f.get("id") for f in form_fields}
        assert ids == {"visitorName", "visitorEmail"}
