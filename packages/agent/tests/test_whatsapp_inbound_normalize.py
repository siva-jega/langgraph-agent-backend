"""WhatsApp inbound normalization: reply linking (Phase 3) and button/
interactive tagging (Phase 5)."""

from app.modules.channels.providers.meta.whatsapp.adapter import WhatsAppAdapter
from app.modules.channels.schemas import InboundMessage

_ROUTING_CTX = {
    "agentId": "agent-1",
    "integrationId": "int-1",
    "workspaceId": "ws-1",
    "chatId": "chat-1",
    "externalUserId": "919999999999",
}


def _envelope(message: dict) -> InboundMessage:
    return InboundMessage(
        id="env-1",
        channel="whatsapp",
        source="meta",
        receivedAt="2026-06-15T00:00:00Z",
        payload={
            "context": _ROUTING_CTX,
            "entry": [
                {
                    "id": "e1",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "pnid-1"},
                                "contacts": [
                                    {"wa_id": "919999999999", "profile": {"name": "Kri"}}
                                ],
                                "messages": [message],
                            },
                        }
                    ],
                }
            ],
        },
    )


def _normalize(message: dict):
    return WhatsAppAdapter().normalize_inbound(_envelope(message)).message


def test_reply_message_captures_quoted_wamid() -> None:
    msg = _normalize(
        {
            "from": "919999999999",
            "id": "wamid.IN",
            "type": "text",
            "text": {"body": "yes that one"},
            "context": {"from": "15551234567", "id": "wamid.QUOTED"},
        }
    )
    assert msg.inReplyTo == "wamid.QUOTED"
    assert msg.text == "yes that one"
    assert msg.messageType == "TEXT"


def test_plain_text_has_no_reply_and_is_text_type() -> None:
    msg = _normalize(
        {"from": "919999999999", "id": "wamid.X", "type": "text", "text": {"body": "hi"}}
    )
    assert msg.inReplyTo is None
    assert msg.messageType == "TEXT"
    assert msg.buttonPayload is None


def test_button_tap_is_tagged_and_linked_to_template() -> None:
    msg = _normalize(
        {
            "from": "919999999999",
            "id": "wamid.B",
            "type": "button",
            "button": {"text": "Add to Cart", "payload": "ADD_TO_CART::SHOPIFY::123"},
            "context": {"id": "wamid.TEMPLATE"},
        }
    )
    assert msg.messageType == "BUTTON"
    assert msg.text == "Add to Cart"
    assert msg.inReplyTo == "wamid.TEMPLATE"
    assert msg.buttonPayload == {
        "type": "button",
        "text": "Add to Cart",
        "payload": "ADD_TO_CART::SHOPIFY::123",
    }


def test_interactive_reply_is_tagged_with_payload_id() -> None:
    msg = _normalize(
        {
            "from": "919999999999",
            "id": "wamid.I",
            "type": "interactive",
            "interactive": {"button_reply": {"id": "opt_2", "title": "Option 2"}},
        }
    )
    assert msg.messageType == "INTERACTIVE"
    assert msg.text == "Option 2"
    assert msg.buttonPayload == {
        "type": "interactive",
        "text": "Option 2",
        "payload": "opt_2",
    }


def test_interactive_list_reply_is_supported() -> None:
    msg = _normalize(
        {
            "from": "919999999999",
            "id": "wamid.L",
            "type": "interactive",
            "interactive": {"list_reply": {"id": "row_3", "title": "Third row"}},
        }
    )
    assert msg.messageType == "INTERACTIVE"
    assert msg.text == "Third row"
    assert msg.buttonPayload["payload"] == "row_3"
