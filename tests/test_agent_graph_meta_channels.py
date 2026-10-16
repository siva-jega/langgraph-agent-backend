from app.modules.agents.graph.graph import _resolve_human_handoff_enabled
from app.modules.agents.graph.nodes import _get_tools


def test_resolve_human_handoff_enabled_blocks_meta_channels_for_non_streaming() -> None:
    assert (
        _resolve_human_handoff_enabled(
            db_handoff_on=True,
            has_handoff_skill=True,
            channel="whatsapp",
            is_non_streaming=True,
        )
        is False
    )
    assert (
        _resolve_human_handoff_enabled(
            db_handoff_on=True,
            has_handoff_skill=True,
            channel="instagram",
            is_non_streaming=True,
        )
        is False
    )
    assert (
        _resolve_human_handoff_enabled(
            db_handoff_on=True,
            has_handoff_skill=True,
            channel="messenger",
            is_non_streaming=True,
        )
        is False
    )


def test_resolve_human_handoff_enabled_keeps_web_enabled() -> None:
    assert (
        _resolve_human_handoff_enabled(
            db_handoff_on=True,
            has_handoff_skill=True,
            channel="web",
            is_non_streaming=True,
        )
        is True
    )


def test_get_tools_skips_handoff_tool_for_blocked_meta_channels() -> None:
    config = {
        "configurable": {
            "channel": "whatsapp",
            "is_non_streaming": True,
            "allow_human_handoff": False,
            "entity_ids": [],
            "folder_ids": [],
            "apps": [],
            "skills": [
                {"toolName": "human_handoff_trigger"},
                {"toolName": "create_ticket_aide"},
            ],
        }
    }
    names = sorted(tool.name for tool in _get_tools(config))
    assert "create_ticket_aide" in names
    assert "human_handoff_trigger" not in names


def test_get_tools_keeps_handoff_tool_for_web() -> None:
    config = {
        "configurable": {
            "channel": "web",
            "is_non_streaming": True,
            "allow_human_handoff": True,
            "entity_ids": [],
            "folder_ids": [],
            "apps": [],
            "skills": [
                {"toolName": "human_handoff_trigger"},
                {"toolName": "create_ticket_aide"},
            ],
        }
    }
    names = sorted(tool.name for tool in _get_tools(config))
    assert "create_ticket_aide" in names
    assert "human_handoff_trigger" in names
