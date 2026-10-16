"""Utility functions for Calendly API integration."""

import asyncio
import httpx
import logging
from typing import Any, Dict, List, Optional

from tenacity import retry, stop_after_attempt, wait_exponential
from common.integration import get_token
from common.modules.token import TokenResponse
from .models import (
    CalendlyEvent,
    CalendlyEventType,
    CalendlyInvitee,
    CalendlyUser,
    CalendlySchedulingLink,
    CalendlyNoShow,
    CalendlyMeetingRecap,
    CalendlyMeetingTranscript,
)

logger = logging.getLogger(__name__)

CALENDLY_API_BASE = "https://api.calendly.com"
_semaphore = asyncio.Semaphore(5)


async def get_calendly_credentials(
    user_id: str, connector_id: str | None = None
) -> Optional[TokenResponse]:
    """Get Calendly OAuth credentials from the token service.

    Calendly now uses standard OAuth credentials (access_token + refresh_token).
    The token service handles decryption and automatic refresh via the NestJS backend.
    """
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="calendly",
            connectorId=connector_id,
            needs_refresh=True,
        )
        if user_data and user_data.token:
            return user_data
        return None
    except Exception as e:
        logger.error("Error getting Calendly credentials: %s", e)
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=lambda retry_state: (
        retry_state.outcome is not None
        and retry_state.outcome.failed
        and not isinstance(retry_state.outcome.exception(), ValueError)
    ),
    reraise=True,
)
async def make_calendly_request(
    access_token: str,
    method: str,
    endpoint: str,
    **kwargs: Any,
) -> Any:
    """Make a Calendly API request with retry logic.

    Args:
        access_token: Calendly access token (PAT or OAuth token).
        method: HTTP method (GET, POST, PATCH, DELETE).
        endpoint: API endpoint path (e.g. /users/me) or full URL.
        **kwargs: Additional parameters for the request (json, params, etc.).
    """
    url = endpoint if endpoint.startswith("http") else f"{CALENDLY_API_BASE}{endpoint}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with _semaphore:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(method, url, headers=headers, **kwargs)

            if response.status_code == 401:
                raise ValueError(
                    "Calendly authentication failed. The stored credentials are not a valid "
                    "API token. Please go to Calendly > Integrations > API & Webhooks, "
                    "copy your Personal Access Token, and re-connect Calendly."
                )
            if response.status_code == 403:
                detail = response.text[:500] if response.text else ""
                scope_hint = ""
                try:
                    payload = response.json()
                    required = payload.get("required_scopes") or payload.get(
                        "details", {}
                    ).get("required_scopes")
                    if required:
                        scope_hint = (
                            f" Reconnect Calendly in Apps so the token includes: "
                            f"{', '.join(required)}."
                        )
                except Exception:
                    pass
                raise ValueError(
                    "Insufficient permissions for this Calendly operation."
                    f"{scope_hint} "
                    "Enabling scopes in the Calendly developer portal is not enough — "
                    "disconnect and reconnect Calendly in Apps after scopes are updated. "
                    f"Detail: {detail}"
                )
            if response.status_code == 404:
                raise ValueError(f"Calendly resource not found: {endpoint}")
            if response.status_code >= 400:
                body = response.text[:200]
                raise ValueError(f"Calendly API error {response.status_code}: {body}")

            if response.status_code == 204:
                return {}
            return response.json()


def extract_uuid_from_uri(uri: str) -> str:
    """Extract the UUID from a Calendly resource URI."""
    return uri.rstrip("/").split("/")[-1]


def parse_event(data: Dict[str, Any]) -> CalendlyEvent:
    """Parse raw Calendly event data into a CalendlyEvent."""
    return CalendlyEvent(
        uri=data.get("uri", ""),
        name=data.get("name"),
        status=data.get("status"),
        start_time=data.get("start_time"),
        end_time=data.get("end_time"),
        event_type=data.get("event_type"),
        location=data.get("location"),
        invitees_counter=data.get("invitees_counter"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        event_memberships=data.get("event_memberships"),
        event_guests=data.get("event_guests"),
        calendar_event=data.get("calendar_event"),
        cancellation=data.get("cancellation"),
        meeting_notes_plain=data.get("meeting_notes_plain"),
        meeting_notes_html=data.get("meeting_notes_html"),
    )


def parse_invitee(data: Dict[str, Any]) -> CalendlyInvitee:
    """Parse raw Calendly invitee data."""
    return CalendlyInvitee(
        uri=data.get("uri", ""),
        name=data.get("name"),
        email=data.get("email"),
        status=data.get("status"),
        timezone=data.get("timezone"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        cancel_url=data.get("cancel_url"),
        reschedule_url=data.get("reschedule_url"),
    )


def parse_user(data: Dict[str, Any]) -> CalendlyUser:
    """Parse raw Calendly user data."""
    return CalendlyUser(
        uri=data.get("uri", ""),
        name=data.get("name", ""),
        slug=data.get("slug"),
        email=data.get("email"),
        scheduling_url=data.get("scheduling_url"),
        timezone=data.get("timezone"),
        avatar_url=data.get("avatar_url"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        current_organization=data.get("current_organization"),
    )


def parse_event_type(data: Dict[str, Any]) -> CalendlyEventType:
    """Parse raw Calendly event type data."""
    return CalendlyEventType(
        uri=data.get("uri", ""),
        name=data.get("name"),
        active=data.get("active"),
        slug=data.get("slug"),
        scheduling_url=data.get("scheduling_url"),
        duration=data.get("duration"),
        kind=data.get("kind"),
        type=data.get("type"),
        color=data.get("color"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        description_plain=data.get("description_plain"),
    )
