from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "get_event_details": {
        "id": "get_event_details",
        "name": "Get Calendar Event",
        "description": "Get detailed information for a specific Google Calendar event by event id.",
    },
    "list_events": {
        "id": "list_events",
        "name": "List Calendar Events",
        "description": "List upcoming Google Calendar events within a time range.",
    },
    "search_events": {
        "id": "search_events",
        "name": "Search Calendar Events",
        "description": "Search Google Calendar events for matching text within a time range.",
    },
    "filter_events_by_attendee": {
        "id": "filter_events_by_attendee",
        "name": "Filter Events by Attendee",
        "description": "Return events in a time range where the provided attendee email is present.",
    },
    "list_past_events": {
        "id": "list_past_events",
        "name": "List Past Calendar Events",
        "description": "List past Google Calendar events within a specified time range.",
    },
    "summarize_schedule": {
        "id": "summarize_schedule",
        "name": "Summarize Schedule",
        "description": "Summarize your calendar schedule for a day/week/month period.",
    },
    "analyze_meeting_patterns": {
        "id": "analyze_meeting_patterns",
        "name": "Analyze Meeting Patterns",
        "description": "Analyze meeting patterns and time allocation over a day/week/month period.",
    },
}

__all__ = ["TOOLS_DATA"]

