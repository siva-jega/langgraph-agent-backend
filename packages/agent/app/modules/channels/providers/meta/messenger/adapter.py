"""Messenger channel provider.

All shared Meta Graph API logic (catalog cards, order tracking, postback
parsing, credential resolution) lives in MetaFeedProvider. This adapter
only contains what is unique to Messenger: inbound normalization and
prompt overlay.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

from app.modules.channels.providers.meta import MetaFeedProvider
from app.modules.channels.schemas import (
    InboundMessage,
    InboundMessagePayload,
    InboundMeta,
    InboundSender,
    NormalizedInboundMessage,
)


class MessengerAdapter(MetaFeedProvider):
    channel = "messenger"
    returns_html = False
    _max_message_chars = 2000

    def normalize_inbound(self, envelope: InboundMessage) -> NormalizedInboundMessage:
        payload: dict[str, Any] = envelope.payload if isinstance(envelope.payload, dict) else {}
        entry = payload.get("entry") or []
        if not isinstance(entry, list) or not entry or not isinstance(entry[0], dict):
            raise ValueError("Messenger payload missing entry list")
        messaging = entry[0].get("messaging") or []
        if not isinstance(messaging, list) or not messaging or not isinstance(messaging[0], dict):
            raise ValueError("Messenger payload missing messaging list")

        msg_event: dict[str, Any] = messaging[0]
        sender_raw = msg_event.get("sender")
        recipient_raw = msg_event.get("recipient")
        message_raw = msg_event.get("message")
        context: dict[str, Any] = payload.get("context") if isinstance(payload.get("context"), dict) else {}

        sender_id = str(sender_raw.get("id") or "").strip() if isinstance(sender_raw, dict) else ""
        recipient_id = str(recipient_raw.get("id") or "").strip() if isinstance(recipient_raw, dict) else ""
        message_id = str(message_raw.get("mid") or envelope.id).strip() if isinstance(message_raw, dict) else envelope.id
        text = str(message_raw.get("text") or "").strip() if isinstance(message_raw, dict) else ""

        if isinstance(msg_event.get("postback"), dict) and not text:
            text = str(msg_event["postback"].get("title") or "").strip()

        agent_id = str(context.get("agentId") or payload.get("agentId") or "").strip()
        integration_id = str(context.get("integrationId") or payload.get("integrationId") or "").strip()
        agent_integration_id = str(
            context.get("agentIntegrationId") or payload.get("agentIntegrationId") or integration_id or ""
        ).strip()
        customer_id = str(context.get("customerId") or payload.get("customerId") or "").strip()
        workspace_id = str(context.get("workspaceId") or payload.get("workspaceId") or "").strip()
        external_user_id = str(
            context.get("externalUserId") or payload.get("externalUserId") or sender_id or ""
        ).strip()
        chat_id = str(context.get("chatId") or payload.get("chatId") or "").strip()
        if not chat_id and agent_integration_id and external_user_id:
            chat_id = f"{agent_integration_id}:{external_user_id}"

        missing = [
            name for name, value in (
                ("agentId", agent_id), ("integrationId", integration_id),
                ("workspaceId", workspace_id), ("chatId", chat_id),
                ("externalUserId", external_user_id),
            ) if not value
        ]
        if missing:
            raise ValueError(
                "Messenger envelope missing routing fields: "
                + ", ".join(missing)
                + ". Expected them in payload.context."
            )

        return NormalizedInboundMessage(
            id=envelope.id,
            timestamp=envelope.receivedAt,
            channel="messenger",
            agentId=agent_id,
            integrationId=integration_id,
            agentIntegrationId=agent_integration_id,
            customerId=customer_id or None,
            workspaceId=workspace_id,
            chatId=chat_id,
            externalUserId=external_user_id,
            sender=InboundSender(id=sender_id or None),
            message=InboundMessagePayload(
                text=text,
                messageId=message_id or envelope.id,
            ),
            meta=InboundMeta(pageId=recipient_id or None),
            mediaRefs=envelope.mediaRefs or None,
        )

    def get_format_rules(self, inbound: NormalizedInboundMessage) -> dict:
        sender_name = inbound.sender.displayName or ""
        return {
            "max_chars": 1900,
            "markdown": "none",
            "greeting": (
                f"Polite greeting using '{sender_name}' if available; keep it natural."
                if sender_name
                else "Polite, brief greeting."
            ),
            "sign_off": "",
            "emoji": "sparingly",
            "links": "allow",
            "tone": "Helpful, quick, polite — short well-spaced paragraphs.",
        }

    def get_few_shots(self, inbound: NormalizedInboundMessage) -> str:
        return (
            "Greeting / small talk — reply directly, no tool call.\n"
            "User: hello\n"
            "You: Hi! How can I help today?\n\n"
            "Product / shopping query — always `get_products`, never the KB.\n"
            "User: I'm looking for running shoes.\n"
            "Action: call `get_products` with query=\"running shoes\"\n"
            "You (after tool result): We have two strong options — TrailFlex at ₹4,999 and AirGlide at ₹6,499. "
            "Both are in stock. Any preference on cushioning or terrain?\n\n"
            "Order status — always `get_order_status` first; only ticket if the lookup confirms a real issue.\n"
            "User: where is my order 1242\n"
            "Action: call `get_order_status` with order_id=\"1242\"\n"
            "You (after tool result): It shipped yesterday and should arrive by Tuesday.\n\n"
            "Policy / non-product question — `search_knowledgebase` (if KB attached); never `get_products`.\n"
            "User: do you ship internationally?\n"
            "Action: call `search_knowledgebase` with query=\"international shipping\"\n"
            "You (after tool result): Yes, we ship to most countries. Standard delivery is 7–10 business days.\n\n"
            "Purchase intent / quote / callback — `create_lead_aide_channel`. The workspace configures which fields this tool collects (check the schema). Derive `summary` from the conversation, and ask the user in one or two short messages for any other required value you don't already have. Read back the collected values to confirm before calling.\n\n"
            "Complaint / return / refund — `create_ticket_aide_channel`. The workspace configures which fields this tool collects (check the schema). Derive `summary` from the conversation, and ask the user in one or two short messages for any other required value you don't already have. Read back the collected values to confirm before calling.\n\n"
            "Frustration / explicit human ask — `human_handoff_trigger_channel`.\n"
            "User: I want to speak to a human\n"
            "Action: call `human_handoff_trigger_channel` with summary=\"User requested human agent\", priority=\"HIGH\"\n"
            "You (after tool result): Of course — connecting you to a human now.\n"
        )

    def get_role_block(self, inbound: NormalizedInboundMessage, persona: dict) -> str:
        return ""


