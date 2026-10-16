from app.modules.agents.graph.graph import _resolve_human_handoff_enabled
from app.modules.agents.graph.nodes import _get_tools


def test_resolve_human_handoff_enabled_requires_db_and_skill() -> None:
    """Web graph handoff flag is DB + skill only; Meta channels use channels/ai graph."""
    assert (
        _resolve_human_handoff_enabled(
            db_handoff_on=True,
            has_handoff_skill=True,
        )
        is True
    )
    assert (
        _resolve_human_handoff_enabled(
            db_handoff_on=False,
            has_handoff_skill=True,
        )
        is False
    )
    assert (
        _resolve_human_handoff_enabled(
            db_handoff_on=True,
            has_handoff_skill=False,
        )
        is False
    )


def test_get_tools_skips_handoff_tool_for_blocked_meta_channels() -> None:
    config = {
        "configurable": {
            "channel": "whatsapp",
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


def test_get_tools_does_not_add_shopify_products_without_shopify_app() -> None:
    config = {
        "configurable": {
            "channel": "web",
            "allow_human_handoff": True,
            "entity_ids": ["file-1"],
            "folder_ids": [],
            "apps": [],
            "skills": [],
        }
    }

    names = sorted(tool.name for tool in _get_tools(config))

    assert "search_knowledgebase" in names
    assert "get_products" not in names


def test_get_tools_adds_shopify_products_only_from_connected_app() -> None:
    config = {
        "configurable": {
            "channel": "web",
            "allow_human_handoff": True,
            "entity_ids": ["file-1"],
            "folder_ids": [],
            "apps": [{"slug": "shopify"}],
            "skills": [],
        }
    }

    names = sorted(tool.name for tool in _get_tools(config))

    assert "search_knowledgebase" in names
    assert "get_products" in names
