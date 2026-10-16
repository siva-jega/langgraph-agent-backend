from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "get_my_profile_calcom": {
        "id": "get_my_profile_calcom",
        "name": "Get Profile",
        "description": "Get the current Cal.com profile (username, display name, and timezone).",
    },
    "list_event_types_calcom": {
        "id": "list_event_types_calcom",
        "name": "List Event Types",
        "description": "List the available Cal.com event types for the connected user.",
    },
    "book_meeting_calcom": {
        "id": "book_meeting_calcom",
        "name": "Book Meeting",
        "description": "Book a Cal.com meeting with an attendee email and a specified date/time (and optional event type).",
    },
    "create_event_type_calcom": {
        "id": "create_event_type_calcom",
        "name": "Create Event Type",
        "description": "Create a Cal.com event type (title/slug/length) so meetings can be booked.",
    },
    "list_bookings_calcom": {
        "id": "list_bookings_calcom",
        "name": "List Bookings",
        "description": "List upcoming Cal.com bookings to find booking UIDs for cancellation/rescheduling.",
    },
    "cancel_booking_calcom": {
        "id": "cancel_booking_calcom",
        "name": "Cancel Booking",
        "description": "Cancel an existing Cal.com booking by its UID.",
    },
    "reschedule_booking_calcom": {
        "id": "reschedule_booking_calcom",
        "name": "Reschedule Booking",
        "description": "Reschedule a Cal.com booking to a new date and time by booking UID.",
    },
}

__all__ = ["TOOLS_DATA"]

