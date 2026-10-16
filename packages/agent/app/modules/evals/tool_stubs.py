"""Stub out write-side tools during evals so retrieval signal is real but no
records get created in CRMs / ticketing / email systems.

Read-only tools (KB search, product search, order status, Gmail search,
calendar list) execute for real, so the eval scores the agent against
actual retrieved data — not a fixture. Side-effecting tools (ticket creation,
lead creation, handoff trigger, send_email, etc.) return a canned success
message and record their arguments into ``CapturedToolCalls`` so the
trajectory judge in Phase 3 can compare actual vs expected.

Wiring: the agent resolves write-side skill tools via
``registry.get_tool_by_name(name)`` (the singleton at
``common/tools/registry.py:203``). This module monkey-patches that method
for the duration of a ``stubbed_write_tools(captured)`` block, then restores
it on exit. Provider lookups (``registry.get_provider``) are left alone —
those return read-only tools (Shopify ``get_products``, etc.).
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from common.tools.registry import registry  # singleton instance — patched in-place


class _OpenStubSchema(BaseModel):
    """Args schema for eval stubs.

    ``extra="allow"`` is the load-bearing part: it accepts whatever args the
    LLM passes (which varies per write-tool) and surfaces them on
    ``model_dump()`` so the stub can record the full call.
    """

    model_config = ConfigDict(extra="allow")


class _OpenSchemaTool(StructuredTool):
    """``StructuredTool`` variant that doesn't short-circuit to empty kwargs
    when the args_schema has no declared fields.

    LangChain's ``StructuredTool._to_args_and_kwargs`` returns ``(), {}`` for
    any args_schema with no declared fields, even when ``extra="allow"`` would
    accept arbitrary fields. We override to validate via the schema (so
    extras are captured) and pass ``model_dump()`` through as kwargs.
    """

    def _to_args_and_kwargs(self, tool_input, tool_call_id):
        if isinstance(tool_input, str):
            return (tool_input,), {}
        if isinstance(tool_input, dict):
            if self.args_schema is not None and isinstance(self.args_schema, type) and issubclass(self.args_schema, BaseModel):
                model = self.args_schema.model_validate(tool_input)
                return (), model.model_dump()
            return (), dict(tool_input)
        return (), {}

logger = logging.getLogger(__name__)


# Tools that write/persist data. Update when new write-side tools land in
# AgentSkills. Read-only tools are intentionally absent here so they execute
# for real and produce real retrieval/observation data for scoring.
WRITE_TOOLS: set[str] = {
    # Aide (web + channel variants)
    "create_ticket_aide",
    "create_lead_aide",
    "create_ticket_aide_channel",
    "create_lead_aide_channel",
    # Human handoff (interrupt-style + channel direct)
    "human_handoff_trigger",
    "human_handoff_trigger_channel",
    # Email / messaging side-effects
    "send_email",
    "send_slack_message",
    # Calendar bookings
    "book_meeting_calendly",
    "book_meeting_calcom",
    "cancel_event_calendly",
    "cancel_booking_calcom",
    "reschedule_booking_calcom",
    # CRM writes
    "create_ticket",  # HubSpot / Zendesk
    "create_notion_database_item",
    "create_airtable_record",
    "update_airtable_record",
}


@dataclass
class CapturedToolCalls:
    """Accumulator for stub invocations during one eval run.

    One instance per case. The runner reads ``calls`` after the agent finishes
    to know which write tools the model attempted to call (and with what args).
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    def reset(self) -> None:
        self.calls.clear()


def _make_stub(tool_name: str, captured: CapturedToolCalls):
    """Build a LangChain tool that accepts arbitrary args and records them.

    We construct a ``StructuredTool`` directly (rather than using ``@tool``)
    because the decorator builds its schema from the function signature; a
    ``**kwargs``-only function yields an empty schema and LangChain strips
    every arg before invocation. ``StructuredTool`` with an explicit
    ``extra="allow"`` Pydantic schema accepts whatever the LLM sends.
    """

    async def _impl(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        captured.calls.append({"name": tool_name, "args": dict(kwargs)})
        logger.info(
            "eval stub captured %s with arg_keys=%s",
            tool_name,
            sorted(kwargs.keys()),
        )
        return (
            f"[stubbed] {tool_name} captured for eval; no side effect ran.",
            {
                "name": "EvalStub",
                "tool": tool_name,
                "data": {"success": True, "stubbed": True, "args": kwargs},
            },
        )

    return _OpenSchemaTool(
        name=tool_name,
        description=f"[EVAL STUB] {tool_name} — captured for eval; no side effect.",
        args_schema=_OpenStubSchema,
        coroutine=_impl,
        response_format="content_and_artifact",
    )


@contextlib.contextmanager
def stubbed_write_tools(
    captured: CapturedToolCalls,
    extra_stubs: set[str] | None = None,
) -> Iterator[CapturedToolCalls]:
    """Monkey-patch the tool registry to return stubs for ``WRITE_TOOLS``.

    Read-only tools are not patched — they execute for real, so RAG metrics
    measure actual retrieval quality.

    Usage:
        captured = CapturedToolCalls()
        with stubbed_write_tools(captured):
            result = await graph.run_non_streaming(...)
        # captured.calls now holds every write-tool invocation, with args
    """
    stub_names = set(WRITE_TOOLS)
    if extra_stubs:
        stub_names.update(extra_stubs)

    original_get_tool_by_name = registry.get_tool_by_name

    def patched_get_tool_by_name(name: str):
        if name in stub_names:
            return _make_stub(name, captured)
        return original_get_tool_by_name(name)

    registry.get_tool_by_name = patched_get_tool_by_name  # type: ignore[assignment]
    try:
        yield captured
    finally:
        registry.get_tool_by_name = original_get_tool_by_name  # type: ignore[assignment]
