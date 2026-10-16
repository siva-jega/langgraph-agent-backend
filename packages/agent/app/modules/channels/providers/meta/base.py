"""Shared base for Messenger and Instagram (Meta Messenger Platform API).

Both channels use the same Graph API endpoint, same credential structure,
same generic-template product cards, and same button-template order tracking.
Only normalization, prompt overlay, and plain-text send differ per channel.
"""

from __future__ import annotations

import json as _json
import logging
import re
from copy import deepcopy
from typing import Any, Dict

import httpx

from app.core.config import app_settings
from app.modules.channels.providers.base import ChannelProvider
from app.modules.channels.schemas import (
    InboundMessage,
    NormalizedInboundMessage,
    SendResult,
)
from common.modules.token.encryption import TokenEncryption

logger = logging.getLogger(__name__)

_META_GRAPH_BASE = "https://graph.facebook.com/v23.0"
_QR_LINE_RE = re.compile(r"\n?QUICK_REPLIES:\s*(.+)$", re.MULTILINE)
_QR_MAX_COUNT = 13
_QR_TITLE_MAX = 20


class MetaFeedProvider(ChannelProvider):
    """Abstract base for Messenger and Instagram — shared Meta Graph API logic."""

    # Set to a positive int in subclasses to enable multi-message splitting.
    _max_message_chars: int | None = None

    # ------------------------------------------------------------------ #
    # Routing helpers (called by service routing phase)                   #
    # ------------------------------------------------------------------ #

    def extract_account_id(self, payload: dict) -> str:
        entry = payload.get("entry") or []
        if isinstance(entry, list) and entry and isinstance(entry[0], dict):
            return str(entry[0].get("id") or "").strip()
        return ""

    def extract_sender_id(self, payload: dict) -> str:
        entry = payload.get("entry") or []
        if not isinstance(entry, list) or not entry or not isinstance(entry[0], dict):
            return ""
        messaging = entry[0].get("messaging") or []
        if not isinstance(messaging, list) or not messaging or not isinstance(messaging[0], dict):
            return ""
        sender = messaging[0].get("sender")
        if isinstance(sender, dict):
            return str(sender.get("id") or "").strip()
        return ""

    def build_customer_data(self, payload: dict, external_user_id: str) -> dict:
        return {"channel": self.channel, "externalUserId": external_user_id}

    def build_chat_title(self, customer: dict, external_user_id: str) -> str:
        data = customer.get("data")
        data_obj = _json.loads(data) if isinstance(data, str) else (data if isinstance(data, dict) else {})
        channel_name = self.channel.capitalize()
        return str(data_obj.get("name") or external_user_id or f"{channel_name} conversation").strip()

    # ------------------------------------------------------------------ #
    # Quick replies (shared — same Meta API field for both channels)      #
    # ------------------------------------------------------------------ #

    def get_channel_instructions(self, integration: dict | None = None) -> str:
        # Quick replies are on by default; only disabled when explicitly set to False.
        metadata = {}
        if integration:
            raw = integration.get("metadata") or {}
            if isinstance(raw, str):
                try:
                    raw = _json.loads(raw)
                except Exception:
                    raw = {}
            metadata = raw if isinstance(raw, dict) else {}
        enabled = metadata.get("quickRepliesEnabled", True)
        if not enabled:
            return ""
        return (
            "\n# Quick replies\n"
            "After your reply text, on a new line, append exactly: "
            "QUICK_REPLIES: <suggestion 1> || <suggestion 2> || <suggestion 3>. "
            "Each suggestion must be a short phrase the user might tap next (max 20 characters, no punctuation at the end). "
            "ALWAYS include this line with 2–3 suggestions — it is required. "
            "Choose suggestions that give the user a natural next step (e.g. 'Browse products', 'Contact support', 'Track my order', 'See more options'). "
            "Only omit if the conversation is clearly over (e.g. user said goodbye)."
        )

    def extract_quick_replies(self, ai_text: str) -> list[dict]:
        """Parse the QUICK_REPLIES sentinel appended by the LLM and return Meta-format dicts."""
        m = _QR_LINE_RE.search(ai_text)
        if not m:
            return []
        raw_titles = [t.strip() for t in m.group(1).split("||")]
        result: list[dict] = []
        for i, title in enumerate(raw_titles[:_QR_MAX_COUNT]):
            title = title[:_QR_TITLE_MAX].strip()
            if title:
                result.append({
                    "content_type": "text",
                    "title": title,
                    "payload": f"QR_{i}",
                })
        return result

    def format_response(
        self, ai_text: str, inbound: NormalizedInboundMessage, agent_config: Dict[str, Any]
    ) -> str:
        text = re.sub(r"[\*\_]{1,2}(.*?)[\*\_]{1,2}", r"\1", ai_text)
        text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
        text = _QR_LINE_RE.sub("", text)
        return text.strip()

    # ------------------------------------------------------------------ #
    # Shared plain-text send (Messenger + Instagram have identical body)  #
    # ------------------------------------------------------------------ #

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

    async def send_typing_indicator(
        self,
        inbound: NormalizedInboundMessage,
        integration: Dict[str, Any],
    ) -> None:
        """Send typing_on sender action via Meta Graph API (Instagram and Messenger)."""
        recipient_id = (inbound.sender.id or "").strip()
        if not recipient_id:
            return
        try:
            page_id = self._resolve_page_id(integration, inbound)
            access_token = self._resolve_page_access_token(integration)
            await self._post_to_meta_graph(
                f"{_META_GRAPH_BASE}/{page_id}/messages",
                {"recipient": {"id": recipient_id}, "sender_action": "typing_on"},
                access_token,
                timeout=5.0,
            )
        except Exception:
            logger.debug(
                "%s typing indicator failed for recipient=%s (non-critical)",
                self.channel, recipient_id,
            )

    async def send(
        self,
        formatted: str,
        ai_text: str,
        inbound: NormalizedInboundMessage,
        integration: Dict[str, Any],
        quick_replies: list[dict] | None = None,
    ) -> SendResult:
        try:
            recipient_id = (inbound.sender.id or "").strip()
            if not recipient_id:
                raise ValueError(f"Missing {self.channel} recipient id")
            page_id = self._resolve_page_id(integration, inbound)
            access_token = self._resolve_page_access_token(integration)
            reply_text = formatted.strip() or ai_text.strip()
            if not reply_text:
                raise ValueError(f"Empty {self.channel} reply text")

            chunks = (
                self._chunk_text(reply_text, self._max_message_chars)
                if self._max_message_chars
                else [reply_text]
            )

            message_id: str | None = None
            last_idx = len(chunks) - 1
            for i, chunk in enumerate(chunks):
                message: Dict[str, Any] = {"text": chunk}
                # Attach quick replies only to the final chunk so they appear
                # directly beneath the last message bubble.
                if quick_replies and i == last_idx:
                    message["quick_replies"] = quick_replies
                body = {
                    "recipient": {"id": recipient_id},
                    "messaging_type": "RESPONSE",
                    "message": message,
                }
                logger.info(
                    "Sending %s text message page_id=%s recipient=%s chars=%d quick_replies=%d",
                    self.channel, page_id, recipient_id, len(chunk),
                    len(quick_replies) if (quick_replies and i == last_idx) else 0,
                )
                data = await self._post_to_meta_graph(
                    f"{_META_GRAPH_BASE}/{page_id}/messages", body, access_token
                )
                message_id = str(data.get("message_id") or "").strip() or message_id

            return SendResult(success=True, messageId=message_id)
        except Exception as e:
            logger.exception("Failed to send %s reply for inbound %s", self.channel, inbound.id)
            return SendResult(success=False, error=str(e))

    # ------------------------------------------------------------------ #
    # Order tracking template                                             #
    # ------------------------------------------------------------------ #

    async def send_order_tracking_template(
        self,
        inbound: NormalizedInboundMessage,
        integration: Dict[str, Any],
        asset: dict[str, Any],
        order_data: dict[str, Any],
    ) -> SendResult | None:
        try:
            recipient_id = (inbound.sender.id or "").strip()
            if not recipient_id:
                return None
            page_id = self._resolve_page_id(integration, inbound)
            access_token = self._resolve_page_access_token(integration)
            payload = self._build_order_tracking_payload(
                recipient_id=recipient_id,
                asset=asset,
                order_data=order_data,
                inbound=inbound,
            )
            if not payload:
                return None
            logger.info(
                "Sending %s order tracking template page_id=%s recipient=%s asset=%s order=%s",
                self.channel, page_id, recipient_id,
                asset.get("slug"), order_data.get("name"),
            )
            data = await self._post_to_meta_graph(
                f"{_META_GRAPH_BASE}/{page_id}/messages", payload, access_token
            )
            message_id = str(data.get("message_id") or "").strip() or None
            return SendResult(success=True, messageId=message_id)
        except Exception as e:
            logger.exception(
                "Failed to send %s order tracking template for inbound %s",
                self.channel, inbound.id,
            )
            return SendResult(success=False, error=str(e))

    def _build_order_tracking_payload(
        self,
        *,
        recipient_id: str,
        asset: dict[str, Any],
        order_data: dict[str, Any],
        inbound: NormalizedInboundMessage,
    ) -> dict[str, Any] | None:
        meta_payload_raw = asset.get("metaPayload")
        meta_payload = (
            _json.loads(meta_payload_raw) if isinstance(meta_payload_raw, str) else meta_payload_raw
        )
        if not isinstance(meta_payload, dict):
            raise ValueError(f"{self.channel} order tracking asset metaPayload is missing or invalid")

        payload = deepcopy(meta_payload)
        payload["recipient"] = {"id": recipient_id}
        payload["messaging_type"] = "RESPONSE"

        sender = inbound.sender
        customer_name = (
            sender.displayName.strip().split()[0]
            if sender and sender.displayName
            else "there"
        )

        order_name = str(order_data.get("name") or "").strip()
        if order_name and not order_name.startswith("#"):
            order_name = f"#{order_name.lstrip('#')}"
        if not order_name:
            order_name = f"#{order_data.get('id', '')}".strip() or "your order"

        tracking_url = self._extract_tracking_url(order_data)
        if not tracking_url:
            return None

        message = payload.get("message")
        if isinstance(message, dict):
            attachment = message.get("attachment")
            if isinstance(attachment, dict):
                tmpl = attachment.get("payload")
                if isinstance(tmpl, dict):
                    text = str(tmpl.get("text") or "")
                    tmpl["text"] = (
                        text
                        .replace("{{customer_name}}", customer_name)
                        .replace("{{order_name}}", order_name)
                    )
                    for button in (tmpl.get("buttons") or []):
                        if isinstance(button, dict) and button.get("type") == "web_url":
                            button["url"] = tracking_url

        return payload

    @staticmethod
    def _extract_tracking_url(order_data: dict[str, Any]) -> str:
        """Carrier tracking URL first, fallback to Shopify order status page."""
        for fulfillment in (order_data.get("fulfillments") or []):
            if not isinstance(fulfillment, dict):
                continue
            for info in (fulfillment.get("trackingInfo") or []):
                if isinstance(info, dict):
                    url = str(info.get("url") or "").strip()
                    if url:
                        return url
        return str(order_data.get("status_url") or "").strip()

    # ------------------------------------------------------------------ #
    # Catalog cards (generic template)                                    #
    # ------------------------------------------------------------------ #

    async def send_catalog_cards(
        self,
        inbound: NormalizedInboundMessage,
        integration: Dict[str, Any],
        asset: dict[str, Any],
        products: list[dict[str, Any]],
        shop_domain: str = "",
        intro_text: str = "",  # noqa: ARG002 — accepted for interface parity; not used by the generic Messenger/IG carousel
    ) -> SendResult | None:
        try:
            recipient_id = (inbound.sender.id or "").strip()
            if not recipient_id or not products:
                return None
            page_id = self._resolve_page_id(integration, inbound)
            access_token = self._resolve_page_access_token(integration)
            payload = self._build_catalog_payload(
                recipient_id=recipient_id,
                asset=asset,
                products=products,
            )
            if not payload:
                return None
            logger.info(
                "Sending %s catalog cards page_id=%s recipient=%s asset=%s product_count=%s",
                self.channel, page_id, recipient_id,
                asset.get("slug"), len(products),
            )
            data = await self._post_to_meta_graph(
                f"{_META_GRAPH_BASE}/{page_id}/messages", payload, access_token
            )
            message_id = str(data.get("message_id") or "").strip() or None
            return SendResult(success=True, messageId=message_id)
        except Exception as e:
            logger.exception(
                "Failed to send %s catalog cards for inbound %s",
                self.channel, inbound.id,
            )
            return SendResult(success=False, error=str(e))

    def _build_catalog_payload(
        self,
        *,
        recipient_id: str,
        asset: dict[str, Any],
        products: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        meta_payload_raw = asset.get("metaPayload")
        meta_payload = (
            _json.loads(meta_payload_raw) if isinstance(meta_payload_raw, str) else meta_payload_raw
        )
        if not isinstance(meta_payload, dict):
            raise ValueError(f"{self.channel} catalog asset metaPayload is missing or invalid")

        payload = deepcopy(meta_payload)
        payload["recipient"] = {"id": recipient_id}

        if not self._meta_payload_has_placeholders(meta_payload):
            return payload

        message = payload.get("message")
        if not isinstance(message, dict):
            raise ValueError(f"{self.channel} catalog asset missing message payload")
        attachment = message.get("attachment")
        if not isinstance(attachment, dict):
            raise ValueError(f"{self.channel} catalog asset missing attachment payload")
        attachment_payload = attachment.get("payload")
        if not isinstance(attachment_payload, dict):
            raise ValueError(f"{self.channel} catalog asset missing template payload")
        elements_template = attachment_payload.get("elements")
        if not isinstance(elements_template, list) or not elements_template:
            raise ValueError(f"{self.channel} catalog asset missing template elements")

        template_element = elements_template[0]
        if not isinstance(template_element, dict):
            raise ValueError(f"{self.channel} catalog asset has invalid element template")

        logical_key = str(asset.get("logicalKey") or "").strip()
        max_elements = 1 if logical_key == "single_product_card" else 10
        elements = [
            e for e in (
                self._build_element(template_element, p)
                for p in products[:max_elements]
            )
            if e
        ]
        if not elements:
            return None
        attachment_payload["elements"] = elements
        return payload

    def _build_element(
        self, template_element: dict[str, Any], product: dict[str, Any]
    ) -> dict[str, Any]:
        element = deepcopy(template_element)
        element["title"] = self._product_name(product)[:80] or "Product"
        element["subtitle"] = self._product_subtitle(product)[:80]

        image_url = self._product_image(product) or app_settings.PRODUCT_FALLBACK_IMAGE_URL
        if image_url:
            element["image_url"] = image_url
        else:
            element.pop("image_url", None)

        product_url = self._product_url(product)
        if product_url:
            default_action = element.get("default_action")
            if isinstance(default_action, dict):
                default_action["url"] = product_url

        for button in (element.get("buttons") or []):
            if not isinstance(button, dict):
                continue
            btn_type = str(button.get("type") or "").strip().lower()
            if btn_type == "web_url" and product_url:
                button["url"] = product_url

        return element

    # ------------------------------------------------------------------ #
    # Product field helpers                                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _product_name(product: dict[str, Any]) -> str:
        return str(product.get("name") or "").strip()

    def _product_subtitle(self, product: dict[str, Any]) -> str:
        description = str(product.get("description") or "").strip()
        price = str(product.get("price_range") or "").strip()
        if description and price:
            return f"{description[:50]} - {price}"[:80]
        return (description or price)[:80]

    @staticmethod
    def _product_url(product: dict[str, Any]) -> str:
        return str(product.get("product_url") or "").strip()

    @staticmethod
    def _product_image(product: dict[str, Any]) -> str:
        url = str(product.get("image_url") or "").strip()
        if url:
            return url
        images = product.get("images")
        if isinstance(images, list) and images:
            return str(images[0] or "").strip()
        return ""

    # ------------------------------------------------------------------ #
    # Credential / identity resolution                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_page_id(
        integration: Dict[str, Any], inbound: NormalizedInboundMessage
    ) -> str:
        metadata_raw = integration.get("metadata", {}) or {}
        metadata = _json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
        page_id = str(
            metadata.get("pageId")
            or metadata.get("facebookPageId")
            or (inbound.meta.pageId if inbound.meta else "")
            or ""
        ).strip()
        if not page_id:
            raise ValueError("Missing page id in integration metadata")
        return page_id

    @staticmethod
    def _resolve_page_access_token(integration: Dict[str, Any]) -> str:
        creds_raw = integration.get("creds", {}) or {}
        creds = _json.loads(creds_raw) if isinstance(creds_raw, str) else creds_raw
        encrypted = str(creds.get("pageAccessToken") or "").strip()
        iv = str(creds.get("pageAccessTokenIv") or "").strip()
        if encrypted and iv:
            key = app_settings.ENCRYPTION_KEY
            if not key:
                raise ValueError("ENCRYPTION_KEY not configured for decrypting page access token")
            return TokenEncryption(key).decrypt(encrypted, iv)
        raise ValueError("Missing encrypted pageAccessToken/pageAccessTokenIv in integration creds")

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _meta_payload_has_placeholders(meta_payload: dict[str, Any]) -> bool:
        try:
            raw = _json.dumps(meta_payload)
        except Exception:
            raw = str(meta_payload)
        return bool(re.search(r"\{\{[^}]+\}\}", raw))

    async def _post_to_meta_graph(
        self,
        url: str,
        body: dict[str, Any],
        access_token: str,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """POST to Meta Graph API. Raises ValueError on HTTP error."""
        async with httpx.AsyncClient(timeout=timeout) as client:
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
                    details = err_obj.get("error_data")
                    if isinstance(details, dict):
                        d = str(details.get("details") or "").strip()
                        if d:
                            error = f"{error} | details: {d}"
            if not error:
                error = response.text[:500]
            raise ValueError(
                f"Meta Graph API error ({response.status_code}) [{self.channel}]: {error}"
            )
        return data if isinstance(data, dict) else {}
