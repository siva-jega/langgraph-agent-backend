"""Cal.com tools: book meeting, list event types, cancel booking, get profile."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from dateutil import parser as date_parser
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from common.integration import get_email
from common.tools.utils.writer import emit_update
from .utils import (
    get_calcom_credentials,
    make_calcom_request,
    CALCOM_API_VERSION_BOOKINGS,
    CALCOM_API_VERSION_BOOKINGS_READ,
    CALCOM_API_VERSION_EVENT_TYPES,
    CALCOM_API_VERSION_SLOTS,
)

logger = logging.getLogger(__name__)

CALCOM_API_VERSION = "2026-02-25"


def _parse_datetime(date_str: str, time_str: str, tz_name: str = "Asia/Kolkata") -> datetime:
    """Parse flexible date/time into timezone-aware datetime."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(tz)
    date_lower = date_str.strip().lower()
    if date_lower == "today":
        date_obj = now.date()
    elif date_lower == "tomorrow":
        date_obj = (now + timedelta(days=1)).date()
    else:
        parsed = date_parser.parse(date_str, dayfirst=False, fuzzy=True)
        date_obj = parsed.date()
    time_parsed = date_parser.parse(time_str, fuzzy=True)
    return datetime(
        date_obj.year, date_obj.month, date_obj.day,
        time_parsed.hour, time_parsed.minute, 0, tzinfo=tz,
    )


def _to_utc_iso(dt: datetime) -> str:
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _get_access_token_and_metadata(config: RunnableConfig) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """Returns (access_token, metadata_dict, error_msg). Metadata comes from the token service (DB config)."""
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    if not user_id:
        return None, None, "No user_id in configuration"
    all_apps = configurable.get("apps_config", []) or configurable.get("apps", [])
    calcom_app = next((a for a in all_apps if (a.get("slug") or "").lower() == "calcom"), None)
    connector_id = calcom_app.get("id") if calcom_app else None
    creds = await get_calcom_credentials(user_id, connector_id=connector_id)
    if not creds or not creds.token:
        return (
            None,
            None,
            "Cal.com is not connected or credentials could not be loaded. Connect Cal.com in the Apps section.",
        )
    metadata = creds.metadata or {}
    config_data = metadata.get("config") or {}
    if isinstance(config_data, str):
        import json
        try:
            config_data = json.loads(config_data)
        except Exception:
            config_data = {}
    return creds.token, config_data, None


async def _get_access_token(config: RunnableConfig) -> Tuple[Optional[str], Optional[str]]:
    token, _, err = await _get_access_token_and_metadata(config)
    return token, err


async def _get_me(access_token: str) -> Dict[str, Any]:
    resp = await make_calcom_request(
        access_token, "GET", "/v2/me",
        api_version=CALCOM_API_VERSION,
    )
    return (resp.get("data") or resp.get("user") or resp)


async def _send_confirmation_email(
    to: str,
    date_time_display: str,
    invitee_name: Optional[str] = None,
    meeting_title: Optional[str] = None,
    meeting_description: Optional[str] = None,
    reschedule_url: Optional[str] = None,
    cancel_url: Optional[str] = None,
) -> None:
    try:
        email_service = get_email()
        await email_service.send_meeting_confirmation(
            to=to,
            date_time_display=date_time_display,
            invitee_name=invitee_name,
            meeting_title=meeting_title,
            meeting_description=meeting_description,
            reschedule_url=reschedule_url,
            cancel_url=cancel_url,
        )
    except Exception as e:
        logger.warning("Could not send Cal.com confirmation email to %s: %s", to, e)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(response_format="content_and_artifact")
async def get_my_profile_calcom(config: RunnableConfig) -> Tuple[str, Any]:
    """Get the current Cal.com user profile (username, name, timezone). Use to know the account's username for booking."""
    try:
        access_token, err = await _get_access_token(config)
        if err:
            return err, {"name": "Cal.com", "tool": "Get Profile", "data": {"success": False, "message": err}}
        me = await _get_me(access_token)
        username = me.get("username", "")
        name = me.get("name") or me.get("email") or username
        msg = f"Profile: {name} (username: {username}), timezone: {me.get('timeZone', 'N/A')}"
        return msg, {"name": "Cal.com", "tool": "Get Profile", "data": {"success": True, "username": username, "profile": me}}
    except Exception as e:
        logger.error("Error getting Cal.com profile: %s", e)
        return str(e), {"name": "Cal.com", "tool": "Get Profile", "data": {"success": False, "message": str(e)}}


@tool(response_format="content_and_artifact")
async def list_event_types_calcom(config: RunnableConfig) -> Tuple[str, Any]:
    """List Cal.com event types for the connected user. Use to find event type id or slug for booking."""
    try:
        emit_update("Listing Cal.com event types…")
        access_token, config_data, err = await _get_access_token_and_metadata(config)
        if err:
            return err, {"name": "Cal.com", "tool": "List Event Types", "data": {"success": False, "message": err}}
        username = (config_data or {}).get("username")
        if not username:
            me = await _get_me(access_token)
            username = me.get("username")
        if not username:
            return "Could not determine username.", {"name": "Cal.com", "tool": "List Event Types", "data": {"success": False}}
        # GET /v2/event-types is public (needs ?username=). Third-party OAuth tokens get 403 if auth is sent.
        resp = await make_calcom_request(
            access_token, "GET", "/v2/event-types",
            api_version=CALCOM_API_VERSION_EVENT_TYPES,
            skip_auth=True,
            params={"username": username},
        )
        data = resp.get("data") or resp.get("eventTypes") or []
        if not data:
            return "No event types found.", {"name": "Cal.com", "tool": "List Event Types", "data": {"success": True, "eventTypes": []}}
        lines = [f"• {et.get('title', et.get('slug', 'N/A'))} (id: {et.get('id')}, slug: {et.get('slug')})" for et in data]
        msg = "Event types:\n" + "\n".join(lines)
        return msg, {"name": "Cal.com", "tool": "List Event Types", "data": {"success": True, "eventTypes": data}}
    except Exception as e:
        logger.error("Error listing Cal.com event types: %s", e)
        return str(e), {"name": "Cal.com", "tool": "List Event Types", "data": {"success": False, "message": str(e)}}


@tool(response_format="content_and_artifact")
async def book_meeting_calcom(
    invitee_email: str,
    date: str,
    time: str,
    config: RunnableConfig,
    invitee_name: Optional[str] = None,
    timezone_name: str = "Asia/Kolkata",
    event_type_id: Optional[int] = None,
    meeting_title: Optional[str] = None,
    meeting_description: Optional[str] = None,
    length_in_minutes: Optional[int] = None,
    guests: Optional[List[str]] = None,
    booking_fields_responses: Optional[Dict[str, str]] = None,
    metadata: Optional[Dict[str, str]] = None,
) -> Tuple[str, Any]:
    """Book a Cal.com meeting.

    CRITICAL RULES — you MUST follow ALL of these before calling this tool:
    1. Ask the user: "What date would you like to schedule?" — wait for their answer.
    2. Ask the user: "What time works for you?" — wait for their answer.
    3. Ask the user: "What is the attendee's email?" — wait for their answer (unless already provided).
    4. If there are multiple event types, call list_event_types_calcom first, show the options, and ask which one.

    NEVER guess or assume date or time. NEVER use the current time or "now". ALWAYS wait for the user to explicitly provide the date AND time before calling this tool.

    Parameters (all required unless marked optional):
    - invitee_email: attendee's email (MUST be provided by the user)
    - date: e.g. "2026-03-25" or "tomorrow" (MUST be provided by the user)
    - time: e.g. "3:00 PM" or "15:00" (MUST be provided by the user)
    - event_type_id (optional): id of the event type to book; if unknown, call list_event_types_calcom first
    - invitee_name, meeting_title, meeting_description, length_in_minutes, guests, timezone_name: all optional"""
    try:
        emit_update("Booking Cal.com meeting…")
        access_token, config_data, err = await _get_access_token_and_metadata(config)
        if err:
            return err, {"name": "Cal.com", "tool": "Book Meeting", "data": {"success": False, "message": err}}
        username = (config_data or {}).get("username")
        if not username:
            me = await _get_me(access_token)
            username = me.get("username")
        if not username:
            return "Could not determine Cal.com username.", {"name": "Cal.com", "tool": "Book Meeting", "data": {"success": False}}

        requested_dt = _parse_datetime(date, time, timezone_name)
        start_utc = _to_utc_iso(requested_dt)
        day_start = requested_dt.replace(hour=0, minute=0, second=0)
        day_end = day_start + timedelta(days=1)
        start_date = day_start.strftime("%Y-%m-%d")
        end_date = day_end.strftime("%Y-%m-%d")

        # GET /v2/event-types is public — skip auth to avoid 403 "insufficient_scope"
        event_types_resp = await make_calcom_request(
            access_token, "GET", "/v2/event-types",
            api_version=CALCOM_API_VERSION_EVENT_TYPES,
            skip_auth=True,
            params={"username": username},
        )
        event_types = event_types_resp.get("data") or event_types_resp.get("eventTypes") or []
        if not event_types:
            return "No event types found. Create one in Cal.com first.", {"name": "Cal.com", "tool": "Book Meeting", "data": {"success": False}}

        # Match event_type_id if provided; otherwise use the only one or ask user to pick
        if event_type_id:
            matched_et = next((et for et in event_types if et.get("id") == event_type_id), None)
            if not matched_et:
                lines = [f"• {et.get('title')} (id: {et.get('id')})" for et in event_types]
                return f"Event type with id {event_type_id} not found. Available:\n" + "\n".join(lines), \
                    {"name": "Cal.com", "tool": "Book Meeting", "data": {"success": False}}
            first_et = matched_et
        elif len(event_types) > 1:
            lines = [f"• {et.get('title')} (id: {et.get('id')}, slug: {et.get('slug')})" for et in event_types]
            return "Multiple event types found. Please ask the user which one to book and pass event_type_id:\n" + "\n".join(lines), \
                {"name": "Cal.com", "tool": "Book Meeting", "data": {"success": False, "eventTypes": event_types}}
        else:
            first_et = event_types[0]

        selected_event_type_id = first_et.get("id")
        event_type_slug = first_et.get("slug")

        # GET /v2/slots — docs show it as public; skip auth to avoid 403
        slots_resp = await make_calcom_request(
            access_token, "GET", "/v2/slots",
            api_version=CALCOM_API_VERSION_SLOTS,
            skip_auth=True,
            params={
                "eventTypeId": selected_event_type_id,
                "start": start_date,
                "end": end_date,
                "timeZone": timezone_name,
            },
        )
        slots_data = slots_resp.get("data") or slots_resp or {}
        if isinstance(slots_data, dict):
            all_slots = []
            for day_slots in slots_data.values():
                if isinstance(day_slots, list):
                    for s in day_slots:
                        if isinstance(s, str):
                            all_slots.append(s)
                        elif isinstance(s, dict) and s.get("start"):
                            all_slots.append(s["start"])
        else:
            all_slots = []

        if not all_slots:
            return f"No available slots on {date}. Try another date or check availability in Cal.com.", {"name": "Cal.com", "tool": "Book Meeting", "data": {"success": False}}

        requested_ts = requested_dt.timestamp()
        best_slot = min(
            all_slots,
            key=lambda s: abs(date_parser.isoparse(s).timestamp() - requested_ts) if s else 1e12,
        )
        try:
            slot_dt = date_parser.isoparse(best_slot)
            start_utc_iso = slot_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            start_utc_iso = start_utc

        body: Dict[str, Any] = {
            "start": start_utc_iso,
            "attendee": {
                "email": invitee_email,
                "name": invitee_name or invitee_email.split("@")[0],
                "timeZone": timezone_name,
            },
            "eventTypeId": selected_event_type_id,
        }
        if event_type_slug and not selected_event_type_id:
            body["eventTypeSlug"] = event_type_slug
            body["username"] = username
        # Only send lengthInMinutes if the event type has multiple length options
        has_multiple_lengths = bool(first_et.get("lengthInMinutesOptions"))
        if length_in_minutes is not None and has_multiple_lengths:
            body["lengthInMinutes"] = length_in_minutes
        if guests:
            body["guests"] = guests
        if booking_fields_responses:
            body["bookingFieldsResponses"] = booking_fields_responses
        if metadata:
            body["metadata"] = metadata

        # POST /v2/bookings — docs don't show auth header; skip auth to avoid 403
        create_resp = await make_calcom_request(
            access_token, "POST", "/v2/bookings",
            api_version=CALCOM_API_VERSION_BOOKINGS,
            skip_auth=True,
            json=body,
        )
        booking = create_resp.get("data") or create_resp
        booking_uid = booking.get("uid")
        start_time = booking.get("startTime") or best_slot
        try:
            display_dt = date_parser.isoparse(start_time)
            booked_display = display_dt.strftime("%B %d, %Y at %I:%M %p %Z")
        except Exception:
            booked_display = start_time

        reschedule_url = booking.get("rescheduleUrl") or ""
        cancel_url = booking.get("cancelUrl") or (f"https://api.cal.com/v2/bookings/{booking_uid}/cancel" if booking_uid else "")

        await _send_confirmation_email(
            to=invitee_email,
            date_time_display=booked_display,
            invitee_name=invitee_name,
            meeting_title=meeting_title,
            meeting_description=meeting_description,
            reschedule_url=reschedule_url or None,
            cancel_url=cancel_url or None,
        )

        msg = f"Meeting booked with {invitee_email} on {booked_display}."
        return msg, {
            "name": "Cal.com",
            "tool": "Book Meeting",
            "data": {
                "success": True,
                "message": msg,
                "bookingUid": booking_uid,
                "startTime": start_time,
                "rescheduleUrl": reschedule_url,
                "cancelUrl": cancel_url,
            },
        }
    except Exception as e:
        logger.error("Error booking Cal.com meeting: %s", e)
        return f"Failed to book meeting: {e}", {"name": "Cal.com", "tool": "Book Meeting", "data": {"success": False, "message": str(e)}}


@tool(response_format="content_and_artifact")
async def create_event_type_calcom(
    title: str,
    slug: str,
    length_in_minutes: int,
    config: RunnableConfig,
    description: Optional[str] = None,
    schedule_id: Optional[int] = None,
) -> Tuple[str, Any]:
    """Create a Cal.com event type. Required: title, slug (URL-friendly, unique per event type, e.g. my-30-min-meeting), length_in_minutes. Optional: description, schedule_id (defaults to user's default schedule). Use a unique slug to avoid 'User already has an event type with this slug'. See https://cal.com/docs/api-reference/v2/event-types/create-an-event-type."""
    try:
        emit_update("Creating Cal.com event type…")
        access_token, err = await _get_access_token(config)
        if err:
            return err, {"name": "Cal.com", "tool": "Create Event Type", "data": {"success": False, "message": err}}
        me = await _get_me(access_token)
        payload: Dict[str, Any] = {
            "title": title,
            "slug": slug,
            "lengthInMinutes": length_in_minutes,
            "lengthInMinutesOptions": [15, 30, 60],
            "bookingFields": [
                {
                    "type": "name",
                    "slug": "name",
                    "label": "Your name",
                    "placeholder": "Enter name",
                    "disableOnPrefill": True,
                }
            ],
            "disableGuests": True,
        }
        if description:
            payload["description"] = description
        if schedule_id is not None:
            payload["scheduleId"] = schedule_id
        elif me.get("defaultScheduleId") is not None:
            payload["scheduleId"] = me["defaultScheduleId"]

        resp = await make_calcom_request(
            access_token, "POST", "/v2/event-types",
            api_version=CALCOM_API_VERSION_EVENT_TYPES,
            json=payload,
        )
        data = resp.get("data") or resp
        event_type_id = data.get("id")
        event_slug = data.get("slug") or slug
        msg = f"Event type created: {title} (slug: {event_slug}, id: {event_type_id})."
        return msg, {"name": "Cal.com", "tool": "Create Event Type", "data": {"success": True, "eventType": data, "message": msg}}
    except Exception as e:
        logger.error("Error creating Cal.com event type: %s", e)
        return str(e), {"name": "Cal.com", "tool": "Create Event Type", "data": {"success": False, "message": str(e)}}


@tool(response_format="content_and_artifact")
async def list_bookings_calcom(config: RunnableConfig) -> Tuple[str, Any]:
    """List upcoming Cal.com bookings for the connected user. Use this FIRST when the user wants to cancel or reschedule — it shows booking UIDs needed for those actions."""
    try:
        emit_update("Fetching Cal.com bookings…")
        access_token, err = await _get_access_token(config)
        if err:
            return err, {"name": "Cal.com", "tool": "List Bookings", "data": {"success": False, "message": err}}
        resp = await make_calcom_request(
            access_token, "GET", "/v2/bookings",
            api_version=CALCOM_API_VERSION_BOOKINGS_READ,
            params={"take": 50, "status": "upcoming"},
        )
        bookings = resp.get("data") or resp.get("bookings") or []
        if not bookings:
            return "No upcoming bookings found.", {"name": "Cal.com", "tool": "List Bookings", "data": {"success": True, "bookings": []}}
        lines = []
        for b in bookings:
            title = b.get("title") or "Untitled"
            uid = b.get("uid") or ""
            start = b.get("startTime") or b.get("start") or ""
            attendees = b.get("attendees") or []
            attendee_names = ", ".join(a.get("name") or a.get("email", "") for a in attendees) if attendees else "N/A"
            try:
                dt = date_parser.isoparse(start)
                display = dt.strftime("%B %d, %Y at %I:%M %p")
            except Exception:
                display = start
            lines.append(f"• {title} — {display} (attendees: {attendee_names}, uid: {uid})")
        msg = "Upcoming bookings:\n" + "\n".join(lines)
        return msg, {"name": "Cal.com", "tool": "List Bookings", "data": {"success": True, "bookings": bookings}}
    except Exception as e:
        logger.error("Error listing Cal.com bookings: %s", e)
        return str(e), {"name": "Cal.com", "tool": "List Bookings", "data": {"success": False, "message": str(e)}}


@tool(response_format="content_and_artifact")
async def cancel_booking_calcom(booking_uid: str, config: RunnableConfig) -> Tuple[str, Any]:
    """Cancel a Cal.com booking by its UID.

    IMPORTANT: If the user doesn't provide a booking UID, call list_bookings_calcom FIRST to show them their
    upcoming meetings and let them pick which one to cancel. NEVER ask the user for a UID directly."""
    try:
        emit_update("Cancelling Cal.com booking…")
        access_token, err = await _get_access_token(config)
        if err:
            return err, {"name": "Cal.com", "tool": "Cancel Booking", "data": {"success": False, "message": err}}
        await make_calcom_request(
            access_token, "POST", f"/v2/bookings/{booking_uid}/cancel",
            api_version=CALCOM_API_VERSION_BOOKINGS,
            skip_auth=True,
            json={},
        )
        msg = f"Booking {booking_uid} cancelled."
        return msg, {"name": "Cal.com", "tool": "Cancel Booking", "data": {"success": True, "bookingUid": booking_uid}}
    except Exception as e:
        logger.error("Error cancelling Cal.com booking: %s", e)
        return str(e), {"name": "Cal.com", "tool": "Cancel Booking", "data": {"success": False, "message": str(e)}}


@tool(response_format="content_and_artifact")
async def reschedule_booking_calcom(
    booking_uid: str,
    new_date: str,
    new_time: str,
    config: RunnableConfig,
    timezone_name: str = "Asia/Kolkata",
    rescheduling_reason: Optional[str] = None,
) -> Tuple[str, Any]:
    """Reschedule a Cal.com booking to a new date and time.

    IMPORTANT: If the user doesn't provide a booking UID, call list_bookings_calcom FIRST to show them their
    upcoming meetings and let them pick which one to reschedule.
    You MUST ask the user for the new date AND new time before calling this tool. NEVER guess."""
    try:
        emit_update("Rescheduling Cal.com booking…")
        access_token, err = await _get_access_token(config)
        if err:
            return err, {"name": "Cal.com", "tool": "Reschedule Booking", "data": {"success": False, "message": err}}
        new_dt = _parse_datetime(new_date, new_time, timezone_name)
        new_start_utc = _to_utc_iso(new_dt)
        body: Dict[str, Any] = {"start": new_start_utc}
        if rescheduling_reason:
            body["reschedulingReason"] = rescheduling_reason
        resp = await make_calcom_request(
            access_token, "POST", f"/v2/bookings/{booking_uid}/reschedule",
            api_version=CALCOM_API_VERSION_BOOKINGS,
            skip_auth=True,
            json=body,
        )
        booking = resp.get("data") or resp
        new_uid = booking.get("uid") or booking_uid
        start_time = booking.get("startTime") or new_start_utc
        try:
            display_dt = date_parser.isoparse(start_time)
            display = display_dt.strftime("%B %d, %Y at %I:%M %p")
        except Exception:
            display = start_time
        msg = f"Booking rescheduled to {display} (uid: {new_uid})."
        return msg, {"name": "Cal.com", "tool": "Reschedule Booking", "data": {"success": True, "bookingUid": new_uid, "startTime": start_time}}
    except Exception as e:
        logger.error("Error rescheduling Cal.com booking: %s", e)
        return str(e), {"name": "Cal.com", "tool": "Reschedule Booking", "data": {"success": False, "message": str(e)}}


def get_calcom_tools() -> List[BaseTool]:
    return [
        get_my_profile_calcom,
        list_event_types_calcom,
        list_bookings_calcom,
        create_event_type_calcom,
        book_meeting_calcom,
        cancel_booking_calcom,
        reschedule_booking_calcom,
    ]
