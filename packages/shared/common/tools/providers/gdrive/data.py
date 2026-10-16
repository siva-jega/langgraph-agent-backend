from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "list_drive_items": {
        "id": "list_drive_items",
        "name": "List Drive Items",
        "description": "List files and folders in Google Drive with support for queries and pagination.",
    },
    "search_drive_files": {
        "id": "search_drive_files",
        "name": "Search Drive Files",
        "description": "Search Google Drive for files/folders using Drive query language and return results.",
    },
    "get_drive_file_metadata": {
        "id": "get_drive_file_metadata",
        "name": "Get Drive File Metadata",
        "description": "Retrieve metadata for a specific Google Drive file or folder.",
    },
    "shared_files": {
        "id": "shared_files",
        "name": "Shared Files",
        "description": "List files shared with the current user (or a specified user) in Google Drive.",
    },
    "get_drive_file_content": {
        "id": "get_drive_file_content",
        "name": "Get Drive File Content",
        "description": "Fetch the contents of a specific Google Drive file.",
    },
}

__all__ = ["TOOLS_DATA"]

