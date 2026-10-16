from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "add_memory": {
        "id": "add_memory",
        "name": "Add Memory",
        "description": "Store a new user-specific memory for future personalization.",
    },
    "get_memories": {
        "id": "get_memories",
        "name": "Get Memories",
        "description": "Retrieve saved memories for the current user within the configured context.",
    },
}

__all__ = ["TOOLS_DATA"]

