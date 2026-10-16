"""Email notifications for Aide skills (ticket / customer creation).

Skill owners configure `notificationEmails: string[]` in the skill config
(same JSON blob as `form.fields`, `triggerCondition`, etc.). When the tool
fires and succeeds, we fan-out one email per configured recipient.

Fire-and-forget: any failure is logged, never raised — a broken SMTP hop
must not fail the ticket creation itself.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import Any, Iterable, Optional

from langchain_core.runnables import RunnableConfig

from common.integration.registry import get_email

logger = logging.getLogger(__name__)


def _extract_notification_emails(
    config: RunnableConfig, tool_name: str
) -> list[str]:
    """Read + normalize the notificationEmails list for a given tool."""
    configurable = config.get("configurable", {}) or {}
    skill_configs = configurable.get("skill_configs") or {}
    skill_config = skill_configs.get(tool_name) or {}
    raw = skill_config.get("notificationEmails")
    if not isinstance(raw, list):
        return []

    seen: set[str] = set()
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        trimmed = entry.strip().lower()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        out.append(trimmed)
    return out


def _row(label: str, value: str) -> str:
    """One label/value row in the notification email body."""
    return (
        f'<p style="margin:0 0 12px 0;">'
        f'<span style="color:#6B7280;font-size:13px;">{html.escape(label)}</span><br/>'
        f'<span style="color:#111;font-size:15px;font-weight:600;">{html.escape(value)}</span>'
        f'</p>'
    )


def _build_ticket_email_html(
    *,
    ticket_id: str,
    subject: str,
    description: str,
    reason: str,
    priority: str,
    status: str,
    requester_name: Optional[str],
    requester_email: Optional[str],
    channel: Optional[str],
    extras: Iterable[tuple[str, str]] = (),
) -> str:
    """Build a compact HTML body summarizing the new ticket."""
    rows: list[str] = [_row("Subject", subject)]
    if description:
        rows.append(_row("Description", description))
    if reason:
        rows.append(_row("Reason", reason))
    rows.append(_row("Priority", priority))
    rows.append(_row("Status", status))
    if requester_name:
        rows.append(_row("Requester", requester_name))
    if requester_email:
        rows.append(_row("Requester email", requester_email))
    if channel:
        rows.append(_row("Channel", channel))
    for label, value in extras:
        if value:
            rows.append(_row(label, value))
    rows.append(_row("Ticket ID", ticket_id))

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial,sans-serif;'
        'background:#F9FAFB;padding:24px 0;">'
        '<div style="max-width:520px;margin:0 auto;background:#fff;border-radius:16px;'
        'box-shadow:0 8px 25px rgba(0,0,0,0.08);overflow:hidden;">'
        '<div style="padding:32px;text-align:center;'
        'background:linear-gradient(90deg,#F472B6,#A78BFA,#60A5FA);">'
        '<h1 style="color:#fff;font-size:22px;margin:0;">New Support Ticket</h1></div>'
        f'<div style="padding:28px 32px;">{"".join(rows)}'
        '<p style="margin-top:20px;color:#6B7280;font-size:13px;">'
        'You are receiving this because this agent\'s ticket-creation skill '
        'is configured to notify you.</p></div>'
        '<div style="text-align:center;padding:16px;color:#9CA3AF;font-size:12px;'
        'background:#F9FAFB;">© Agent Backend</div></div></div>'
    )


async def send_ticket_notifications(
    *,
    config: RunnableConfig,
    tool_name: str,
    ticket_id: str,
    ticket_data: dict[str, Any],
    channel: Optional[str] = None,
) -> None:
    """Fire one email per configured recipient. Never raises.

    Reads `notificationEmails` from `configurable.skill_configs[tool_name]`
    and dispatches concurrently via the shared EmailService (Resend).
    """
    emails = _extract_notification_emails(config, tool_name)
    if not emails:
        return

    subject_text = str(ticket_data.get("subject") or "New ticket")
    description = str(ticket_data.get("description") or "")
    reason = str(ticket_data.get("reason") or "")
    priority = str(ticket_data.get("priority") or "MEDIUM")
    status = str(ticket_data.get("status") or "OPEN")

    requester_name = (
        ticket_data.get("requesterName")
        or ticket_data.get("name")
        or ticket_data.get("visitorName")
    )
    requester_email = (
        ticket_data.get("requesterEmail")
        or ticket_data.get("email")
        or ticket_data.get("visitorEmail")
    )

    html_body = _build_ticket_email_html(
        ticket_id=ticket_id,
        subject=subject_text,
        description=description,
        reason=reason,
        priority=priority,
        status=status,
        requester_name=str(requester_name) if requester_name else None,
        requester_email=str(requester_email) if requester_email else None,
        channel=channel,
    )
    subject_line = f"New ticket: {subject_text}"[:120]

    try:
        email_service = get_email()
    except Exception as e:  # noqa: BLE001 — treat any registry failure as "skip email".
        logger.warning(
            "send_ticket_notifications: cannot resolve EmailService: %s", e
        )
        return

    async def _send(recipient: str) -> None:
        try:
            await email_service.send_email(recipient, subject_line, html_body)
        except Exception as e:  # noqa: BLE001 — one bad recipient must not fail others.
            logger.warning(
                "send_ticket_notifications: send failed to=%s ticket_id=%s: %s",
                recipient,
                ticket_id,
                e,
            )

    await asyncio.gather(*(_send(e) for e in emails), return_exceptions=True)
    logger.info(
        "send_ticket_notifications: dispatched to %d recipient(s) ticket_id=%s",
        len(emails),
        ticket_id,
    )
