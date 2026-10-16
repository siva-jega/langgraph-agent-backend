from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "list_notion_pages": {
        "id": "list_notion_pages",
        "name": "List Notion Pages",
        "description": "List recent Notion pages, optionally filtered to specific database contexts.",
    },
    "search_notion_pages": {
        "id": "search_notion_pages",
        "name": "Search Notion Pages",
        "description": "Search Notion pages using the Notion API search syntax and return matches.",
    },
    "get_notion_page": {
        "id": "get_notion_page",
        "name": "Get Notion Page",
        "description": "Retrieve a single Notion page by page id.",
    },
    "append_notion_block_children": {
        "id": "append_notion_block_children",
        "name": "Append Notion Block Children",
        "description": "Append child blocks to an existing Notion parent block.",
    },
    "retrieve_notion_block": {
        "id": "retrieve_notion_block",
        "name": "Retrieve Notion Block",
        "description": "Get details for a specific Notion block by block id.",
    },
    "retrieve_notion_block_children": {
        "id": "retrieve_notion_block_children",
        "name": "Retrieve Notion Block Children",
        "description": "Retrieve child blocks within a Notion block.",
    },
    "delete_notion_block": {
        "id": "delete_notion_block",
        "name": "Delete Notion Block",
        "description": "Delete a Notion block by block id.",
    },
    "retrieve_notion_page": {
        "id": "retrieve_notion_page",
        "name": "Retrieve Notion Page",
        "description": "Retrieve a Notion page (alias of page retrieval) by page id.",
    },
    "update_notion_page_properties": {
        "id": "update_notion_page_properties",
        "name": "Update Notion Page Properties",
        "description": "Update properties/fields on a Notion page.",
    },
    "create_notion_database": {
        "id": "create_notion_database",
        "name": "Create Notion Database",
        "description": "Create a new Notion database in a specified parent context.",
    },
    "query_notion_database": {
        "id": "query_notion_database",
        "name": "Query Notion Database",
        "description": "Query a Notion database and return matching items.",
    },
    "retrieve_notion_database": {
        "id": "retrieve_notion_database",
        "name": "Retrieve Notion Database",
        "description": "Retrieve information about a Notion database by database id.",
    },
    "update_notion_database": {
        "id": "update_notion_database",
        "name": "Update Notion Database",
        "description": "Update database settings (e.g., properties) for an existing Notion database.",
    },
    "create_notion_database_item": {
        "id": "create_notion_database_item",
        "name": "Create Notion Database Item",
        "description": "Create a new item (row) in a Notion database.",
    },
    "search_notion": {
        "id": "search_notion",
        "name": "Search Notion",
        "description": "Search Notion pages or databases by title or query-like input.",
    },
    "list_notion_users": {
        "id": "list_notion_users",
        "name": "List Notion Users",
        "description": "List users in the Notion workspace (typically requires higher permissions).",
    },
    "retrieve_notion_user": {
        "id": "retrieve_notion_user",
        "name": "Retrieve Notion User",
        "description": "Retrieve a specific Notion user by user id.",
    },
    "retrieve_notion_bot_user": {
        "id": "retrieve_notion_bot_user",
        "name": "Retrieve Notion Bot User",
        "description": "Retrieve the bot user associated with the current Notion token.",
    },
    "create_notion_comment": {
        "id": "create_notion_comment",
        "name": "Create Notion Comment",
        "description": "Create a comment in Notion (requires appropriate comment capabilities).",
    },
    "retrieve_notion_comments": {
        "id": "retrieve_notion_comments",
        "name": "Retrieve Notion Comments",
        "description": "Retrieve unresolved comments from a Notion page or block (where supported).",
    },
}

__all__ = ["TOOLS_DATA"]

