from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "list_messages": {
        "id": "list_messages",
        "name": "List Gmail Messages",
        "description": "List recent Gmail messages with optional concurrency for faster retrieval.",
    },
    "search_messages": {
        "id": "search_messages",
        "name": "Search Gmail Messages",
        "description": "Search Gmail messages using query parameters and return matching results.",
    },
    "get_message": {
        "id": "get_message",
        "name": "Get Gmail Message",
        "description": "Retrieve a single Gmail message by id (including message details).",
    },
    "list_threads": {
        "id": "list_threads",
        "name": "List Gmail Threads",
        "description": "List recent Gmail conversation threads (optionally with faster fetching).",
    },
    "get_thread_detail": {
        "id": "get_thread_detail",
        "name": "Get Gmail Thread Detail",
        "description": "Retrieve detailed information for a Gmail thread, including all messages.",
    },
}

__all__ = ["TOOLS_DATA"]

