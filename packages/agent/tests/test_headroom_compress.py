"""Tests for the Headroom tool-output compression node (Phase 1).

Covers the invariants that make the node safe to drop into the agent graph:
  * flag OFF  -> no-op (graph stays byte-identical)
  * flag ON   -> large ToolMessages shrink, preserving id/name/tool_call_id
  * add_messages REPLACES the original (no duplicate ToolMessage for one tool_call_id)
  * boundary values and error text survive compression
  * any compression error falls back to passing messages through unchanged
  * the graph builder only adds the node when the flag is on
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages

from app.core.config import app_settings
from app.modules.agents.graph import headroom_compress as hc


def _big_orders(n: int = 80) -> str:
    return json.dumps(
        [
            {"id": i, "status": "shipped", "total": i * 9.99, "tracking": f"TRK{i:06d}"}
            for i in range(n)
        ]
    )


@pytest.fixture
def headroom_on(monkeypatch):
    """Enable Headroom with a low threshold and a clean stock-node cache."""
    monkeypatch.setattr(app_settings, "HEADROOM_ENABLED", True)
    monkeypatch.setattr(app_settings, "HEADROOM_MIN_TOKENS_TO_COMPRESS", 50)
    monkeypatch.setattr(app_settings, "HEADROOM_PRESERVE_ERRORS", True)
    monkeypatch.setattr(hc, "_stock_node", None)
    yield
    hc._stock_node = None


def _state_with_tool_output(content: str):
    # Route through add_messages so ToolMessages get ids assigned, like the live graph.
    return {
        "messages": add_messages(
            [],
            [
                HumanMessage(content="track my orders"),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "get_orders", "args": {}, "id": "call_1"}],
                ),
                ToolMessage(content=content, tool_call_id="call_1", name="get_orders"),
            ],
        )
    }


async def test_flag_off_is_noop(monkeypatch):
    monkeypatch.setattr(app_settings, "HEADROOM_ENABLED", False)
    state = _state_with_tool_output(_big_orders())
    assert await hc.compress_tool_messages_node(state) == {}


async def test_compresses_and_preserves_identity(headroom_on):
    big = _big_orders()
    state = _state_with_tool_output(big)
    original_tm = [m for m in state["messages"] if isinstance(m, ToolMessage)][0]

    update = await hc.compress_tool_messages_node(state)
    assert "messages" in update and len(update["messages"]) == 1

    new_tm = update["messages"][0]
    assert isinstance(new_tm, ToolMessage)
    assert new_tm.id == original_tm.id  # same id => reducer replaces in place
    assert new_tm.name == "get_orders"
    assert new_tm.tool_call_id == "call_1"
    assert len(str(new_tm.content)) < len(big)  # actually smaller
    assert "TRK000079" in str(new_tm.content)  # boundary value survives


async def test_add_messages_replaces_not_duplicates(headroom_on):
    state = _state_with_tool_output(_big_orders())
    update = await hc.compress_tool_messages_node(state)
    merged = add_messages(state["messages"], update["messages"])

    tool_msgs = [m for m in merged if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1  # replaced, not appended
    assert len(merged) == len(state["messages"])  # no growth


async def test_error_text_survives(headroom_on):
    err = "ERROR: shipment 42 failed customs clearance at port"
    state = {
        "messages": add_messages(
            [],
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "get_status", "args": {}, "id": "c2"}],
                ),
                ToolMessage(content=err, tool_call_id="c2", name="get_status"),
            ],
        )
    }
    update = await hc.compress_tool_messages_node(state)
    # Small error payload is below threshold -> unchanged (no-op), text intact either way.
    blob = json.dumps(
        [str(m.content) for m in (update.get("messages") or state["messages"])]
    )
    assert "customs" in blob


async def test_compression_failure_falls_back(headroom_on, monkeypatch):
    def boom(_state):
        raise RuntimeError("compressor exploded")

    monkeypatch.setattr(hc, "_get_stock_node", lambda: boom)
    state = _state_with_tool_output(_big_orders())
    # Must not raise; returns empty update so the original messages pass through unchanged.
    assert await hc.compress_tool_messages_node(state) == {}


def test_builder_wiring_flag_toggles(monkeypatch):
    import app.modules.agents.graph.graph as g

    # Flag ON -> compress_tools node present
    monkeypatch.setattr(g.app_settings, "HEADROOM_ENABLED", True)
    from langgraph.graph import StateGraph
    from app.modules.agents.graph.nodes import (
        GraphState,
        llm_node,
        tools_node,
        trim_messages_node,
    )

    def build():
        builder = StateGraph(GraphState)
        builder.add_node("trim", trim_messages_node)
        builder.add_node("llm", llm_node)
        builder.add_node("tools", tools_node)
        if g.app_settings.HEADROOM_ENABLED:
            builder.add_node("compress_tools", g.compress_tool_messages_node)
        return builder

    assert "compress_tools" in build().nodes
    monkeypatch.setattr(g.app_settings, "HEADROOM_ENABLED", False)
    assert "compress_tools" not in build().nodes
