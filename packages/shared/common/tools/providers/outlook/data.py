from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "list_outlook_messages": {
        "id": "list_outlook_messages",
        "name": "List Outlook Messages",
        "description": "List recent Outlook messages with optional folder filtering and optional body inclusion.",
    },
    "search_outlook_messages": {
        "id": "search_outlook_messages",
        "name": "Search Outlook Messages",
        "description": "Search Outlook messages using Microsoft Graph search syntax and return the matching messages.",
    },
    "get_outlook_message": {
        "id": "get_outlook_message",
        "name": "Get Outlook Message",
        "description": "Retrieve a single Outlook message by message id (including its details).",
    },
}

__all__ = ["TOOLS_DATA"]

