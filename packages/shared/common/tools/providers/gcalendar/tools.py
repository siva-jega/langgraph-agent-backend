"""Google Calendar tools for managing events."""

from datetime import timedelta, datetime, timezone
from typing import List, Optional, Dict
from langchain_core.tools import tool, Tool
from langchain_core.runnables import RunnableConfig
import logging
from dateutil import parser as date_parser
from collections import Counter
from .models import CalendarEvent
from .utils import (
    get_gcalendar_credentials,
    build_gcalendar_service,
    execute_gcalendar_request,
    process_event,
    process_events,
)
from common.tools.utils.writer import emit_update

logger = logging.getLogger(__name__)


@tool
async def get_event_details(
    event_id: str,
    config: RunnableConfig = None,
) -> CalendarEvent:
    """
    Get detailed information about a specific Google Calendar event by ID.

    Args:
        event_id: ID of the event to get details for
        config: Configuration containing user_id.

    Returns:
        CalendarEvent: Detailed information about the event
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "calendar" in slug or "google" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_gcalendar_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Calendar credentials")

        calendar_service = build_gcalendar_service(credentials.token)
        emit_update(
            {
                "tool": "Google Calendar",
                "update": f"Getting event details for ID: {event_id}",
            },
            "gcalendar",
        )

        try:
            request = calendar_service.events().get(
                calendarId="primary", eventId=event_id
            )
            event = await execute_gcalendar_request(request)
            calendar_event = process_event(event)
            emit_update(
                {
                    "tool": "Google Calendar",
                    "update": f"Found event: {calendar_event.summary}",
                },
                "gcalendar",
            )
            return calendar_event
        except Exception as e:
            logger.error(f"Error getting calendar event details: {str(e)}")
            raise Exception(f"Error getting calendar event details: {str(e)}")
    except Exception as e:
        logger.error(f"Error getting event details: {str(e)}")
        raise


@tool
async def list_events(
    time_range: int = 7,
    max_results: int = 10,
    config: RunnableConfig = None,
) -> List[CalendarEvent]:
    """
    List upcoming Google Calendar events in a given time range.

    Args:
        time_range: Number of days to look ahead (must be between 1 and 30)
        max_results: Maximum number of events to return (must be between 1 and 2500)
        config: Configuration containing user_id.

    Returns:
        List[CalendarEvent]: List of upcoming Google Calendar events
    """
    try:
        if time_range < 1 or time_range > 30:
            raise ValueError("time_range must be between 1 and 30")
        if max_results < 1 or max_results > 2500:
            raise ValueError("max_results must be between 1 and 2500")

        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "calendar" in slug or "google" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_gcalendar_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Calendar credentials")

        calendar_service = build_gcalendar_service(credentials.token)
        emit_update(
            {
                "tool": "Google Calendar",
                "update": f"Listing events for {time_range} days, max_results: {max_results}",
            },
            "gcalendar",
        )

        now = datetime.now(timezone.utc)
        time_min = now - timedelta(days=1)
        time_max = now + timedelta(days=time_range + 1)
        time_min_str = time_min.isoformat()
        time_max_str = time_max.isoformat()

        try:
            request = calendar_service.events().list(
                calendarId="primary",
                orderBy="startTime",
                singleEvents=True,
                timeMin=time_min_str,
                timeMax=time_max_str,
                maxResults=max_results,
            )
            results = await execute_gcalendar_request(request)
            events = results.get("items", [])
            parsed_events = process_events(events)
            emit_update(
                {
                    "tool": "Google Calendar",
                    "update": f"Found {len(parsed_events)} events",
                },
                "gcalendar",
            )
            return parsed_events
        except Exception as e:
            logger.error(f"Error listing calendar events: {str(e)}")
            raise Exception(f"Error listing calendar events: {str(e)}")
    except Exception as e:
        logger.error(f"Error listing events: {str(e)}")
        raise


@tool
async def search_events(
    query: str,
    time_range: int = 7,
    max_results: int = 10,
    time_range_needed: bool = True,
    config: RunnableConfig = None,
) -> List[CalendarEvent]:
    """
    Search for Google Calendar events matching a query in a given time range.

    Args:
        query: Search query
        time_range: Number of days to look ahead
        max_results: Maximum number of results
        time_range_needed: Whether to use the time range or not
        config: Configuration containing user_id.

    Returns:
        List[CalendarEvent]: List of Google Calendar events matching the query
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "calendar" in slug or "google" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_gcalendar_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Calendar credentials")

        calendar_service = build_gcalendar_service(credentials.token)
        emit_update(
            {
                "tool": "Google Calendar",
                "update": f"Searching events with query: {query}, time_range: {time_range}",
            },
            "gcalendar",
        )

        now = datetime.now(timezone.utc)
        time_min = now - timedelta(days=1)
        time_max = now + timedelta(days=time_range)
        time_min_str = time_min.isoformat() if time_range_needed else None
        time_max_str = time_max.isoformat() if time_range_needed else None

        try:
            request = calendar_service.events().list(
                calendarId="primary",
                timeMin=time_min_str,
                timeMax=time_max_str,
                maxResults=max_results,
                q=query,
                singleEvents=True,
                orderBy="startTime",
            )
            results = await execute_gcalendar_request(request)
            events = results.get("items", [])
            parsed_events = process_events(events)
            emit_update(
                {
                    "tool": "Google Calendar",
                    "update": f"Found {len(parsed_events)} matching events",
                },
                "gcalendar",
            )
            return parsed_events
        except Exception as e:
            logger.error(f"Error searching calendar events: {str(e)}")
            raise Exception(f"Error searching calendar events: {str(e)}")
    except Exception as e:
        logger.error(f"Error searching events: {str(e)}")
        raise


@tool
async def filter_events_by_attendee(
    attendee_email: str,
    time_range: int = 7,
    max_results: int = 20,
    config: RunnableConfig = None,
) -> List[CalendarEvent]:
    """
    Filter events by attendee email in a given time range.

    Args:
        attendee_email: Email of the attendee to filter by
        time_range: Number of days to look ahead
        max_results: Maximum number of events to return
        config: Configuration containing user_id.

    Returns:
        List[CalendarEvent]: List of events with the specified attendee
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "calendar" in slug or "google" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_gcalendar_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Calendar credentials")

        calendar_service = build_gcalendar_service(credentials.token)
        emit_update(
            {
                "tool": "Google Calendar",
                "update": f"Filtering events by attendee: {attendee_email}, time_range: {time_range}",
            },
            "gcalendar",
        )

        now = datetime.now(timezone.utc)
        time_min = now - timedelta(days=1)
        time_max = now + timedelta(days=time_range)
        time_min_str = time_min.isoformat()
        time_max_str = time_max.isoformat()

        try:
            request = calendar_service.events().list(
                calendarId="primary",
                timeMin=time_min_str,
                timeMax=time_max_str,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            results = await execute_gcalendar_request(request)
            events = results.get("items", [])
            filtered = [
                e
                for e in events
                if any(a.get("email") == attendee_email for a in e.get("attendees", []))
            ]
            parsed_events = process_events(filtered)
            emit_update(
                {
                    "tool": "Google Calendar",
                    "update": f"Found {len(parsed_events)} events with attendee",
                },
                "gcalendar",
            )
            return parsed_events
        except Exception as e:
            logger.error(f"Error filtering events by attendee: {str(e)}")
            raise Exception(f"Error filtering events by attendee: {str(e)}")
    except Exception as e:
        logger.error(f"Error filtering events by attendee: {str(e)}")
        raise


@tool
async def list_past_events(
    time_range: int = 7,
    max_results: int = 20,
    config: RunnableConfig = None,
) -> List[CalendarEvent]:
    """
    List past Google Calendar events in a given time range.

    Args:
        time_range: Number of days to look back
        max_results: Maximum number of events to return
        config: Configuration containing user_id.

    Returns:
        List[CalendarEvent]: List of past Google Calendar events
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "calendar" in slug or "google" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_gcalendar_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Calendar credentials")

        calendar_service = build_gcalendar_service(credentials.token)
        emit_update(
            {
                "tool": "Google Calendar",
                "update": f"Listing past events for {time_range} days, max_results: {max_results}",
            },
            "gcalendar",
        )

        now = datetime.now(timezone.utc)
        time_min = now - timedelta(days=time_range)
        time_max = now
        time_min_str = time_min.isoformat()
        time_max_str = time_max.isoformat()

        try:
            request = calendar_service.events().list(
                calendarId="primary",
                orderBy="startTime",
                singleEvents=True,
                timeMin=time_min_str,
                timeMax=time_max_str,
                maxResults=max_results,
            )
            results = await execute_gcalendar_request(request)
            events = results.get("items", [])
            parsed_events = process_events(events)
            emit_update(
                {
                    "tool": "Google Calendar",
                    "update": f"Found {len(parsed_events)} past events",
                },
                "gcalendar",
            )
            return parsed_events
        except Exception as e:
            logger.error(f"Error listing past calendar events: {str(e)}")
            raise Exception(f"Error listing past calendar events: {str(e)}")
    except Exception as e:
        logger.error(f"Error listing past events: {str(e)}")
        raise


@tool
async def summarize_schedule(
    period: str = "week",
    config: RunnableConfig = None,
) -> Dict:
    """
    Summarize your schedule for a given period (day, week, month).

    Args:
        period: Period to summarize (day, week, month)
        config: Configuration containing user_id.

    Returns:
        dict: Summary with number of events and total duration (in hours)
    """
    try:
        if period not in ["day", "week", "month"]:
            raise ValueError("Invalid period. Use 'day', 'week', or 'month'.")

        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_gcalendar_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Calendar credentials")

        calendar_service = build_gcalendar_service(credentials.token)
        emit_update(
            {"tool": "Google Calendar", "update": f"Summarizing schedule for {period}"},
            "gcalendar",
        )

        now = datetime.now(timezone.utc)
        if period == "day":
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=1)
        elif period == "week":
            time_min = now - timedelta(days=now.weekday())
            time_min = time_min.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=7)
        elif period == "month":
            time_min = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if time_min.month == 12:
                time_max = time_min.replace(year=time_min.year + 1, month=1)
            else:
                time_max = time_min.replace(month=time_min.month + 1)

        time_min_str = time_min.isoformat()
        time_max_str = time_max.isoformat()

        try:
            request = calendar_service.events().list(
                calendarId="primary",
                orderBy="startTime",
                singleEvents=True,
                timeMin=time_min_str,
                timeMax=time_max_str,
                maxResults=100,
            )
            results = await execute_gcalendar_request(request)
            events = results.get("items", [])
            total_events = len(events)
            total_duration = 0.0
            for e in events:
                start = e.get("start", {}).get("dateTime")
                end = e.get("end", {}).get("dateTime")
                if start and end:
                    start_dt = date_parser.isoparse(start)
                    end_dt = date_parser.isoparse(end)
                    total_duration += (end_dt - start_dt).total_seconds() / 3600.0
            return {
                "period": period,
                "total_events": total_events,
                "total_duration_hours": round(total_duration, 2),
            }
        except Exception as e:
            logger.error(f"Error summarizing schedule: {str(e)}")
            raise Exception(f"Error summarizing schedule: {str(e)}")
    except Exception as e:
        logger.error(f"Error summarizing schedule: {str(e)}")
        raise


@tool
async def analyze_meeting_patterns(
    period: str = "month",
    config: RunnableConfig = None,
) -> Dict:
    """
    Analyze your meeting patterns and time allocation for a given period.

    Args:
        period: Period to analyze (day, week, month)
        config: Configuration containing user_id.

    Returns:
        dict: Analysis including meeting count, total time, frequent attendees, and common meeting times
    """
    try:
        if period not in ["day", "week", "month"]:
            raise ValueError("Invalid period. Use 'day', 'week', or 'month'.")

        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_gcalendar_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Calendar credentials")

        calendar_service = build_gcalendar_service(credentials.token)
        emit_update(
            {
                "tool": "Google Calendar",
                "update": f"Analyzing meeting patterns for {period}",
            },
            "gcalendar",
        )

        now = datetime.now(timezone.utc)
        if period == "day":
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=1)
        elif period == "week":
            time_min = now - timedelta(days=now.weekday())
            time_min = time_min.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=7)
        elif period == "month":
            time_min = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if time_min.month == 12:
                time_max = time_min.replace(year=time_min.year + 1, month=1)
            else:
                time_max = time_min.replace(month=time_min.month + 1)

        time_min_str = time_min.isoformat()
        time_max_str = time_max.isoformat()

        try:
            request = calendar_service.events().list(
                calendarId="primary",
                orderBy="startTime",
                singleEvents=True,
                timeMin=time_min_str,
                timeMax=time_max_str,
                maxResults=100,
            )
            results = await execute_gcalendar_request(request)
            events = results.get("items", [])
            total_events = len(events)
            total_duration = 0.0
            attendee_counter = Counter()
            hour_counter = Counter()
            for e in events:
                start = e.get("start", {}).get("dateTime")
                end = e.get("end", {}).get("dateTime")
                if start and end:
                    start_dt = date_parser.isoparse(start)
                    end_dt = date_parser.isoparse(end)
                    total_duration += (end_dt - start_dt).total_seconds() / 3600.0
                    hour_counter[start_dt.hour] += 1
                for a in e.get("attendees", []):
                    email = a.get("email")
                    if email:
                        attendee_counter[email] += 1
            most_frequent_attendees = attendee_counter.most_common(5)
            most_common_hours = hour_counter.most_common(3)
            return {
                "period": period,
                "total_events": total_events,
                "total_duration_hours": round(total_duration, 2),
                "most_frequent_attendees": most_frequent_attendees,
                "most_common_meeting_hours": most_common_hours,
            }
        except Exception as e:
            logger.error(f"Error analyzing meeting patterns: {str(e)}")
            raise Exception(f"Error analyzing meeting patterns: {str(e)}")
    except Exception as e:
        logger.error(f"Error analyzing meeting patterns: {str(e)}")
        raise


def get_gcalendar_tools() -> List[Tool]:
    """Get all Google Calendar tools."""
    return [
        get_event_details,
        list_events,
        search_events,
        filter_events_by_attendee,
        list_past_events,
        summarize_schedule,
        analyze_meeting_patterns,
    ]
