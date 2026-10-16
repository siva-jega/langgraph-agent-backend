"""Calendly tools for event management, scheduling, and user lookup."""

import logging
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, List, Optional, Tuple

from dateutil import parser as date_parser
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from common.integration import get_email
from common.tools.utils.writer import emit_update
from .models import CalendlyResult
from .utils import (
    get_calendly_credentials,
    make_calendly_request,
    extract_uuid_from_uri,
    parse_event,
    parse_event_type,
    parse_user,
    parse_invitee,
)

logger = logging.getLogger(__name__)


async def _send_meeting_confirmation_email(
    to: str,
    date_time_display: str,
    invitee_name: Optional[str] = None,
    cancel_url: Optional[str] = None,
    reschedule_url: Optional[str] = None,
    booking_url: Optional[str] = None,
    meeting_title: Optional[str] = None,
    meeting_description: Optional[str] = None,
) -> None:
    """Send meeting confirmation email via Resend. Logs and ignores failures."""
    try:
        email_service = get_email()
        await email_service.send_meeting_confirmation(
            to=to,
            date_time_display=date_time_display,
            invitee_name=invitee_name,
            cancel_url=cancel_url,
            reschedule_url=reschedule_url,
            booking_url=booking_url,
            meeting_title=meeting_title,
            meeting_description=meeting_description,
        )
    except Exception as e:
        logger.warning("Could not send meeting confirmation email to %s: %s", to, e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_access_token(config: RunnableConfig) -> Tuple[Optional[str], Optional[str]]:
    """Return (access_token, error_message) from RunnableConfig."""
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    if not user_id:
        return None, "No user_id in configuration"

    all_apps = configurable.get("apps_config", []) or configurable.get("apps", [])
    calendly_app = next(
        (a for a in all_apps if a.get("slug") == "calendly"), None
    )
    connector_id = calendly_app.get("id") if calendly_app else None

    creds = await get_calendly_credentials(user_id, connector_id=connector_id)
    if not creds or not creds.token:
        return None, "Calendly is not connected. Please connect Calendly in the Apps section first."
    return creds.token, None


async def _get_current_user_uri(access_token: str) -> str:
    """Fetch the authenticated user's URI (needed for many Calendly API calls)."""
    data = await make_calendly_request(access_token, "GET", "/users/me")
    return data["resource"]["uri"]


async def _auto_resolve_event_type(access_token: str, user_uri: str) -> Optional[str]:
    """Auto-resolve the first active event type URI for the user."""
    resp = await make_calendly_request(
        access_token, "GET", "/event_types", params={"user": user_uri, "active": "true"}
    )
    event_types = resp.get("collection", [])
    if event_types:
        return event_types[0].get("uri", "")
    return None


def _error_result(msg: str) -> Tuple[str, Dict[str, Any]]:
    return msg, {"name": "Calendly", "tool": "Error", "data": {"success": False, "message": msg}}


def _parse_datetime(date_str: str, time_str: str, tz_name: str = "Asia/Kolkata") -> datetime:
    """Parse flexible date and time strings into a timezone-aware datetime.

    Returns a datetime object in the given timezone (not converted to UTC yet).
    Caller is responsible for converting to UTC if needed.
    """
    try:
        import zoneinfo
        tz: tzinfo = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = timezone(timedelta(hours=5, minutes=30))

    now = datetime.now(tz)

    date_lower = date_str.strip().lower()
    if date_lower == "today":
        date_obj = now.date()
    elif date_lower == "tomorrow":
        date_obj = (now + timedelta(days=1)).date()
    elif date_lower == "day after tomorrow":
        date_obj = (now + timedelta(days=2)).date()
    else:
        weekdays = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        stripped = date_lower.replace("next ", "")
        if stripped in weekdays:
            target = weekdays[stripped]
            days_ahead = (target - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            date_obj = (now + timedelta(days=days_ahead)).date()
        else:
            parsed = date_parser.parse(date_str, dayfirst=False, fuzzy=True)
            date_obj = parsed.date()

    time_parsed = date_parser.parse(time_str, fuzzy=True)
    return datetime(
        date_obj.year, date_obj.month, date_obj.day,
        time_parsed.hour, time_parsed.minute, 0,
        tzinfo=tz,
    )


def _to_utc_iso(dt: datetime) -> str:
    """Convert a timezone-aware datetime to UTC ISO 8601 string with trailing Z."""
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _get_event_type_details(access_token: str, event_type_uri: str) -> Dict[str, Any]:
    """Fetch full event type details including location configuration."""
    uuid = extract_uuid_from_uri(event_type_uri)
    resp = await make_calendly_request(access_token, "GET", f"/event_types/{uuid}")
    return resp.get("resource", resp)


async def _create_scheduling_link_or_fallback(
    access_token: str,
    event_type_uri: str,
) -> str:
    """Create a single-use scheduling link, or fall back to the event type's public URL."""
    try:
        scheduling_link_resp = await make_calendly_request(
            access_token,
            "POST",
            "/scheduling_links",
            json={
                "max_event_count": 1,
                "owner": event_type_uri,
                "owner_type": "EventType",
            },
        )
        return scheduling_link_resp.get("resource", {}).get("booking_url", "") or ""
    except ValueError as err:
        if "Insufficient permissions" not in str(err):
            raise
        logger.info(
            "POST /scheduling_links unavailable (missing scheduling_links:write), "
            "using event type scheduling_url"
        )

    et_details = await _get_event_type_details(access_token, event_type_uri)
    return et_details.get("scheduling_url") or ""


async def _get_available_times(
    access_token: str,
    event_type_uri: str,
    start_time: str,
    end_time: str,
) -> List[Dict[str, Any]]:
    """Fetch available booking slots for an event type within a time range.

    start_time / end_time must be UTC ISO 8601 strings.
    The Calendly API limits queries to 7 days per request.
    """
    resp = await make_calendly_request(
        access_token,
        "GET",
        "/event_type_available_times",
        params={
            "event_type": event_type_uri,
            "start_time": start_time,
            "end_time": end_time,
        },
    )
    return resp.get("collection", [])


# ---------------------------------------------------------------------------
# 1. Cancel an Event
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def cancel_event_calendly(
    event_uuid: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Cancel a Calendly event. The agent should obtain the event UUID from a
    previous find_event_calendly call or from context. Do NOT ask the user for a UUID.

    Args:
        event_uuid: The UUID of the scheduled event to cancel.
        config: Runtime configuration.
    """
    try:
        emit_update("Cancelling Calendly event…")
        access_token, err = await _get_access_token(config)
        if err:
            return _error_result(err)

        await make_calendly_request(
            access_token,
            "POST",
            f"/scheduled_events/{event_uuid}/cancellation",
            json={"reason": "Cancelled via AI assistant"},
        )

        msg = f"Successfully cancelled event {event_uuid}."
        return msg, {
            "name": "Calendly",
            "tool": "Cancel Event",
            "data": {"success": True, "message": msg, "event_uuid": event_uuid},
        }
    except Exception as e:
        logger.error("Error cancelling Calendly event: %s", e)
        return _error_result(f"Failed to cancel event: {e}")


# ---------------------------------------------------------------------------
# 2. Create One-Off Meeting Link
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def create_one_off_meeting_calendly(
    name: str,
    duration_minutes: int,
    config: RunnableConfig,
    date_setting_type: str = "indefinite",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    location_kind: Optional[str] = None,
    location_value: Optional[str] = None,
    max_event_count: int = 1,
) -> Tuple[str, Any]:
    """Create a one-off meeting link in Calendly that can be shared with someone.

    The agent should infer reasonable defaults: use 30 minutes if no duration is
    mentioned, use "indefinite" date_setting_type unless the user specifies a date range.
    Do NOT ask the user for technical parameters.

    Args:
        name: Name/title of the meeting (e.g. "Quick Chat with John").
        duration_minutes: Duration in minutes (default to 30 if user doesn't specify).
        config: Runtime configuration.
        date_setting_type: "date_range" or "indefinite" (default "indefinite").
        start_date: Start date YYYY-MM-DD (only if date_setting_type is "date_range").
        end_date: End date YYYY-MM-DD (only if date_setting_type is "date_range").
        location_kind: Optional - "google_conference", "zoom_conference", "physical", "outbound_call".
        location_value: Optional - address or phone (only for physical/outbound_call).
        max_event_count: Max bookings allowed (default 1 for one-off).
    """
    try:
        emit_update("Creating one-off meeting in Calendly…")
        access_token, err = await _get_access_token(config)
        if err:
            return _error_result(err)

        user_uri = await _get_current_user_uri(access_token)

        date_setting: Dict[str, Any] = {"type": date_setting_type}
        if date_setting_type == "date_range":
            if start_date:
                date_setting["start_date"] = start_date
            if end_date:
                date_setting["end_date"] = end_date

        event_type_body: Dict[str, Any] = {
            "name": name,
            "host": user_uri,
            "duration": duration_minutes,
            "date_setting": date_setting,
        }
        if location_kind:
            loc: Dict[str, Any] = {"kind": location_kind}
            if location_value:
                loc["location"] = location_value
            event_type_body["location"] = loc

        event_type_resp = await make_calendly_request(
            access_token, "POST", "/one_off_event_types", json=event_type_body
        )
        event_type_uri = event_type_resp.get("resource", {}).get("uri", "")

        scheduling_link_resp = await make_calendly_request(
            access_token,
            "POST",
            "/scheduling_links",
            json={
                "max_event_count": max_event_count,
                "owner": event_type_uri,
                "owner_type": "EventType",
            },
        )
        booking_url = scheduling_link_resp.get("resource", {}).get("booking_url", "")

        msg = f"One-off meeting '{name}' created. Booking URL: {booking_url}"
        return msg, {
            "name": "Calendly",
            "tool": "Create One-Off Meeting Link",
            "data": {
                "success": True,
                "message": msg,
                "booking_url": booking_url,
                "event_type_uri": event_type_uri,
            },
        }
    except Exception as e:
        logger.error("Error creating one-off meeting: %s", e)
        return _error_result(f"Failed to create one-off meeting: {e}")


# ---------------------------------------------------------------------------
# 3. Book Meeting for Invitee
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def book_meeting_calendly(
    invitee_email: str,
    date: str,
    time: str,
    config: RunnableConfig,
    invitee_name: Optional[str] = None,
    timezone_name: str = "Asia/Kolkata",
    meeting_title: Optional[str] = None,
    meeting_description: Optional[str] = None,
) -> Tuple[str, Any]:
    """Book a meeting for an invitee on Calendly.

    **Required info — gather BEFORE calling this tool**:
      - invitee_email (must be a real email address)
      - date
      - time
      - invitee_name (use the name from context if known; if the user only said
        "book a meeting with Ayush" without an email, ask for the email — do NOT
        call this tool without an email).

    If any of the above are missing, ask the user for them in a single
    friendly question — for example: "Sure — what's their email and the
    date/time you'd like?". Do NOT call this tool and then ask; ask first.

    **Title & description** are optional. You may ask once
    ("Any meeting title or agenda?"); if the user skips, proceed without them.

    Do NOT ask for event type, URI, UUID, or timezone; the tool resolves those.

    On paid Calendly plans the meeting is booked directly; otherwise a
    one-click scheduling link is created and returned.

    Args:
        invitee_email: Email of the person to invite (e.g. "john@example.com").
        date: Meeting date in any format: "tomorrow", "2026-03-06", "March 6", "next Monday".
        time: Meeting time in any format: "3 PM", "15:00", "3:30 PM", "10 AM".
        config: Runtime configuration.
        invitee_name: Name of the invitee if known (e.g. "Ayush Kumar").
        timezone_name: IANA timezone of the invitee (default "Asia/Kolkata").
        meeting_title: Optional title for the meeting (included in confirmation email).
        meeting_description: Optional description/agenda (included in confirmation email).
    """
    try:
        emit_update("Booking Calendly meeting…")
        access_token, err = await _get_access_token(config)
        if err:
            return _error_result(err)

        user_uri = await _get_current_user_uri(access_token)
        event_type_uri = await _auto_resolve_event_type(access_token, user_uri)
        if not event_type_uri:
            return _error_result(
                "I need an active event type in your Calendly account to book against. "
                "Create one in your Calendly dashboard and we're ready to go."
            )

        try:
            requested_dt = _parse_datetime(date, time, timezone_name)
        except Exception as parse_err:
            logger.error("Failed to parse date/time: date=%s time=%s err=%s", date, time, parse_err)
            return _error_result(
                f"I need a clearer date and time to book this. "
                f"Try something like 'tomorrow at 3 PM' or '2026-03-06 at 15:00'."
            )

        booked_display = requested_dt.strftime("%B %d, %Y at %I:%M %p")

        # --- Try direct booking via Scheduling API (paid plans) ---
        try:
            return await _book_directly(
                access_token, event_type_uri, invitee_email,
                requested_dt, timezone_name, invitee_name, date,
                meeting_title=meeting_title,
                meeting_description=meeting_description,
            )
        except ValueError as direct_err:
            if "Insufficient permissions" not in str(direct_err):
                raise
            logger.info(
                "Direct booking not available (missing scope or plan), falling back to scheduling link"
            )

        # --- Fallback: single-use link API, then public event-type URL (no extra scope) ---
        booking_url = await _create_scheduling_link_or_fallback(
            access_token, event_type_uri
        )
        if not booking_url:
            return _error_result(
                "Could not create a Calendly booking link. Disconnect and reconnect "
                "Calendly in Apps (scheduling_links:write), or use a Calendly plan "
                "that supports the Scheduling API."
            )

        await _send_meeting_confirmation_email(
            to=invitee_email,
            date_time_display=booked_display,
            invitee_name=invitee_name,
            booking_url=booking_url,
            meeting_title=meeting_title,
            meeting_description=meeting_description,
        )

        who = invitee_name or invitee_email
        msg = (
            f"Here's the booking link for {who} for {booked_display}: {booking_url} — "
            f"share it and they can confirm the slot in one click."
        )
        return msg, {
            "name": "Calendly",
            "tool": "Book Meeting for Invitee",
            "data": {
                "success": True,
                "message": msg,
                "booking_url": booking_url,
                "invitee_email": invitee_email,
                "invitee_name": invitee_name,
                "requested_time": booked_display,
            },
        }
    except Exception as e:
        logger.error("Error booking Calendly meeting: %s", e)
        return _error_result(f"Failed to book meeting: {e}")


async def _book_directly(
    access_token: str,
    event_type_uri: str,
    invitee_email: str,
    requested_dt: datetime,
    timezone_name: str,
    invitee_name: Optional[str],
    date_label: str,
    meeting_title: Optional[str] = None,
    meeting_description: Optional[str] = None,
) -> Tuple[str, Any]:
    """Attempt direct booking via POST /invitees (Scheduling API, paid plans only).

    Raises ValueError on 403 so the caller can fall back to scheduling links.
    """
    requested_utc = _to_utc_iso(requested_dt)

    day_start = requested_dt.replace(hour=0, minute=0, second=0)
    day_end = day_start + timedelta(days=1)
    available_slots = await _get_available_times(
        access_token,
        event_type_uri,
        _to_utc_iso(day_start),
        _to_utc_iso(day_end),
    )

    if not available_slots:
        return _error_result(
            f"{date_label} has no open slots in your Calendly availability — "
            "pick another day or widen your availability settings and we'll book it."
        )

    chosen_slot = requested_utc
    requested_utc_dt = requested_dt.astimezone(timezone.utc)
    best_delta = None
    for slot in available_slots:
        slot_time_str = slot.get("start_time", "")
        if not slot_time_str:
            continue
        parsed_slot = date_parser.isoparse(slot_time_str)
        if parsed_slot.tzinfo is None:
            parsed_slot = parsed_slot.replace(tzinfo=timezone.utc)
        delta = abs((parsed_slot - requested_utc_dt).total_seconds())
        if best_delta is None or delta < best_delta:
            best_delta = delta
            chosen_slot = slot_time_str

    et_details = await _get_event_type_details(access_token, event_type_uri)

    body: Dict[str, Any] = {
        "event_type": event_type_uri,
        "start_time": chosen_slot,
        "invitee": {
            "email": invitee_email,
            "timezone": timezone_name,
        },
    }
    if invitee_name:
        body["invitee"]["name"] = invitee_name

    locations = et_details.get("locations") or et_details.get("location", {})
    if isinstance(locations, list) and locations:
        loc_kind = locations[0].get("kind")
        if loc_kind:
            body["location_configuration"] = {"kind": loc_kind}
    elif isinstance(locations, dict) and locations.get("kind"):
        body["location_configuration"] = {"kind": locations["kind"]}

    resp = await make_calendly_request(
        access_token, "POST", "/invitees", json=body
    )

    resource = resp.get("resource", resp)
    event_uri = resource.get("event", "")
    cancel_url = resource.get("cancel_url", "")
    reschedule_url = resource.get("reschedule_url", "")
    invitee_tz = resource.get("timezone", timezone_name)

    booked_display = chosen_slot
    try:
        booked_dt = date_parser.isoparse(chosen_slot)
        import zoneinfo
        display_tz = zoneinfo.ZoneInfo(invitee_tz)
        booked_display = booked_dt.astimezone(display_tz).strftime("%B %d, %Y at %I:%M %p %Z")
    except Exception:
        pass

    msg = f"Meeting booked with {invitee_email} on {booked_display}."
    await _send_meeting_confirmation_email(
        to=invitee_email,
        date_time_display=booked_display,
        invitee_name=invitee_name,
        cancel_url=cancel_url,
        reschedule_url=reschedule_url,
        meeting_title=meeting_title,
        meeting_description=meeting_description,
    )
    return msg, {
        "name": "Calendly",
        "tool": "Book Meeting for Invitee",
        "data": {
            "success": True,
            "message": msg,
            "event_uri": event_uri,
            "invitee_email": invitee_email,
            "start_time": chosen_slot,
            "cancel_url": cancel_url,
            "reschedule_url": reschedule_url,
        },
    }


# ---------------------------------------------------------------------------
# 4. Mark Invitee as No Show
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def mark_no_show_calendly(
    invitee_uri: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Mark a specific invitee as a no-show on Calendly.

    The agent should obtain the invitee_uri from a previous find_event_calendly
    call. Do NOT ask the user for a URI.

    Args:
        invitee_uri: The full URI of the invitee (obtained from find_event_calendly results).
        config: Runtime configuration.
    """
    try:
        emit_update("Marking invitee as no-show…")
        access_token, err = await _get_access_token(config)
        if err:
            return _error_result(err)

        resp = await make_calendly_request(
            access_token,
            "POST",
            "/invitee_no_shows",
            json={"invitee": invitee_uri},
        )

        no_show_uri = resp.get("resource", {}).get("uri", "")
        msg = "Invitee marked as no-show."
        return msg, {
            "name": "Calendly",
            "tool": "Mark Invitee as No Show",
            "data": {
                "success": True,
                "message": msg,
                "no_show_uri": no_show_uri,
                "invitee_uri": invitee_uri,
            },
        }
    except Exception as e:
        logger.error("Error marking invitee as no-show: %s", e)
        return _error_result(f"Failed to mark no-show: {e}")


# ---------------------------------------------------------------------------
# 5. Cancel Scheduled Event (with reason)
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def cancel_scheduled_event_calendly(
    event_uuid: str,
    reason: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Cancel a scheduled Calendly event with a specific reason.

    The agent should obtain the event UUID from a previous find_event_calendly
    call or from context. Generate a reasonable cancellation reason from the
    user's message. Do NOT ask the user for a UUID.

    Args:
        event_uuid: The UUID of the scheduled event to cancel.
        reason: The reason for cancellation (infer from user's message).
        config: Runtime configuration.
    """
    try:
        emit_update("Cancelling scheduled Calendly event…")
        access_token, err = await _get_access_token(config)
        if err:
            return _error_result(err)

        await make_calendly_request(
            access_token,
            "POST",
            f"/scheduled_events/{event_uuid}/cancellation",
            json={"reason": reason},
        )

        msg = f"Event {event_uuid} cancelled. Reason: {reason}"
        return msg, {
            "name": "Calendly",
            "tool": "Cancel Scheduled Event",
            "data": {
                "success": True,
                "message": msg,
                "event_uuid": event_uuid,
                "reason": reason,
            },
        }
    except Exception as e:
        logger.error("Error cancelling scheduled event: %s", e)
        return _error_result(f"Failed to cancel scheduled event: {e}")


# ---------------------------------------------------------------------------
# 6. Find Event
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def find_event_calendly(
    event_uuid: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Find a Calendly event by its UUID and return full details including invitees.

    Args:
        event_uuid: The UUID of the event to look up.
        config: Runtime configuration.
    """
    try:
        emit_update("Looking up Calendly event…")
        access_token, err = await _get_access_token(config)
        if err:
            return _error_result(err)

        resp = await make_calendly_request(
            access_token, "GET", f"/scheduled_events/{event_uuid}"
        )
        event_data = resp.get("resource", resp)
        event = parse_event(event_data)

        invitees_resp = await make_calendly_request(
            access_token,
            "GET",
            f"/scheduled_events/{event_uuid}/invitees",
        )
        invitees_raw = invitees_resp.get("collection", [])
        invitees = [parse_invitee(i) for i in invitees_raw]

        invitee_names = [inv.name or inv.email or "Unknown" for inv in invitees]
        msg = (
            f"Event: {event.name} | Status: {event.status} | "
            f"{event.start_time} – {event.end_time} | "
            f"Invitees: {', '.join(invitee_names) if invitee_names else 'None'}"
        )
        return msg, {
            "name": "Calendly",
            "tool": "Find Event",
            "data": {
                "success": True,
                "message": msg,
                "event": event.model_dump(),
                "invitees": [inv.model_dump() for inv in invitees],
            },
        }
    except Exception as e:
        logger.error("Error finding Calendly event: %s", e)
        return _error_result(f"Failed to find event: {e}")


# ---------------------------------------------------------------------------
# 7. Find Meeting Recap
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def find_meeting_recap_calendly(
    event_uuid: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Find a meeting recap for a Calendly event. Requires a paid Calendly plan.

    Args:
        event_uuid: The UUID of the event whose recap to retrieve.
        config: Runtime configuration.
    """
    try:
        emit_update("Fetching Calendly meeting recap…")
        access_token, err = await _get_access_token(config)
        if err:
            return _error_result(err)

        resp = await make_calendly_request(
            access_token, "GET", f"/scheduled_events/{event_uuid}/meeting_recap"
        )
        resource = resp.get("resource", resp)
        summary = resource.get("summary", "No recap summary available.")

        msg = f"Meeting Recap for event {event_uuid}: {summary}"
        return msg, {
            "name": "Calendly",
            "tool": "Find Meeting Recap",
            "data": {
                "success": True,
                "message": msg,
                "recap": resource,
            },
        }
    except ValueError as e:
        if "404" in str(e) or "not found" in str(e).lower():
            return _error_result(
                "Meeting recap not found. This feature requires a paid Calendly plan."
            )
        return _error_result(f"Failed to find meeting recap: {e}")
    except Exception as e:
        logger.error("Error finding Calendly meeting recap: %s", e)
        return _error_result(f"Failed to find meeting recap: {e}")


# ---------------------------------------------------------------------------
# 8. Find Meeting Recap Transcript
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def find_meeting_recap_transcript_calendly(
    event_uuid: str,
    config: RunnableConfig,
) -> Tuple[str, Any]:
    """Find a meeting recap transcript for a Calendly event. Requires a paid Calendly plan.

    Args:
        event_uuid: The UUID of the event whose transcript to retrieve.
        config: Runtime configuration.
    """
    try:
        emit_update("Fetching Calendly meeting transcript…")
        access_token, err = await _get_access_token(config)
        if err:
            return _error_result(err)

        resp = await make_calendly_request(
            access_token,
            "GET",
            f"/scheduled_events/{event_uuid}/meeting_recap/transcript",
        )
        resource = resp.get("resource", resp)
        transcript = resource.get("transcript_text", "No transcript available.")

        msg = f"Meeting Transcript for event {event_uuid}:\n{transcript}"
        return msg, {
            "name": "Calendly",
            "tool": "Find Meeting Recap Transcript",
            "data": {
                "success": True,
                "message": msg,
                "transcript": resource,
            },
        }
    except ValueError as e:
        if "404" in str(e) or "not found" in str(e).lower():
            return _error_result(
                "Meeting transcript not found. This feature requires a paid Calendly plan."
            )
        return _error_result(f"Failed to find meeting transcript: {e}")
    except Exception as e:
        logger.error("Error finding Calendly meeting transcript: %s", e)
        return _error_result(f"Failed to find meeting transcript: {e}")


# ---------------------------------------------------------------------------
# 9. Find User
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def find_user_calendly(
    config: RunnableConfig,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> Tuple[str, Any]:
    """Search for a Calendly user by name or email within the organization.

    Args:
        config: Runtime configuration.
        email: Email address to search for.
        name: Name to search for.
    """
    try:
        if not email and not name:
            return _error_result("Provide at least one of email or name to search for a user.")

        emit_update("Searching for Calendly user…")
        access_token, err = await _get_access_token(config)
        if err:
            return _error_result(err)

        current_user = await make_calendly_request(access_token, "GET", "/users/me")
        org_uri = current_user["resource"].get("current_organization", "")

        if not org_uri:
            return _error_result("Could not determine Calendly organization.")

        params: Dict[str, Any] = {"organization": org_uri}
        if email:
            params["email"] = email

        resp = await make_calendly_request(
            access_token,
            "GET",
            "/organization_memberships",
            params=params,
        )
        memberships = resp.get("collection", [])

        matched_users = []
        for membership in memberships:
            user_data = membership.get("user", {})
            user = parse_user(user_data)

            if email and user.email and user.email.lower() == email.lower():
                matched_users.append(user)
            elif name and user.name and name.lower() in user.name.lower():
                matched_users.append(user)
            elif not name:
                matched_users.append(user)

        if not matched_users:
            return _error_result(
                f"No Calendly users found matching {'email: ' + email if email else ''}"
                f"{' and ' if email and name else ''}"
                f"{'name: ' + name if name else ''}"
            )

        user_summaries = [
            f"{u.name} ({u.email}) – {u.scheduling_url or 'No scheduling URL'}"
            for u in matched_users
        ]
        msg = f"Found {len(matched_users)} user(s):\n" + "\n".join(user_summaries)
        return msg, {
            "name": "Calendly",
            "tool": "Find User",
            "data": {
                "success": True,
                "message": msg,
                "users": [u.model_dump() for u in matched_users],
            },
        }
    except Exception as e:
        logger.error("Error finding Calendly user: %s", e)
        return _error_result(f"Failed to find user: {e}")


# ---------------------------------------------------------------------------
# 10. List Event Types
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def list_event_types_calendly(
    config: RunnableConfig,
    active_only: bool = True,
) -> Tuple[str, Any]:
    """List all available Calendly event types for the current user.

    Use this when the user explicitly asks to see their event types.
    Do NOT call this before book_meeting_calendly — booking auto-resolves
    the event type internally.

    Args:
        config: Runtime configuration.
        active_only: If True, only return active event types (default True).
    """
    try:
        emit_update("Listing Calendly event types…")
        access_token, err = await _get_access_token(config)
        if err:
            return _error_result(err)

        user_uri = await _get_current_user_uri(access_token)

        params: Dict[str, Any] = {"user": user_uri}
        if active_only:
            params["active"] = "true"

        resp = await make_calendly_request(
            access_token, "GET", "/event_types", params=params
        )
        event_types_raw = resp.get("collection", [])
        event_types = [parse_event_type(et) for et in event_types_raw]

        if not event_types:
            return _error_result("No event types found. Create one in your Calendly dashboard first.")

        summaries = [
            f"• {et.name} ({et.duration}min) – URI: {et.uri} | URL: {et.scheduling_url or 'N/A'}"
            for et in event_types
        ]
        msg = f"Found {len(event_types)} event type(s):\n" + "\n".join(summaries)
        return msg, {
            "name": "Calendly",
            "tool": "List Event Types",
            "data": {
                "success": True,
                "message": msg,
                "event_types": [et.model_dump() for et in event_types],
            },
        }
    except Exception as e:
        logger.error("Error listing Calendly event types: %s", e)
        return _error_result(f"Failed to list event types: {e}")


# ---------------------------------------------------------------------------
# Tool list
# ---------------------------------------------------------------------------

def get_calendly_tools() -> List[BaseTool]:
    """Return all Calendly tools."""
    return [
        cancel_event_calendly,
        create_one_off_meeting_calendly,
        book_meeting_calendly,
        mark_no_show_calendly,
        cancel_scheduled_event_calendly,
        find_event_calendly,
        find_meeting_recap_calendly,
        find_meeting_recap_transcript_calendly,
        find_user_calendly,
        list_event_types_calendly,
    ]
