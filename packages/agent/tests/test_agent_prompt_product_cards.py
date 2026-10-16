from app.modules.agents.graph.prompt import get_system_prompt


def test_kb_only_prompt_omits_shopify_product_card_section() -> None:
    """KB-only agents rely on search_knowledgebase artifacts, not prompt card rules."""
    prompt = get_system_prompt(
        {
            "channel": "web",
            "entityIds": ["website-id"],
            "folderIds": [],
            "apps": [],
            "appConfigs": [],
            "persona": {},
        }
    )

    assert "search_knowledgebase" in prompt
    assert "# Shopify product cards" not in prompt
    assert "call `get_products`" not in prompt
