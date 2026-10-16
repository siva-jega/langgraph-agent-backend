"""
Email service: send email via Resend API with retry.

Uses AppSettings (RESEND_API_KEY, SENDER_EMAIL). Lazy-connects on first send_email().
"""

import asyncio
import logging
from typing import Any, Optional

import httpx

from common.modules.settings_protocols import SupportsEmailSettings

logger = logging.getLogger(__name__)

_RESEND_BASE = "https://api.resend.com"


def _meeting_confirmation_html(
    invitee_name: str,
    date_time_display: str,
    cancel_url: Optional[str] = None,
    reschedule_url: Optional[str] = None,
    booking_url: Optional[str] = None,
    meeting_title: Optional[str] = None,
    meeting_description: Optional[str] = None,
) -> str:
    """Build HTML body for meeting confirmation email."""
    import html
    name = invitee_name or "there"
    lines = [
        f"<p>Hi {name},</p>",
        "<p>Your meeting has been scheduled.</p>",
        f"<p><strong>When:</strong> {date_time_display}</p>",
    ]
    if meeting_title:
        lines.append(f"<p><strong>Title:</strong> {html.escape(meeting_title)}</p>")
    if meeting_description:
        lines.append(f"<p><strong>Description:</strong></p><p>{html.escape(meeting_description)}</p>")
    if reschedule_url:
        lines.append(f'<p><a href="{reschedule_url}">Reschedule</a></p>')
    if cancel_url:
        lines.append(f'<p><a href="{cancel_url}">Cancel</a></p>')
    if booking_url and not reschedule_url:
        lines.append(f'<p>Use this link to confirm or pick a time: <a href="{booking_url}">Book here</a></p>')
    lines.append("<p>See you then!</p>")
    return "".join(lines)


class EmailService:
    """Send email via Resend. Single responsibility; no custom config."""

    def __init__(
        self,
        settings: SupportsEmailSettings,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
    ):
        self._settings = settings
        self._retry_attempts = retry_attempts
        self._retry_delay = retry_delay
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> None:
        if self._client is not None:
            return
        api_key = self._settings.RESEND_API_KEY
        if not api_key:
            raise ValueError("RESEND_API_KEY is not configured")
        self._client = httpx.AsyncClient(
            base_url=_RESEND_BASE,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        logger.info("Email client initialized")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_email(
        self, to: str, subject: str, body: str
    ) -> Optional[dict[str, Any]]:
        """Send email. Connects lazily; retries with backoff on failure."""
        await self._ensure_client()
        from_email = self._settings.SENDER_EMAIL or "noreply@example.com"
        payload = {
            "from": from_email,
            "to": [to] if isinstance(to, str) else to,
            "subject": subject,
            "html": body,
        }
        for attempt in range(self._retry_attempts):
            try:
                r = await self._client.post("/emails", json=payload)
                r.raise_for_status()
                return r.json()
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Resend API error attempt %s: %s - %s",
                    attempt + 1, e.response.status_code, e.response.text,
                )
                if attempt == self._retry_attempts - 1:
                    raise
            except httpx.RequestError as e:
                logger.warning("Resend request error attempt %s: %s", attempt + 1, e)
                if attempt == self._retry_attempts - 1:
                    raise
            if attempt < self._retry_attempts - 1:
                await asyncio.sleep(self._retry_delay * (2**attempt))
        return None

    async def send_meeting_confirmation(
        self,
        to: str,
        date_time_display: str,
        invitee_name: Optional[str] = None,
        cancel_url: Optional[str] = None,
        reschedule_url: Optional[str] = None,
        booking_url: Optional[str] = None,
        meeting_title: Optional[str] = None,
        meeting_description: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Send a meeting confirmation email via Resend. Does not raise on failure."""
        if not to or not date_time_display:
            return None
        subject = meeting_title if meeting_title else "Meeting scheduled"
        body = _meeting_confirmation_html(
            invitee_name=invitee_name or "",
            date_time_display=date_time_display,
            cancel_url=cancel_url,
            reschedule_url=reschedule_url,
            booking_url=booking_url,
            meeting_title=meeting_title,
            meeting_description=meeting_description,
        )
        try:
            return await self.send_email(to, subject, body)
        except Exception as e:
            logger.warning("Failed to send meeting confirmation email to %s: %s", to, e)
            return None
