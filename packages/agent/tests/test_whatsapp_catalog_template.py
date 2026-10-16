from app.modules.channels.providers.meta.whatsapp.adapter import WhatsAppAdapter


def test_catalog_intro_body_uses_short_template_example() -> None:
    adapter = WhatsAppAdapter()
    component = {
        "type": "body",
        "text": "Hi! {{1}}\n\nTap a card to view details or add to cart.",
        "example": {"body_text": [["Here are some options for you"]]},
    }

    body = adapter._build_intro_body(
        component,
        "Here are some cool tees you might like",
    )

    assert body == {
        "type": "body",
        "parameters": [
            {
                "type": "text",
                "text": "Here are some options for you",
            }
        ],
    }


def test_catalog_intro_body_uses_short_generic_fallback() -> None:
    adapter = WhatsAppAdapter()
    component = {
        "type": "body",
        "text": "Hi! {{1}}\n\nTap a card to view details or add to cart.",
    }

    body = adapter._build_intro_body(component, "")

    assert body["parameters"][0]["text"] == "Here are some options for you 👇"
