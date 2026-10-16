"""Gmail channel adapter: prompt overlay, HTML formatting, and Gmail API send."""

import base64
import logging
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from jinja2 import Environment, FileSystemLoader

from app.core.config import app_settings
from app.modules.channels.providers.base import ChannelProvider
from app.modules.channels.schemas import (
    InboundMessage,
    NormalizedInboundMessage,
    SendResult,
)
from common.modules.token.encryption import TokenEncryption

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
)


class GmailAdapter(ChannelProvider):
    channel = "gmail"
    returns_html = True

    def normalize_inbound(self, envelope: InboundMessage) -> NormalizedInboundMessage:
        return self._normalize_legacy_payload(envelope)

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def get_format_rules(self, inbound: NormalizedInboundMessage) -> dict:
        sender_name = inbound.sender.displayName or inbound.sender.email or "there"
        return {
            "max_chars": 0,
            "markdown": "none",
            "greeting": f'Open with "Hi {sender_name}," on its own line.',
            "sign_off": 'Close with "Best regards," on its own line, then the persona name.',
            "emoji": "none",
            "links": "allow",
            "tone": "Professional email — 2–4 short paragraphs of plain prose. The reply is rendered as styled HTML, so no markdown headings or bullet symbols.",
        }

    def get_few_shots(self, inbound: NormalizedInboundMessage) -> str:
        persona = "the team"
        return (
            "Product / shopping question — always `get_products`, never the KB.\n"
            "User (subject: Stock check): Do you have the blue raincoat in size M?\n"
            "Action: call `get_products` with query=\"blue raincoat\"\n"
            f"You (after tool result):\nHi <name>,\n\nYes — the Coastal Blue raincoat is available in size M at ₹3,499. "
            "It's currently in stock and ships within 48 hours.\n\nLet me know if you'd like me to share the link.\n\n"
            f"Best regards,\n{persona}\n\n"
            "Order status — always `get_order_status` first; only ticket if the lookup confirms a real issue.\n"
            "User (subject: Order status): Can you tell me where order #1242 is?\n"
            "Action: call `get_order_status` with order_id=\"1242\"\n"
            f"You (after tool result):\nHi <name>,\n\nOrder #1242 shipped yesterday and is currently in transit. "
            "Tracking shows delivery by Tuesday.\n\nDo let me know if anything else comes up.\n\n"
            f"Best regards,\n{persona}\n\n"
            "Policy / non-product question — `search_knowledgebase` (if KB attached); never `get_products`.\n"
            "User (subject: Returns): What's your return window?\n"
            "Action: call `search_knowledgebase` with query=\"return window policy\"\n"
            f"You (after tool result):\nHi <name>,\n\nYou have 30 days from delivery to return an item, free of charge. "
            "Just reply to this email when you're ready and we'll share the return steps.\n\n"
            f"Best regards,\n{persona}\n\n"
            "Purchase intent / quote / callback — `create_lead_aide_channel`. The workspace configures which fields this tool collects (check the schema). Derive `summary` from the conversation, and ask the user for any other required value you don't already have (in one short reply, never a numbered list). Read back the collected values to confirm before calling.\n\n"
            "Complaint / refund / damaged — `create_ticket_aide_channel`. The workspace configures which fields this tool collects (check the schema). Derive `summary` from the conversation, and ask the user for any other required value you don't already have (in one short reply, never a numbered list). Read back the collected values to confirm before calling.\n\n"
            "Frustration / explicit human ask — `human_handoff_trigger_channel`.\n"
            "User (subject: Need a person): Please connect me with a human, this is taking too long.\n"
            "Action: call `human_handoff_trigger_channel` with summary=\"User requested human agent — frustrated\", priority=\"HIGH\"\n"
            f"You (after tool result):\nHi <name>,\n\nOf course — I've passed this thread to a teammate who will reply directly within the next few hours.\n\n"
            f"Best regards,\n{persona}\n"
        )

    def get_role_block(self, inbound: NormalizedInboundMessage, persona: dict) -> str:
        return ""

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_response(
        self, ai_text: str, inbound: NormalizedInboundMessage, agent_config: dict
    ) -> str:
        """Convert AI plain text to styled HTML email using the Jinja2 template."""
        body_html = self._text_to_html_paragraphs(ai_text)

        config_dict = agent_config.get("config", {}) or {}
        persona = config_dict.get("persona", {}) or {}
        persona_name = persona.get("personaName") or "AI Assistant"
        brand_color = config_dict.get("brandColor") or "#4F46E5"

        template = _jinja_env.get_template("reply.html.j2")
        return template.render(
            persona_name=persona_name,
            brand_color=brand_color,
            body_html=body_html,
        )

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def send(
        self,
        formatted: str,
        ai_text: str,
        inbound: NormalizedInboundMessage,
        integration: dict,
        quick_replies: list[dict] | None = None,
    ) -> SendResult:
        """Send a threaded HTML reply via Gmail API."""
        try:
            gmail_service = self._build_gmail_service(integration)

            msg = MIMEMultipart("alternative")
            msg["To"] = inbound.sender.email or ""
            msg["Subject"] = f"Re: {inbound.message.subject or ''}"

            if inbound.message.originalMessageId:
                msg["In-Reply-To"] = inbound.message.originalMessageId
                msg["References"] = inbound.message.originalMessageId

            msg.attach(MIMEText(ai_text, "plain"))
            msg.attach(MIMEText(formatted, "html"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

            body: dict = {"raw": raw}
            if inbound.message.threadId:
                body["threadId"] = inbound.message.threadId

            result = (
                gmail_service.users()
                .messages()
                .send(userId="me", body=body)
                .execute()
            )

            sent_id = result.get("id", "")
            logger.info(
                "Gmail reply sent id=%s thread=%s to=%s",
                sent_id,
                inbound.message.threadId,
                inbound.sender.email,
            )
            return SendResult(success=True, messageId=sent_id)

        except Exception as e:
            logger.exception("Failed to send Gmail reply for inbound %s", inbound.id)
            return SendResult(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _text_to_html_paragraphs(text: str) -> str:
        """Convert plain text to email-safe HTML paragraphs."""
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)

        paragraphs = text.split("\n\n")
        html_parts: list[str] = []
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            p = p.replace("\n", "<br>")
            html_parts.append(f"<p style='margin:0 0 16px 0;'>{p}</p>")
        return "".join(html_parts)

    @staticmethod
    def _build_gmail_service(integration: dict):
        """Build an authorized Gmail API service from the integration's encrypted creds."""
        import json as _json

        creds_raw = integration.get("creds", {}) or {}
        creds = _json.loads(creds_raw) if isinstance(creds_raw, str) else creds_raw
        enc_data = creds.get("gmailRefreshEnc")
        iv = creds.get("gmailRefreshIv")
        if not enc_data or not iv:
            raise ValueError("Missing encrypted Gmail credentials in integration")

        encryption_key = app_settings.ENCRYPTION_KEY
        if not encryption_key:
            raise ValueError("ENCRYPTION_KEY not configured in AI backend")

        refresh_token = TokenEncryption(encryption_key).decrypt(enc_data, iv)

        client_id = app_settings.GOOGLE_CLIENT_ID
        client_secret = app_settings.GOOGLE_CLIENT_SECRET
        if not client_id or not client_secret:
            raise ValueError("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not configured")

        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
        )

        return build("gmail", "v1", credentials=credentials, cache_discovery=False)
