import logging
import re
import json as _json
from typing import Dict, Any
from urllib.parse import urlparse

import httpx

from app.core.config import app_settings
from app.modules.channels.providers.base import ChannelProvider
from app.modules.channels.schemas import (
    InboundMessagePayload,
    InboundMeta,
    InboundSender,
    InboundMessage,
    NormalizedInboundMessage,
    SendResult,
)
from common.modules.token.encryption import TokenEncryption

logger = logging.getLogger(__name__)

_META_GRAPH_BASE = "https://graph.facebook.com/v23.0"


class WhatsAppAdapter(ChannelProvider):
    channel = "whatsapp"
    returns_html = False
    _max_message_chars = 4096

    def _chunk_text(self, text: str, limit: int) -> list[str]:
        """Split text into chunks of at most *limit* chars at natural boundaries."""
        if len(text) <= limit:
            return [text]
        chunks: list[str] = []
        while text:
            if len(text) <= limit:
                chunks.append(text)
                break
            # Try to split on a paragraph boundary first.
            split_at = text.rfind("\n\n", 0, limit)
            if split_at <= 0:
                # Fall back to sentence boundary (". ", "! ", "? ").
                for punct in (". ", "! ", "? "):
                    pos = text.rfind(punct, 0, limit)
                    if pos > 0:
                        split_at = pos + len(punct) - 1  # include punctuation, drop trailing space
                        break
            if split_at <= 0:
                # Last resort: split at a word boundary.
                split_at = text.rfind(" ", 0, limit)
            if split_at <= 0:
                split_at = limit  # hard cut only if no whitespace at all
            chunks.append(text[:split_at].strip())
            text = text[split_at:].strip()
        return [c for c in chunks if c]

    def normalize_inbound(self, envelope: InboundMessage) -> NormalizedInboundMessage:
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        entry = payload.get("entry") or []
        if not isinstance(entry, list) or not entry:
            raise ValueError("WhatsApp payload missing entry list")
        changes = entry[0].get("changes") if isinstance(entry[0], dict) else []
        if not isinstance(changes, list) or not changes:
            raise ValueError("WhatsApp payload missing changes list")
        value = changes[0].get("value") if isinstance(changes[0], dict) else {}
        if not isinstance(value, dict):
            raise ValueError("WhatsApp payload missing change value")

        messages = value.get("messages") or []
        if not isinstance(messages, list) or not messages:
            raise ValueError("WhatsApp payload missing messages")
        msg = messages[0] if isinstance(messages[0], dict) else {}

        contacts = value.get("contacts") or []
        contact = (
            contacts[0]
            if isinstance(contacts, list) and contacts and isinstance(contacts[0], dict)
            else {}
        )
        metadata = (
            value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
        )
        context = (
            payload.get("context") if isinstance(payload.get("context"), dict) else {}
        )

        agent_id = str(context.get("agentId") or payload.get("agentId") or "").strip()
        integration_id = str(
            context.get("integrationId") or payload.get("integrationId") or ""
        ).strip()
        agent_integration_id = str(
            context.get("agentIntegrationId")
            or payload.get("agentIntegrationId")
            or integration_id
            or ""
        ).strip()
        customer_id = str(
            context.get("customerId") or payload.get("customerId") or ""
        ).strip()
        workspace_id = str(
            context.get("workspaceId") or payload.get("workspaceId") or ""
        ).strip()

        wa_id = str(contact.get("wa_id") or msg.get("from") or "").strip()
        external_user_id = str(
            context.get("externalUserId")
            or payload.get("externalUserId")
            or wa_id
            or ""
        ).strip()
        chat_id = str(context.get("chatId") or payload.get("chatId") or "").strip()
        if not chat_id and agent_integration_id and external_user_id:
            chat_id = f"{agent_integration_id}:{external_user_id}"

        missing = [
            name
            for name, value in (
                ("agentId", agent_id),
                ("integrationId", integration_id),
                ("workspaceId", workspace_id),
                ("chatId", chat_id),
                ("externalUserId", external_user_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "WhatsApp envelope missing routing fields: "
                + ", ".join(missing)
                + ". Expected them in payload.context."
            )

        text = ""
        msg_type = str(msg.get("type") or "").strip().lower()
        message_type = "TEXT"
        button_payload: dict | None = None

        if msg_type == "button" and isinstance(msg.get("button"), dict):
            # Template quick-reply tap.
            btn = msg["button"]
            text = str(btn.get("text") or "").strip()
            message_type = "BUTTON"
            button_payload = {
                "type": "button",
                "text": text,
                "payload": str(btn.get("payload") or "").strip() or None,
            }
        elif msg_type == "interactive" and isinstance(msg.get("interactive"), dict):
            interactive = msg["interactive"]
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            if isinstance(reply, dict):
                text = str(reply.get("title") or "").strip()
                message_type = "INTERACTIVE"
                button_payload = {
                    "type": "interactive",
                    "text": text,
                    "payload": str(reply.get("id") or "").strip() or None,
                }
        elif isinstance(msg.get("text"), dict):
            text = str(msg["text"].get("body") or "").strip()

        message_id = str(msg.get("id") or envelope.id).strip() or envelope.id

        # Quoted-message reference: when the user replies to a specific message,
        # WhatsApp includes `message.context.id` = the wamid being replied to.
        msg_context = msg.get("context") if isinstance(msg.get("context"), dict) else {}
        reply_to_wamid = str(msg_context.get("id") or "").strip() or None

        sender = InboundSender(
            phone=wa_id or None,
            displayName=(
                str(contact.get("profile", {}).get("name") or "").strip() or None
                if isinstance(contact.get("profile"), dict)
                else None
            ),
            id=wa_id or None,
        )
        message = InboundMessagePayload(
            text=text,
            messageId=message_id,
            inReplyTo=reply_to_wamid,
            messageType=message_type,
            buttonPayload=button_payload,
        )
        meta = InboundMeta(
            whatsappPhoneNumberId=(
                str(metadata.get("phone_number_id") or "").strip() or None
            )
        )

        return NormalizedInboundMessage(
            id=envelope.id,
            timestamp=envelope.receivedAt,
            channel="whatsapp",
            agentId=agent_id,
            integrationId=integration_id,
            agentIntegrationId=agent_integration_id,
            customerId=customer_id or None,
            workspaceId=workspace_id,
            chatId=chat_id,
            externalUserId=external_user_id,
            sender=sender,
            message=message,
            meta=meta,
        )

    def get_format_rules(self, inbound: NormalizedInboundMessage) -> dict:
        sender_name = inbound.sender.displayName or ""
        return {
            "max_chars": 3900,
            "markdown": "whatsapp",
            "greeting": (
                f"Friendly first-name greeting if known (e.g. 'Hey {sender_name}'); avoid formal email greetings."
                if sender_name
                else "Friendly conversational opener; avoid formal email greetings."
            ),
            "sign_off": "",
            "emoji": "natural",
            "links": "allow",
            "tone": "Concise, friendly, direct — short paragraphs.",
        }

    def get_few_shots(self, inbound: NormalizedInboundMessage) -> str:
        return (
            "Greeting / small talk — reply directly, no tool call.\n"
            "User: Hi there\n"
            "You: Hey! How can I help today?\n\n"
            "Product / shopping query — always `get_products`, never the KB.\n"
            "User: Do you have black hoodies in stock?\n"
            "Action: call `get_products` with query=\"black hoodie\"\n"
            "You (after tool result): Yes — *Midnight Hoodie* ₹2,499 and *Onyx Crew* ₹2,199 are both in stock. Want sizes?\n\n"
            "Order status — always `get_order_status` first; only raise a ticket if the lookup shows a real problem.\n"
            "User: Where's order #1242?\n"
            "Action: call `get_order_status` with order_id=\"1242\"\n"
            "You (after tool result): It shipped yesterday — tracking shows delivery by Tuesday.\n\n"
            "Policy / non-product question — `search_knowledgebase` (if KB attached); never `get_products`.\n"
            "User: What's your return policy?\n"
            "Action: call `search_knowledgebase` with query=\"return policy\"\n"
            "You (after tool result): You have 30 days from delivery to return, free of charge.\n\n"
            "Purchase intent / quote / callback — `create_lead_aide_channel`. The workspace configures which fields this tool collects (check the schema). Derive `summary` from the conversation, and ask the user in one or two short messages for any other required value you don't already have. Read back the collected values to confirm before calling.\n\n"
            "Complaint / refund / damaged — `create_ticket_aide_channel`. The workspace configures which fields this tool collects (check the schema). Derive `summary` from the conversation, and ask the user in one or two short messages for any other required value you don't already have. Read back the collected values to confirm before calling.\n\n"
            "Frustration / explicit human ask — `human_handoff_trigger_channel`.\n"
            "User: Just connect me to a real agent please.\n"
            "Action: call `human_handoff_trigger_channel` with summary=\"User requested human agent\", priority=\"HIGH\"\n"
            "You (after tool result): Of course — connecting you to a human now.\n"
        )

    def get_role_block(self, inbound: NormalizedInboundMessage, persona: dict) -> str:
        return ""

    def format_response(
        self,
        ai_text: str,
        inbound: NormalizedInboundMessage,
        agent_config: Dict[str, Any],
    ) -> str:
        """Convert standard markdown text to WhatsApp-safe text."""
        # Convert **bold** to *bold*
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", ai_text)
        # Convert markdown headings to pseudo-bold lines
        text = re.sub(r"^#+\s+(.*?)$", r"*\1*", text, flags=re.MULTILINE)
        return text.strip()

    async def send_typing_indicator(
        self,
        inbound: NormalizedInboundMessage,
        integration: Dict[str, Any],
    ) -> None:
        """Send WhatsApp typing indicator tied to the inbound wamid."""
        message_id = (inbound.message.messageId or "").strip()
        if not message_id:
            logger.debug(
                "Skipping WhatsApp typing indicator: missing inbound message id"
            )
            return

        phone_number_id = self._resolve_phone_number_id(integration, inbound)
        access_token = self._resolve_access_token(integration)
        url = f"{_META_GRAPH_BASE}/{phone_number_id}/messages"
        body = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {
                "type": "text",
            },
        }

        logger.info(
            "Sending WhatsApp typing indicator phone_number_id=%s message_id=%s",
            phone_number_id,
            message_id,
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
        if response.status_code >= 400:
            data = (
                response.json()
                if response.headers.get("content-type", "").startswith(
                    "application/json"
                )
                else {}
            )
            error = ""
            if isinstance(data, dict):
                err_obj = data.get("error")
                if isinstance(err_obj, dict):
                    error = str(err_obj.get("message") or "").strip()
            if not error:
                error = response.text[:500]
            raise ValueError(
                f"Meta Graph WhatsApp typing indicator failed ({response.status_code}): {error}"
            )

    async def send(
        self,
        formatted: str,
        ai_text: str,
        inbound: NormalizedInboundMessage,
        integration: Dict[str, Any],
        quick_replies: list[dict] | None = None,
    ) -> SendResult:
        """Send a text message via the Meta Cloud API for WhatsApp.

        Required integration data:
        - ``accountId`` or creds/metadata phone number id -> Meta ``phone_number_id``
        - access token in ``creds`` as encrypted ``access_token`` + ``access_token_iv``
          or plaintext ``access_token`` / ``token``
        """
        try:
            to = (inbound.sender.phone or "").strip()
            if not to:
                raise ValueError("Missing recipient phone for WhatsApp send")

            phone_number_id = self._resolve_phone_number_id(integration, inbound)
            access_token = self._resolve_access_token(integration)
            reply_text = formatted.strip() or ai_text.strip()
            if not reply_text:
                raise ValueError("Empty WhatsApp reply text")

            chunks = self._chunk_text(reply_text, self._max_message_chars)

            url = f"{_META_GRAPH_BASE}/{phone_number_id}/messages"
            message_id = None
            async with httpx.AsyncClient(timeout=30.0) as client:
                for chunk in chunks:
                    body = {
                        "messaging_product": "whatsapp",
                        "recipient_type": "individual",
                        "to": to,
                        "type": "text",
                        "text": {
                            "preview_url": False,
                            "body": chunk,
                        },
                    }

                    logger.info(
                        "Sending WhatsApp message via Meta Graph phone_number_id=%s to=%s chars=%d",
                        phone_number_id,
                        to,
                        len(chunk),
                    )

                    response = await client.post(
                        url,
                        json=body,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                        },
                    )

                    data = (
                        response.json()
                        if response.headers.get("content-type", "").startswith(
                            "application/json"
                        )
                        else {}
                    )
                    if response.status_code >= 400:
                        error = ""
                        if isinstance(data, dict):
                            err_obj = data.get("error")
                            if isinstance(err_obj, dict):
                                error = str(err_obj.get("message") or "").strip()
                        if not error:
                            error = response.text[:500]
                        raise ValueError(
                            f"Meta Graph send failed ({response.status_code}): {error}"
                        )

                    messages = data.get("messages") if isinstance(data, dict) else None
                    if (
                        isinstance(messages, list)
                        and messages
                        and isinstance(messages[0], dict)
                    ):
                        message_id = (
                            str(messages[0].get("id") or "").strip() or message_id
                        )

            return SendResult(success=True, messageId=message_id)

        except Exception as e:
            logger.exception("Failed to send WhatsApp reply for inbound %s", inbound.id)
            return SendResult(success=False, error=str(e))

    async def send_catalog_cards(
        self,
        inbound: NormalizedInboundMessage,
        integration: Dict[str, Any],
        asset: dict[str, Any],
        products: list[dict[str, Any]],
        shop_domain: str = "",
        intro_text: str = "",
    ) -> SendResult | None:
        """Send a WhatsApp template catalog message. When the carousel template's
        intro body has a ``{{1}}`` placeholder, ``intro_text`` (the assistant's
        short lead-in) fills it so the cards arrive as a single message."""
        try:
            to = (inbound.sender.phone or "").strip()
            if not to or not products:
                return None

            phone_number_id = self._resolve_phone_number_id(integration, inbound)
            access_token = self._resolve_access_token(integration)
            payload = self._build_catalog_template_payload(
                to=to,
                inbound=inbound,
                asset=asset,
                products=products,
                shop_domain=shop_domain,
                intro_text=intro_text,
            )
            if not payload:
                return None

            url = f"{_META_GRAPH_BASE}/{phone_number_id}/messages"
            logger.info(
                "Sending WhatsApp catalog template phone_number_id=%s to=%s asset=%s product_count=%s",
                phone_number_id,
                to,
                asset.get("slug"),
                len(products),
            )
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )

            data = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if response.status_code >= 400:
                error = ""
                details = ""
                if isinstance(data, dict):
                    err_obj = data.get("error")
                    if isinstance(err_obj, dict):
                        error = str(err_obj.get("message") or "").strip()
                        error_data = err_obj.get("error_data")
                        if isinstance(error_data, dict):
                            details = str(error_data.get("details") or "").strip()
                if not error:
                    error = response.text[:500]
                if details:
                    error = f"{error} | details: {details}"
                raise ValueError(
                    f"Meta Graph catalog send failed ({response.status_code}): {error}"
                )

            message_id = None
            messages = data.get("messages") if isinstance(data, dict) else None
            if isinstance(messages, list) and messages and isinstance(messages[0], dict):
                message_id = str(messages[0].get("id") or "").strip() or None
            return SendResult(success=True, messageId=message_id)
        except Exception as e:
            logger.exception("Failed to send WhatsApp catalog cards for inbound %s", inbound.id)
            return SendResult(success=False, error=str(e))

    async def send_order_tracking_template(
        self,
        inbound: NormalizedInboundMessage,
        integration: Dict[str, Any],
        asset: dict[str, Any],
        order_data: dict[str, Any],
    ) -> SendResult | None:
        """Send a WhatsApp order-tracking template without a preceding AI text message."""
        try:
            to = (inbound.sender.phone or "").strip()
            if not to:
                return None

            phone_number_id = self._resolve_phone_number_id(integration, inbound)
            access_token = self._resolve_access_token(integration)
            payload = self._build_order_tracking_template_payload(
                to=to,
                asset=asset,
                order_data=order_data,
                inbound=inbound,
            )
            if not payload:
                return None

            url = f"{_META_GRAPH_BASE}/{phone_number_id}/messages"
            logger.info(
                "Sending WhatsApp order tracking template phone_number_id=%s to=%s asset=%s order=%s",
                phone_number_id,
                to,
                asset.get("slug"),
                order_data.get("name"),
            )
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )

            data = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if response.status_code >= 400:
                error = ""
                details = ""
                if isinstance(data, dict):
                    err_obj = data.get("error")
                    if isinstance(err_obj, dict):
                        error = str(err_obj.get("message") or "").strip()
                        error_data = err_obj.get("error_data")
                        if isinstance(error_data, dict):
                            details = str(error_data.get("details") or "").strip()
                if not error:
                    error = response.text[:500]
                if details:
                    error = f"{error} | details: {details}"
                raise ValueError(
                    f"Meta Graph order tracking send failed ({response.status_code}): {error}"
                )

            message_id = None
            messages = data.get("messages") if isinstance(data, dict) else None
            if isinstance(messages, list) and messages and isinstance(messages[0], dict):
                message_id = str(messages[0].get("id") or "").strip() or None
            return SendResult(success=True, messageId=message_id)
        except Exception as e:
            logger.exception("Failed to send WhatsApp order tracking template for inbound %s", inbound.id)
            return SendResult(success=False, error=str(e))

    async def send_quick_reply_template(
        self,
        inbound: NormalizedInboundMessage,
        integration: Dict[str, Any],
        asset: dict[str, Any],
        button_payloads: list[str],
    ) -> SendResult | None:
        """Send a utility template whose buttons are quick replies carrying a
        send-time payload (e.g. the post-add-to-cart actions template). The
        template body is static; only the button payloads are dynamic."""
        try:
            to = (inbound.sender.phone or "").strip()
            if not to or not button_payloads:
                return None

            meta_payload = asset.get("metaPayload")
            if isinstance(meta_payload, str):
                meta_payload = _json.loads(meta_payload)
            if not isinstance(meta_payload, dict):
                return None
            name = str(meta_payload.get("name") or "").strip()
            language = str(meta_payload.get("language") or "en_US").strip() or "en_US"
            if not name:
                return None

            components = [
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": str(index),
                    "parameters": [{"type": "payload", "payload": payload}],
                }
                for index, payload in enumerate(button_payloads)
                if payload
            ]
            if not components:
                return None

            phone_number_id = self._resolve_phone_number_id(integration, inbound)
            access_token = self._resolve_access_token(integration)
            body = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "template",
                "template": {
                    "name": name,
                    "language": {"code": language},
                    "components": components,
                },
            }

            url = f"{_META_GRAPH_BASE}/{phone_number_id}/messages"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                )

            data = (
                response.json()
                if response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            if response.status_code >= 400:
                error = ""
                if isinstance(data, dict):
                    err_obj = data.get("error")
                    if isinstance(err_obj, dict):
                        error = str(err_obj.get("message") or "").strip()
                if not error:
                    error = response.text[:500]
                raise ValueError(
                    f"Meta Graph quick-reply template send failed ({response.status_code}): {error}"
                )

            message_id = None
            messages = data.get("messages") if isinstance(data, dict) else None
            if isinstance(messages, list) and messages and isinstance(messages[0], dict):
                message_id = str(messages[0].get("id") or "").strip() or None
            return SendResult(success=True, messageId=message_id)
        except Exception as e:
            logger.exception(
                "Failed to send WhatsApp quick-reply template for inbound %s", inbound.id
            )
            return SendResult(success=False, error=str(e))

    def _build_catalog_template_payload(
        self,
        *,
        to: str,
        inbound: NormalizedInboundMessage,
        asset: dict[str, Any],
        products: list[dict[str, Any]],
        shop_domain: str = "",
        intro_text: str = "",
    ) -> dict[str, Any] | None:
        meta_payload_raw = asset.get("metaPayload")
        # Replace __SHOP_DOMAIN__ placeholder with the actual Shopify store domain
        if shop_domain and isinstance(meta_payload_raw, str):
            meta_payload_raw = meta_payload_raw.replace("__SHOP_DOMAIN__", shop_domain)
        meta_payload = (
            _json.loads(meta_payload_raw)
            if isinstance(meta_payload_raw, str)
            else meta_payload_raw
        )
        if not isinstance(meta_payload, dict):
            raise ValueError("Catalog asset metaPayload is missing or invalid")

        template_name = str(meta_payload.get("name") or "").strip()
        language_code = str(meta_payload.get("language") or "en_US").strip() or "en_US"
        components = meta_payload.get("components") or []
        if not template_name or not isinstance(components, list):
            raise ValueError("Catalog asset metaPayload missing template name/components")

        if not self._meta_payload_has_placeholders(meta_payload):
            return {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": language_code},
                },
            }

        resolved_components: list[dict[str, Any]] = []
        logical_key = str(asset.get("logicalKey") or "").strip()

        for component in components:
            if not isinstance(component, dict):
                continue
            comp_type = str(component.get("type") or "").strip().lower()
            if comp_type == "header":
                header = self._build_template_header(component, products[0])
                if header:
                    resolved_components.append(header)
            elif comp_type == "body":
                # For the carousel template the top-level body is the intro bubble
                # (fed by the assistant's lead-in); for single-product templates it
                # holds the product's own name/description/price.
                if logical_key.startswith("product_cards_carousel"):
                    body = self._build_intro_body(component, intro_text)
                else:
                    body = self._build_template_body(component, products[0])
                if body:
                    resolved_components.append(body)
            elif comp_type == "buttons":
                resolved_components.extend(self._build_template_buttons(component, products[0]))
            elif comp_type == "carousel" and logical_key.startswith("product_cards_carousel"):
                carousel = self._build_template_carousel(component, products)
                if carousel:
                    resolved_components.append(carousel)

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        if resolved_components:
            payload["template"]["components"] = resolved_components
        return payload

    def _build_order_tracking_template_payload(
        self,
        *,
        to: str,
        asset: dict[str, Any],
        order_data: dict[str, Any],
        inbound: NormalizedInboundMessage,
    ) -> dict[str, Any] | None:
        meta_payload_raw = asset.get("metaPayload")
        meta_payload = (
            _json.loads(meta_payload_raw)
            if isinstance(meta_payload_raw, str)
            else meta_payload_raw
        )
        if not isinstance(meta_payload, dict):
            raise ValueError("Order tracking asset metaPayload is missing or invalid")

        template_name = str(
            meta_payload.get("name")
            or meta_payload.get("library_template_name")
            or ""
        ).strip()
        language_code = str(meta_payload.get("language") or "en_US").strip() or "en_US"
        if not template_name:
            raise ValueError("Order tracking asset missing template name")

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }

        components = self._build_order_tracking_components(
            meta_payload, order_data, inbound
        )
        if components:
            payload["template"]["components"] = components
        return payload

    def _build_order_tracking_components(
        self,
        meta_payload: dict[str, Any],
        order_data: dict[str, Any],
        inbound: NormalizedInboundMessage,
    ) -> list[dict[str, Any]]:
        from app.modules.channels.manifest import (
            build_library_template_body_params,
        )

        template_name = str(
            meta_payload.get("library_template_name")
            or meta_payload.get("name")
            or ""
        ).strip()
        body_params = build_library_template_body_params(
            template_name=template_name,
            order=order_data,
            inbound=inbound,
        )
        if not body_params:
            body_params = [
                {"type": "text", "text": (inbound.sender.displayName or "there").split()[0]},
                {"type": "text", "text": str(order_data.get("name") or order_data.get("id") or "").strip() or "your order"},
            ]
        components: list[dict[str, Any]] = [
            {
                "type": "body",
                "parameters": body_params,
            }
        ]
        button_inputs = meta_payload.get("library_template_button_inputs")
        if not isinstance(button_inputs, list):
            return components

        for index, button_input in enumerate(button_inputs):
            if not isinstance(button_input, dict):
                continue
            btn_type = str(button_input.get("type") or "").strip().upper()
            if btn_type != "URL":
                continue
            url_meta = button_input.get("url")
            if not isinstance(url_meta, dict):
                continue
            suffix = self._order_tracking_url_suffix(order_data)
            if not suffix:
                continue
            components.append(
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": str(index),
                    "parameters": [{"type": "text", "text": suffix}],
                }
            )

        return components

    def _build_template_header(self, component: dict[str, Any], product: dict[str, Any]) -> dict[str, Any] | None:
        fmt = str(component.get("format") or "").strip().lower()
        if fmt != "image":
            return None
        image_url = str(product.get("image_url") or "").strip()
        if not image_url:
            images = product.get("images")
            if isinstance(images, list) and images:
                image_url = str(images[0] or "").strip()
        if not image_url:
            return None
        return {
            "type": "header",
            "parameters": [{"type": "image", "image": {"link": image_url}}],
        }

    def _build_template_body(
        self,
        component: dict[str, Any],
        product: dict[str, Any],
    ) -> dict[str, Any] | None:
        count = self._count_placeholders(str(component.get("text") or ""))
        if count <= 0:
            return None
        fallback = self._example_body_values(component)
        values = [
            self._product_name(product),
            self._product_description(product),
            self._price_for_template(component, product),
        ]
        params = []
        for idx in range(count):
            value = values[idx] if idx < len(values) and values[idx] else ""
            if not value and idx < len(fallback):
                value = fallback[idx]
            params.append({"type": "text", "text": self._sanitize_template_text(value or "-")})
        return {"type": "body", "parameters": params}

    def _build_intro_body(
        self,
        component: dict[str, Any],
        intro_text: str,
    ) -> dict[str, Any] | None:
        """Fill the carousel's intro body with a short Meta-safe lead-in.

        Product details already render in the cards. Meta rejects newline and
        tab characters in template parameters, so use the approved example
        rather than injecting the assistant's full product-list response.
        """
        count = self._count_placeholders(str(component.get("text") or ""))
        if count <= 0:
            return None
        fallback = self._example_body_values(component)
        text = fallback[0] if fallback else "Here are some options for you 👇"
        params = [
            {
                "type": "text",
                "text": self._sanitize_template_text(text, max_len=160),
            }
        ]
        # Defensively fill any extra placeholders so Meta doesn't reject the send.
        for idx in range(1, count):
            value = fallback[idx] if idx < len(fallback) else "-"
            params.append({"type": "text", "text": self._sanitize_template_text(value or "-")})
        return {"type": "body", "parameters": params}

    def _build_template_buttons(self, component: dict[str, Any], product: dict[str, Any]) -> list[dict[str, Any]]:
        buttons = component.get("buttons") or []
        if not isinstance(buttons, list):
            return []
        # Prefer the handle column directly; fall back to extracting from product_url
        handle = str(product.get("handle") or "").strip() or self._product_handle(product)
        out: list[dict[str, Any]] = []
        for index, button in enumerate(buttons):
            if not isinstance(button, dict):
                continue
            btn_type = str(button.get("type") or "").strip().upper()
            if btn_type == "URL" and handle:
                out.append(
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": str(index),
                        "parameters": [{"type": "text", "text": handle}],
                    }
                )
            elif btn_type == "QUICK_REPLY":
                payload = self._quick_reply_payload(button, product)
                if not payload:
                    continue
                out.append(
                    {
                        "type": "button",
                        "sub_type": "quick_reply",
                        "index": str(index),
                        "parameters": [
                            {
                                "type": "payload",
                                "payload": payload,
                            }
                        ],
                    }
                )
        return out

    def _build_template_carousel(self, component: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any] | None:
        cards = component.get("cards") or []
        if not isinstance(cards, list) or not cards:
            return None
        resolved_cards: list[dict[str, Any]] = []
        for idx, product in enumerate(products[: min(len(cards), 10)]):
            card = cards[idx]
            if not isinstance(card, dict):
                continue
            card_components = card.get("components") or []
            if not isinstance(card_components, list):
                continue
            resolved_components: list[dict[str, Any]] = []
            for card_component in card_components:
                if not isinstance(card_component, dict):
                    continue
                comp_type = str(card_component.get("type") or "").strip().lower()
                if comp_type == "header":
                    header = self._build_template_header(card_component, product)
                    if header:
                        resolved_components.append(header)
                elif comp_type == "body":
                    body = self._build_carousel_card_body(card_component, product)
                    if body:
                        resolved_components.append(body)
                elif comp_type == "buttons":
                    resolved_components.extend(self._build_template_buttons(card_component, product))
            if resolved_components:
                resolved_cards.append({"card_index": idx, "components": resolved_components})
        if not resolved_cards:
            return None
        return {"type": "carousel", "cards": resolved_cards}

    def _build_carousel_card_body(self, component: dict[str, Any], product: dict[str, Any]) -> dict[str, Any] | None:
        count = self._count_placeholders(str(component.get("text") or ""))
        if count <= 0:
            return None
        fallback = self._example_body_values(component)
        values = [
            self._sanitize_template_text(self._product_name(product), max_len=30),
            self._sanitize_template_text(self._product_description(product), max_len=35),
            self._sanitize_template_text(self._price_for_template(component, product), max_len=20),
        ]
        params = []
        for idx in range(count):
            value = values[idx] if idx < len(values) and values[idx] else ""
            if not value and idx < len(fallback):
                value = fallback[idx]
            params.append({"type": "text", "text": self._sanitize_template_text(value or "-", max_len=40)})
        return {"type": "body", "parameters": params}

    @staticmethod
    def _count_placeholders(text: str) -> int:
        matches = re.findall(r"\{\{(\d+)\}\}", text or "")
        return max((int(m) for m in matches), default=0)

    @staticmethod
    def _meta_payload_has_placeholders(meta_payload: dict[str, Any]) -> bool:
        try:
            raw = _json.dumps(meta_payload)
        except Exception:
            raw = str(meta_payload)
        return bool(re.search(r"\{\{[^}]+\}\}", raw))

    @staticmethod
    def _example_body_values(component: dict[str, Any]) -> list[str]:
        example = component.get("example")
        if not isinstance(example, dict):
            return []
        body_text = example.get("body_text")
        if not isinstance(body_text, list) or not body_text:
            return []
        row = body_text[0]
        if not isinstance(row, list):
            return []
        return [str(v) for v in row]

    @staticmethod
    def _product_name(product: dict[str, Any]) -> str:
        return str(product.get("name") or "").strip()

    @staticmethod
    def _product_description(product: dict[str, Any]) -> str:
        return str(product.get("description") or "").strip()

    # Map ISO currency codes to display symbols; unmapped codes fall back to
    # "<CODE> " (e.g. "AED 100") so the value is still unambiguous.
    _CURRENCY_SYMBOLS = {
        "INR": "₹",
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "JPY": "¥",
        "CNY": "¥",
        "AUD": "A$",
        "CAD": "C$",
        "NZD": "NZ$",
        "SGD": "S$",
        "HKD": "HK$",
        "BRL": "R$",
        "ZAR": "R",
    }
    _CURRENCY_CHARS = "₹$€£¥"

    @classmethod
    def _currency_symbol(cls, code: str) -> str:
        code = (code or "").strip().upper()
        if not code:
            return ""
        return cls._CURRENCY_SYMBOLS.get(code, f"{code} ")

    @staticmethod
    def _product_currency(product: dict[str, Any]) -> str:
        """Currency code for the product.

        Shopify products carry it as ``currencyCode`` (set in the get_products
        artifact). Fall back to a leading ISO code in price_range
        (e.g. "INR 2499" -> "INR") only if that field is absent.
        """
        code = str(product.get("currencyCode") or "").strip().upper()
        if code:
            return code
        raw = str(product.get("price_range") or "").strip()
        match = re.match(r"^([A-Za-z]{3})\b", raw)
        return match.group(1).upper() if match else ""

    @staticmethod
    def _product_price(product: dict[str, Any]) -> str:
        """Bare numeric amount, with any leading currency code or symbol stripped."""
        value = product.get("price_range")
        if value is None:
            return ""
        raw = str(value).strip()
        raw = re.sub(r"^[A-Za-z]{3}\s+", "", raw)  # strip a leading ISO code, e.g. "USD "
        raw = raw.replace("₹", "").replace("Rs.", "").replace("INR", "").strip()
        return raw[:40]

    def _format_price(self, product: dict[str, Any]) -> str:
        """Amount prefixed with the product's currency symbol/code (e.g. "₹2499")."""
        amount = self._product_price(product)
        if not amount:
            return ""
        return f"{self._currency_symbol(self._product_currency(product))}{amount}"

    def _price_for_template(
        self, component: dict[str, Any], product: dict[str, Any]
    ) -> str:
        """Resolve the price value for a template body.

        If the template text already hardcodes a currency symbol next to the
        price placeholder (legacy templates, e.g. "Price: ₹{{3}}"), return the
        bare amount so it isn't doubled. Otherwise emit the currency-formatted
        value so the symbol is dynamic.
        """
        text = str(component.get("text") or "")
        if any(ch in text for ch in self._CURRENCY_CHARS):
            return self._product_price(product)
        return self._format_price(product)

    @staticmethod
    def _product_handle(product: dict[str, Any]) -> str:
        product_url = str(product.get("product_url") or "").strip()
        if not product_url:
            return ""
        try:
            path = urlparse(product_url).path or ""
        except Exception:
            path = product_url
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[-2] == "products":
            return parts[-1]
        return parts[-1] if parts else ""

    @staticmethod
    def _quick_reply_payload(button: dict[str, Any], product: dict[str, Any]) -> str | None:
        # Encode the action + product/variant in the quick-reply payload so the
        # inbound postback (e.g. "Add to Cart" tap) can be matched and handled in
        # user-backend instead of falling through to the AI queue. The button text
        # becomes the action (e.g. ADD_TO_CART), which the postback parser keys on.
        action = str(button.get("text") or "").strip().upper().replace(" ", "_")
        action = re.sub(r"[^A-Z0-9_]", "", action)
        if not action:
            return None
        product_id = str(product.get("id") or "").strip() or "UNKNOWN"
        variant_id = ""
        variants = product.get("variants")
        if isinstance(variants, list) and variants and isinstance(variants[0], dict):
            variant_id = str(
                variants[0].get("id") or variants[0].get("externalId") or ""
            ).strip()
        # use :: as delimiter to avoid conflicts with slashes/colons in GIDs
        parts = [action, "SHOPIFY", product_id]
        if variant_id:
            parts.append(variant_id)
        return "::".join(parts)[:128]

    @staticmethod
    def _order_tracking_url_suffix(order_data: dict[str, Any]) -> str:
        # Prefer statusPageUrl — extract the path component as suffix so that
        # base_url (https://<shop_domain>/) + suffix = full order status URL.
        # e.g. "https://myshop.myshopify.com/orders/abc123token" → "orders/abc123token"
        status_url = str(order_data.get("status_url") or order_data.get("statusPageUrl") or "").strip()
        if status_url:
            from urllib.parse import urlparse
            path = urlparse(status_url).path.lstrip("/")
            if path:
                return path[:120]
        order_name = str(order_data.get("name") or "").strip()
        if order_name:
            return order_name.lstrip("#")[:120]
        order_id = str(order_data.get("id") or "").strip()
        return order_id[:120]

    @staticmethod
    def _sanitize_template_text(value: str, max_len: int = 120) -> str:
        text = str(value or "")
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_len] if text else "-"

    @staticmethod
    def _resolve_phone_number_id(
        integration: Dict[str, Any],
        inbound: NormalizedInboundMessage,
    ) -> str:
        phone_number_id = str(
            integration.get("accountId")
            or (inbound.meta.whatsappPhoneNumberId if inbound.meta else "")
            or ""
        ).strip()
        if phone_number_id:
            return phone_number_id

        creds_raw = integration.get("creds", {}) or {}
        creds = _json.loads(creds_raw) if isinstance(creds_raw, str) else creds_raw
        metadata_raw = integration.get("metadata", {}) or {}
        metadata = (
            _json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
        )

        phone_number_id = str(
            creds.get("phoneNumberId")
            or creds.get("phone_number_id")
            or metadata.get("phoneNumberId")
            or metadata.get("phone_number_id")
            or ""
        ).strip()
        if not phone_number_id:
            raise ValueError(
                "Missing Meta WhatsApp phone_number_id/accountId in integration"
            )
        return phone_number_id

    @staticmethod
    def _resolve_access_token(integration: Dict[str, Any]) -> str:
        system_token = str(app_settings.SYSTEM_USER_TOKEN or "").strip()
        if system_token:
            return system_token

        creds_raw = integration.get("creds", {}) or {}
        creds = _json.loads(creds_raw) if isinstance(creds_raw, str) else creds_raw

        access_token = str(
            creds.get("access_token")
            or creds.get("accessToken")
            or creds.get("token")
            or ""
        ).strip()
        token_iv = str(
            creds.get("access_token_iv") or creds.get("token_iv") or ""
        ).strip()

        if access_token and token_iv:
            encryption_key = app_settings.ENCRYPTION_KEY
            if not encryption_key:
                raise ValueError(
                    "ENCRYPTION_KEY not configured for decrypting WhatsApp token"
                )
            access_token = TokenEncryption(encryption_key).decrypt(
                access_token, token_iv
            )

        if not access_token:
            raise ValueError(
                "Missing WhatsApp access token. Set SYSTEM_USER_TOKEN or provide token in integration creds"
            )
        return access_token

    # ------------------------------------------------------------------ #
    # Routing hooks                                                        #
    # ------------------------------------------------------------------ #

    def extract_account_id(self, payload: dict) -> str:
        value = self._wa_value(payload)
        metadata = value.get("metadata")
        if not isinstance(metadata, dict):
            return ""
        return str(metadata.get("phone_number_id") or "").strip()

    def extract_sender_id(self, payload: dict) -> str:
        value = self._wa_value(payload)
        contacts = value.get("contacts") or []
        if isinstance(contacts, list) and contacts and isinstance(contacts[0], dict):
            wa_id = str(contacts[0].get("wa_id") or "").strip()
            if wa_id:
                return wa_id
        messages = value.get("messages") or []
        if isinstance(messages, list) and messages and isinstance(messages[0], dict):
            return str(messages[0].get("from") or "").strip()
        return ""

    def build_customer_data(self, payload: dict, external_user_id: str) -> dict:
        value = self._wa_value(payload)
        contacts = value.get("contacts") or []
        contact = (
            contacts[0]
            if isinstance(contacts, list) and contacts and isinstance(contacts[0], dict)
            else {}
        )
        profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
        return {
            "channel": "whatsapp",
            "externalUserId": external_user_id,
            "phone": external_user_id,
            "name": str(profile.get("name") or "").strip() or None,
        }

    def build_chat_title(self, customer: dict, external_user_id: str) -> str:
        import json as _j
        data = customer.get("data")
        data_obj = _j.loads(data) if isinstance(data, str) else (data if isinstance(data, dict) else {})
        name = str(data_obj.get("name") or "").strip()
        phone = str(data_obj.get("phone") or external_user_id or "").strip()
        return name or phone or "WhatsApp conversation"

    @staticmethod
    def _wa_value(payload: dict) -> dict:
        """Extract the WhatsApp change value from a raw webhook payload."""
        entry = payload.get("entry") or []
        if not isinstance(entry, list) or not entry or not isinstance(entry[0], dict):
            return {}
        changes = entry[0].get("changes") or []
        if not isinstance(changes, list) or not changes or not isinstance(changes[0], dict):
            return {}
        value = changes[0].get("value")
        return value if isinstance(value, dict) else {}
