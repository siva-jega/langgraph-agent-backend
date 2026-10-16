from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "cancel_event_calendly": {
        "id": "cancel_event_calendly",
        "name": "Cancel Event",
        "description": "Cancel a Calendly event by its event UUID.",
    },
    "create_one_off_meeting_calendly": {
        "id": "create_one_off_meeting_calendly",
        "name": "Create One-Off Meeting",
        "description": "Create a one-off Calendly meeting link that can be shared with an invitee.",
    },
    "book_meeting_calendly": {
        "id": "book_meeting_calendly",
        "name": "Book Meeting",
        "description": "Book a meeting on Calendly for an invitee (using available scheduling rules).",
    },
    "mark_no_show_calendly": {
        "id": "mark_no_show_calendly",
        "name": "Mark No-Show",
        "description": "Mark an invitee as a no-show for a Calendly event.",
    },
    "cancel_scheduled_event_calendly": {
        "id": "cancel_scheduled_event_calendly",
        "name": "Cancel Scheduled Event",
        "description": "Cancel a scheduled Calendly event with a specified reason.",
    },
    "find_event_calendly": {
        "id": "find_event_calendly",
        "name": "Find Event",
        "description": "Find a Calendly event by UUID and return full details.",
    },
    "find_meeting_recap_calendly": {
        "id": "find_meeting_recap_calendly",
        "name": "Find Meeting Recap",
        "description": "Find a meeting recap for a Calendly event (requires a paid plan).",
    },
    "find_meeting_recap_transcript_calendly": {
        "id": "find_meeting_recap_transcript_calendly",
        "name": "Find Meeting Transcript",
        "description": "Find a meeting recap transcript for a Calendly event (requires a paid plan).",
    },
    "find_user_calendly": {
        "id": "find_user_calendly",
        "name": "Find User",
        "description": "Search for a Calendly user by name or email within the organization.",
    },
    "list_event_types_calendly": {
        "id": "list_event_types_calendly",
        "name": "List Event Types",
        "description": "List all available Calendly event types for the current user.",
    },
}

__all__ = ["TOOLS_DATA"]

