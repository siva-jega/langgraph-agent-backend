"""Utility functions for Google Calendar integration."""

import asyncio
from typing import Optional, Dict, Any, List

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from common.integration import get_token
from common.modules.token import TokenResponse
import logging
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError
from .models import CalendarEvent

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(5)


async def get_gcalendar_credentials(user_id: str, connectorId: str | None = None) -> Optional[TokenResponse]:
    """Get Google Calendar credentials from token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id, connector_name="googlecalendar", connectorId=connectorId, needs_refresh=True
        )
        return user_data
    except Exception as e:
        logger.error(f"Error getting Google Calendar credentials: {str(e)}")
        return None


def build_gcalendar_service(access_token: str):
    """Build Google Calendar service instance from access token."""
    credentials = Credentials(token=access_token)
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    reraise=True,
)
async def execute_gcalendar_request(request: Any) -> Any:
    """Execute Google Calendar API request with retry logic."""
    async with _semaphore:
        try:
            response = request.execute()
            logger.debug(
                f"Google Calendar API response received. Type: {type(response)}"
            )
            return response
        except HttpError as e:
            status = e.resp.status if e.resp else None
            logger.error(f"Google Calendar API HttpError: status={status}, error={e}")
            if status in [429, 500, 502, 503, 504]:
                raise Exception(
                    f"Google Calendar API server/quota error: {status} - {e}"
                )
            if status in [401, 403]:
                raise ValueError("Google Calendar credentials expired")
            raise Exception(f"Google Calendar API error: {status} - {e}")
        except RetryError as e:
            # Extract underlying exception from RetryError
            underlying_error = e.last_attempt.exception() if e.last_attempt else e
            error_msg = f"RetryError after {e.last_attempt.attempt_number if e.last_attempt else 'unknown'} attempts: {underlying_error}"
            logger.error(f"Google Calendar API RetryError: {error_msg}")
            raise underlying_error if underlying_error else e
        except Exception as e:
            error_msg = f"An unexpected error occurred with Google Calendar API: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise Exception(error_msg) from e


def process_event(event: dict) -> CalendarEvent:
    """Process raw Google Calendar event into CalendarEvent object."""
    start = event.get("start", {})
    end = event.get("end", {})
    start_time = start.get("dateTime", start.get("date", "Unknown"))
    end_time = end.get("dateTime", end.get("date", "Unknown"))
    attendees = [a.get("email", "Unknown") for a in event.get("attendees", [])]
    organizer_email = event.get("organizer", {}).get("email", "Unknown")
    html_link = event.get("htmlLink", "")
    meet_link = event.get("hangoutLink")
    if not meet_link:
        conference_data = event.get("conferenceData", {})
        entry_points = conference_data.get("entryPoints", [])
        for ep in entry_points:
            if ep.get("entryPointType") == "video" and ep.get("uri"):
                meet_link = ep["uri"]
                break
    return CalendarEvent(
        summary=event.get("summary", "No Title"),
        organizer_email=organizer_email,
        start_time=start_time,
        end_time=end_time,
        location=event.get("location", "Unknown"),
        description=event.get("description", "No Description"),
        attendees=attendees,
        id=event.get("id", "Unknown"),
        html_link=html_link,
        meet_link=meet_link,
    )


def process_events(events: List[dict]) -> List[CalendarEvent]:
    """Process a list of raw Google Calendar events into CalendarEvent objects."""
    return [process_event(e) for e in events]
