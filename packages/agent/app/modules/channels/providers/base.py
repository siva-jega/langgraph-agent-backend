"""Abstract base class for channel providers.

To implement a new channel provider:
  1. Subclass ``ChannelProvider``
  2. Set the ``channel`` class attribute (e.g. ``"whatsapp"``)
  3. Implement ``normalize_inbound``, ``format_response``, and ``send``
  4. Implement ``get_format_rules``, ``get_few_shots``, ``get_role_block`` to
     shape the system prompt for this channel
  5. Optionally override ``returns_html`` if the formatted response is HTML
"""

from abc import ABC, abstractmethod
from typing import Any

from app.modules.channels.schemas import (
    InboundMessage,
    NormalizedInboundMessage,
    SendResult,
)


class ChannelProvider(ABC):
    """Each channel implements prompt overlay, response formatting, and send logic."""

    channel: str

    returns_html: bool = False
    """Set to ``True`` if ``format_response`` returns HTML (used by the receipt builder)."""

    # ------------------------------------------------------------------ #
    # Routing hooks — called by the service before normalize_inbound     #
    # ------------------------------------------------------------------ #

    def extract_account_id(self, payload: dict) -> str:
        """Extract the channel account identifier from the raw webhook payload."""
        return ""

    def extract_sender_id(self, payload: dict) -> str:
        """Extract the sender's external user id from the raw webhook payload."""
        return ""

    def build_customer_data(self, payload: dict, external_user_id: str) -> dict:
        """Build the data dict for Customer upsert."""
        return {"channel": self.channel, "externalUserId": external_user_id}

    def build_chat_title(self, customer: dict, external_user_id: str) -> str:
        """Build a human-readable title for the chat session."""
        return external_user_id or f"{self.channel} conversation"

    # ------------------------------------------------------------------ #

    async def send_typing_indicator(
        self,
        inbound: NormalizedInboundMessage,
        integration: dict,
    ) -> None:
        """Optionally signal that a reply is being prepared."""
        return None

    async def send_catalog_cards(
        self,
        inbound: NormalizedInboundMessage,
        integration: dict,
        asset: dict[str, Any],
        products: list[dict[str, Any]],
        shop_domain: str = "",
        intro_text: str = "",
    ) -> SendResult | None:
        """Optionally deliver catalog cards. ``intro_text`` is the assistant's
        short lead-in, used as the carousel's intro bubble when supported."""
        return None

    async def send_order_tracking_template(
        self,
        inbound: NormalizedInboundMessage,
        integration: dict,
        asset: dict[str, Any],
        order_data: dict[str, Any],
    ) -> SendResult | None:
        """Optionally deliver an order-tracking template instead of the main text reply."""
        return None

    def _normalize_legacy_payload(self, envelope: InboundMessage) -> NormalizedInboundMessage:
        """Allow already-normalized payloads to keep working during the envelope migration."""
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        if not payload:
            raise ValueError("Envelope payload is empty")

        normalized = {
            "id": payload.get("id") or envelope.id,
            "timestamp": payload.get("timestamp") or envelope.receivedAt,
            "channel": payload.get("channel") or envelope.channel,
            "agentId": payload.get("agentId"),
            "integrationId": payload.get("integrationId"),
            "workspaceId": payload.get("workspaceId"),
            "chatId": payload.get("chatId"),
            "sender": payload.get("sender"),
            "message": payload.get("message"),
            "meta": payload.get("meta"),
        }
        return NormalizedInboundMessage(**normalized)

    @abstractmethod
    def normalize_inbound(self, envelope: InboundMessage) -> NormalizedInboundMessage:
        """Transform a queue envelope into the internal runtime message shape."""

    def get_prompt_overlay(self, inbound: NormalizedInboundMessage) -> str:
        """Deprecated: prefer ``get_format_rules`` / ``get_few_shots`` / ``get_role_block``.

        Kept so adapters not yet migrated keep working. The new builder no longer
        appends overlay text by default — if an adapter still returns content here,
        it will be ignored unless the builder is told otherwise.
        """
        return ""

    def get_format_rules(self, inbound: NormalizedInboundMessage) -> dict:
        """Channel format contract consumed by the prompt builder.

        Keys: ``max_chars`` (int), ``markdown`` (``"full" | "whatsapp" | "none"``),
        ``greeting`` (str or empty), ``sign_off`` (str or empty),
        ``emoji`` (``"sparingly" | "natural" | "none"``),
        ``links`` (``"allow" | "avoid" | "deny"``), ``tone`` (short prose).
        """
        return {
            "max_chars": 0,
            "markdown": "full",
            "greeting": "",
            "sign_off": "",
            "emoji": "sparingly",
            "links": "allow",
            "tone": "",
        }

    def get_few_shots(self, inbound: NormalizedInboundMessage) -> str:
        """Channel-tone few-shot block (already-formatted markdown). Empty if none."""
        return ""

    def get_role_block(
        self, inbound: NormalizedInboundMessage, persona: dict
    ) -> str:
        """Channel-aware role/identity block. Empty to use the generic default."""
        return ""

    @abstractmethod
    def format_response(
        self, ai_text: str, inbound: NormalizedInboundMessage, agent_config: dict
    ) -> str:
        """Transform raw AI text into the channel's output format (plain text, HTML, etc.)."""

    def get_channel_instructions(self, integration: dict | None = None) -> str:
        """Extra channel-specific prompt instructions injected after format rules.

        Override in adapters that need behavioral instructions beyond generic
        formatting (e.g. quick replies for Instagram/Messenger). The default
        returns empty so other channels are unaffected.
        """
        return ""

    def extract_quick_replies(self, ai_text: str) -> list[dict]:
        """Parse suggested quick reply buttons from the raw AI response.

        Override in channel adapters that support inline quick replies (e.g. Instagram).
        Returns a list of Meta-formatted quick reply dicts ready to embed in the message body.
        Default returns an empty list (no quick replies).
        """
        return []

    @abstractmethod
    async def send(
        self,
        formatted: str,
        ai_text: str,
        inbound: NormalizedInboundMessage,
        integration: dict,
        quick_replies: list[dict] | None = None,
    ) -> SendResult:
        """Deliver the formatted response via the channel's API."""


ChannelAdapter = ChannelProvider
