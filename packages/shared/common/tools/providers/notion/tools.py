"""Notion tools for managing pages, blocks, databases, and comments."""

from typing import List, Optional, Dict, Any
from langchain_core.tools import tool, Tool
from langchain_core.runnables import RunnableConfig
import logging
from .models import NotionPage, NotionBlock, NotionDatabase, NotionUser, NotionComment
from .utils import (
    get_notion_credentials,
    execute_notion_request,
    parse_page,
    parse_block,
    parse_database,
    parse_user,
    parse_comment,
)
from common.tools.utils.writer import emit_update

logger = logging.getLogger(__name__)


@tool
async def list_notion_pages(
    database_id: Optional[str] = None,
    max_results: int = 10,
    config: RunnableConfig = None,
) -> List[NotionPage]:
    """
    List recent Notion pages with optional database filtering.

    Args:
        database_id: Optional database ID to filter pages
        max_results: Maximum number of pages to return
        config: Configuration containing user_id.

    Returns:
        List of Notion pages
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
            if "notion" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_notion_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        emit_update(
            {
                "tool": "Notion",
                "update": f"Listing pages with database_id: {database_id}, max_results: {max_results}",
            },
            "notion",
        )

        if database_id:
            url = f"https://api.notion.com/v1/databases/{database_id}/query"
            data = {"page_size": max_results}
            response = await execute_notion_request(
                "POST", url, credentials.token, json=data
            )
            pages = response.get("results", [])
        else:
            url = "https://api.notion.com/v1/search"
            data = {
                "page_size": max_results,
                "filter": {"property": "object", "value": "page"},
            }
            response = await execute_notion_request(
                "POST", url, credentials.token, json=data
            )
            pages = response.get("results", [])

        parsed_pages = [parse_page(page) for page in pages]
        emit_update(
            {"tool": "Notion", "update": f"Found {len(parsed_pages)} pages"},
            "notion",
        )
        return parsed_pages
    except Exception as e:
        logger.error(f"Error listing pages: {str(e)}")
        raise Exception(f"Error listing pages: {str(e)}")


@tool
async def search_notion_pages(
    query: str,
    max_results: int = 10,
    config: RunnableConfig = None,
) -> List[NotionPage]:
    """
    Search Notion pages using Notion API search syntax.

    Args:
        query: Search query
        max_results: Maximum number of results
        config: Configuration containing user_id.

    Returns:
        List of matching Notion pages
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
            if "notion" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_notion_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        emit_update(
            {
                "tool": "Notion",
                "update": f"Searching pages with query: {query}, max_results: {max_results}",
            },
            "notion",
        )

        url = "https://api.notion.com/v1/search"
        data = {
            "query": query,
            "page_size": max_results,
            "filter": {"property": "object", "value": "page"},
        }
        response = await execute_notion_request(
            "POST", url, credentials.token, json=data
        )
        pages = response.get("results", [])
        parsed_pages = [parse_page(page) for page in pages]
        emit_update(
            {"tool": "Notion", "update": f"Found {len(parsed_pages)} matching pages"},
            "notion",
        )
        return parsed_pages
    except Exception as e:
        logger.error(f"Error searching pages: {str(e)}")
        raise Exception(f"Error searching pages: {str(e)}")


@tool
async def get_notion_page(
    page_id: str,
    config: RunnableConfig = None,
) -> Optional[NotionPage]:
    """
    Get a single Notion page by ID.

    Args:
        page_id: Notion page ID
        config: Configuration containing user_id.

    Returns:
        Notion page if found
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
            if "notion" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_notion_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        emit_update(
            {"tool": "Notion", "update": f"Getting page with id: {page_id}"},
            "notion",
        )

        url = f"https://api.notion.com/v1/pages/{page_id}"
        response = await execute_notion_request("GET", url, credentials.token)
        page = parse_page(response)
        emit_update(
            {"tool": "Notion", "update": f"Found page: {page.title}"},
            "notion",
        )
        return page
    except Exception as e:
        logger.error(f"Error getting page {page_id}: {str(e)}")
        raise Exception(f"Error getting page {page_id}: {str(e)}")


@tool
async def append_notion_block_children(
    block_id: str,
    children: List[Dict[str, Any]],
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Append child blocks to a parent block.

    Args:
        block_id: Parent block ID
        children: List of child block objects
        config: Configuration containing user_id.

    Returns:
        Response with created blocks
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
            if "notion" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_notion_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = f"https://api.notion.com/v1/blocks/{block_id}/children"
        data = {"children": children}
        response = await execute_notion_request(
            "PATCH", url, credentials.token, json=data
        )
        return response
    except Exception as e:
        logger.error(f"Error appending blocks to {block_id}: {str(e)}")
        raise Exception(f"Error appending blocks to {block_id}: {str(e)}")


@tool
async def retrieve_notion_block(
    block_id: str,
    config: RunnableConfig = None,
) -> NotionBlock:
    """
    Retrieve information about a specific block.

    Args:
        block_id: Block ID
        config: Configuration containing user_id.

    Returns:
        Notion block information
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
            if "notion" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_notion_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = f"https://api.notion.com/v1/blocks/{block_id}"
        response = await execute_notion_request("GET", url, credentials.token)
        return parse_block(response)
    except Exception as e:
        logger.error(f"Error retrieving block {block_id}: {str(e)}")
        raise Exception(f"Error retrieving block {block_id}: {str(e)}")


@tool
async def retrieve_notion_block_children(
    block_id: str,
    start_cursor: Optional[str] = None,
    page_size: int = 100,
    config: RunnableConfig = None,
) -> List[NotionBlock]:
    """
    Retrieve the children of a specific block.

    Args:
        block_id: Parent block ID
        start_cursor: Pagination cursor
        page_size: Number of results per page
        config: Configuration containing user_id.

    Returns:
        List of child blocks
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
            if "notion" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_notion_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = f"https://api.notion.com/v1/blocks/{block_id}/children"
        params = {"page_size": min(page_size, 100)}
        if start_cursor:
            params["start_cursor"] = start_cursor
        response = await execute_notion_request(
            "GET", url, credentials.token, params=params
        )
        blocks = response.get("results", [])
        return [parse_block(block) for block in blocks]
    except Exception as e:
        logger.error(f"Error retrieving children of block {block_id}: {str(e)}")
        raise Exception(f"Error retrieving children of block {block_id}: {str(e)}")


@tool
async def delete_notion_block(
    block_id: str,
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Delete a specific block.

    Args:
        block_id: Block ID to delete
        config: Configuration containing user_id.

    Returns:
        Deletion response
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
            if "notion" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_notion_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = f"https://api.notion.com/v1/blocks/{block_id}"
        response = await execute_notion_request("DELETE", url, credentials.token)
        return response
    except Exception as e:
        logger.error(f"Error deleting block {block_id}: {str(e)}")
        raise Exception(f"Error deleting block {block_id}: {str(e)}")


@tool
async def retrieve_notion_page(
    page_id: str,
    config: RunnableConfig = None,
) -> NotionPage:
    """
    Retrieve information about a specific page.

    Args:
        page_id: Page ID
        config: Configuration containing user_id.

    Returns:
        Notion page information
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = f"https://api.notion.com/v1/pages/{page_id}"
        response = await execute_notion_request("GET", url, credentials.token)
        return parse_page(response)
    except Exception as e:
        logger.error(f"Error retrieving page {page_id}: {str(e)}")
        raise Exception(f"Error retrieving page {page_id}: {str(e)}")


@tool
async def update_notion_page_properties(
    page_id: str,
    properties: Dict[str, Any],
    config: RunnableConfig = None,
) -> NotionPage:
    """
    Update properties of a page.

    Args:
        page_id: Page ID
        properties: Dictionary of properties to update
        config: Configuration containing user_id.

    Returns:
        Updated Notion page
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = f"https://api.notion.com/v1/pages/{page_id}"
        data = {"properties": properties}
        response = await execute_notion_request(
            "PATCH", url, credentials.token, json=data
        )
        return parse_page(response)
    except Exception as e:
        logger.error(f"Error updating page {page_id}: {str(e)}")
        raise Exception(f"Error updating page {page_id}: {str(e)}")


@tool
async def create_notion_database(
    parent: Dict[str, Any],
    properties: Dict[str, Any],
    title: Optional[List[Dict[str, Any]]] = None,
    config: RunnableConfig = None,
) -> NotionDatabase:
    """
    Create a new database.

    Args:
        parent: Parent object (page_id or workspace)
        properties: Dictionary of database properties
        title: Optional title for the database
        config: Configuration containing user_id.

    Returns:
        Created Notion database
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = "https://api.notion.com/v1/databases"
        data = {"parent": parent, "properties": properties}
        if title:
            data["title"] = title
        response = await execute_notion_request(
            "POST", url, credentials.token, json=data
        )
        return parse_database(response)
    except Exception as e:
        logger.error(f"Error creating database: {str(e)}")
        raise Exception(f"Error creating database: {str(e)}")


@tool
async def query_notion_database(
    database_id: str,
    filter: Optional[Dict[str, Any]] = None,
    sorts: Optional[List[Dict[str, Any]]] = None,
    start_cursor: Optional[str] = None,
    page_size: int = 100,
    config: RunnableConfig = None,
) -> List[NotionPage]:
    """
    Query a database.

    Args:
        database_id: Database ID
        filter: Optional filter object
        sorts: Optional list of sort objects
        start_cursor: Pagination cursor
        page_size: Number of results per page
        config: Configuration containing user_id.

    Returns:
        List of pages from the database
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = f"https://api.notion.com/v1/databases/{database_id}/query"
        data = {"page_size": min(page_size, 100)}
        if filter:
            data["filter"] = filter
        if sorts:
            data["sorts"] = sorts
        if start_cursor:
            data["start_cursor"] = start_cursor
        response = await execute_notion_request(
            "POST", url, credentials.token, json=data
        )
        pages = response.get("results", [])
        return [parse_page(page) for page in pages]
    except Exception as e:
        logger.error(f"Error querying database {database_id}: {str(e)}")
        raise Exception(f"Error querying database {database_id}: {str(e)}")


@tool
async def retrieve_notion_database(
    database_id: str,
    config: RunnableConfig = None,
) -> NotionDatabase:
    """
    Retrieve information about a specific database.

    Args:
        database_id: Database ID
        config: Configuration containing user_id.

    Returns:
        Notion database information
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = f"https://api.notion.com/v1/databases/{database_id}"
        response = await execute_notion_request("GET", url, credentials.token)
        return parse_database(response)
    except Exception as e:
        logger.error(f"Error retrieving database {database_id}: {str(e)}")
        raise Exception(f"Error retrieving database {database_id}: {str(e)}")


@tool
async def update_notion_database(
    database_id: str,
    title: Optional[List[Dict[str, Any]]] = None,
    description: Optional[List[Dict[str, Any]]] = None,
    properties: Optional[Dict[str, Any]] = None,
    config: RunnableConfig = None,
) -> NotionDatabase:
    """
    Update information about a database.

    Args:
        database_id: Database ID
        title: Optional title update
        description: Optional description update
        properties: Optional properties update
        config: Configuration containing user_id.

    Returns:
        Updated Notion database
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = f"https://api.notion.com/v1/databases/{database_id}"
        data = {}
        if title:
            data["title"] = title
        if description:
            data["description"] = description
        if properties:
            data["properties"] = properties
        response = await execute_notion_request(
            "PATCH", url, credentials.token, json=data
        )
        return parse_database(response)
    except Exception as e:
        logger.error(f"Error updating database {database_id}: {str(e)}")
        raise Exception(f"Error updating database {database_id}: {str(e)}")


@tool
async def create_notion_database_item(
    database_id: str,
    properties: Dict[str, Any],
    config: RunnableConfig = None,
) -> NotionPage:
    """
    Create a new item in a Notion database.

    Args:
        database_id: Database ID
        properties: Dictionary of properties for the new item
        config: Configuration containing user_id.

    Returns:
        Created Notion page (database item)
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = "https://api.notion.com/v1/pages"
        data = {"parent": {"database_id": database_id}, "properties": properties}
        response = await execute_notion_request(
            "POST", url, credentials.token, json=data
        )
        return parse_page(response)
    except Exception as e:
        logger.error(f"Error creating database item: {str(e)}")
        raise Exception(f"Error creating database item: {str(e)}")


@tool
async def search_notion(
    query: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    sort: Optional[Dict[str, Any]] = None,
    start_cursor: Optional[str] = None,
    page_size: int = 100,
    config: RunnableConfig = None,
) -> List[Dict[str, Any]]:
    """
    Search pages or databases by title.

    Args:
        query: Optional search query
        filter: Optional filter object
        sort: Optional sort object
        start_cursor: Pagination cursor
        page_size: Number of results per page
        config: Configuration containing user_id.

    Returns:
        List of search results
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = "https://api.notion.com/v1/search"
        data = {"page_size": min(page_size, 100)}
        if query:
            data["query"] = query
        if filter:
            data["filter"] = filter
        if sort:
            data["sort"] = sort
        if start_cursor:
            data["start_cursor"] = start_cursor
        response = await execute_notion_request(
            "POST", url, credentials.token, json=data
        )
        return response.get("results", [])
    except Exception as e:
        logger.error(f"Error searching: {str(e)}")
        raise Exception(f"Error searching: {str(e)}")


@tool
async def list_notion_users(
    start_cursor: Optional[str] = None,
    page_size: int = 100,
    config: RunnableConfig = None,
) -> List[NotionUser]:
    """
    List all users in the Notion workspace. Note: Requires Enterprise plan and Organization API key.

    Args:
        start_cursor: Pagination cursor
        page_size: Number of results per page
        config: Configuration containing user_id.

    Returns:
        List of Notion users
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = "https://api.notion.com/v1/users"
        params = {"page_size": min(page_size, 100)}
        if start_cursor:
            params["start_cursor"] = start_cursor
        response = await execute_notion_request(
            "GET", url, credentials.token, params=params
        )
        users = response.get("results", [])
        return [parse_user(user) for user in users]
    except Exception as e:
        logger.error(f"Error listing users: {str(e)}")
        raise Exception(f"Error listing users: {str(e)}")


@tool
async def retrieve_notion_user(
    user_id: str,
    config: RunnableConfig = None,
) -> NotionUser:
    """
    Retrieve a specific user by user_id. Note: Requires Enterprise plan and Organization API key.

    Args:
        user_id: User ID
        config: Configuration containing user_id.

    Returns:
        Notion user information
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        current_user_id = configurable.get("user_id")
        if not current_user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(current_user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = f"https://api.notion.com/v1/users/{user_id}"
        response = await execute_notion_request("GET", url, credentials.token)
        return parse_user(response)
    except Exception as e:
        logger.error(f"Error retrieving user {user_id}: {str(e)}")
        raise Exception(f"Error retrieving user {user_id}: {str(e)}")


@tool
async def retrieve_notion_bot_user(
    config: RunnableConfig = None,
) -> NotionUser:
    """
    Retrieve the bot user associated with the current token.

    Args:
        config: Configuration containing user_id.

    Returns:
        Bot user information
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = "https://api.notion.com/v1/users/me"
        response = await execute_notion_request("GET", url, credentials.token)
        return parse_user(response)
    except Exception as e:
        logger.error(f"Error retrieving bot user: {str(e)}")
        raise Exception(f"Error retrieving bot user: {str(e)}")


@tool
async def create_notion_comment(
    rich_text: List[Dict[str, Any]],
    parent: Optional[Dict[str, Any]] = None,
    discussion_id: Optional[str] = None,
    config: RunnableConfig = None,
) -> NotionComment:
    """
    Create a comment in Notion. Requires 'insert comment' capabilities.

    Args:
        rich_text: Rich text content for the comment
        parent: Optional parent block or page
        discussion_id: Optional discussion ID
        config: Configuration containing user_id.

    Returns:
        Created Notion comment
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = "https://api.notion.com/v1/comments"
        data = {"rich_text": rich_text}
        if parent:
            data["parent"] = parent
        if discussion_id:
            data["discussion_id"] = discussion_id
        response = await execute_notion_request(
            "POST", url, credentials.token, json=data
        )
        return parse_comment(response)
    except Exception as e:
        logger.error(f"Error creating comment: {str(e)}")
        raise Exception(f"Error creating comment: {str(e)}")


@tool
async def retrieve_notion_comments(
    block_id: str,
    start_cursor: Optional[str] = None,
    page_size: int = 100,
    config: RunnableConfig = None,
) -> List[NotionComment]:
    """
    Retrieve a list of unresolved comments from a Notion page or block. Requires 'read comment' capabilities.

    Args:
        block_id: Block or page ID
        start_cursor: Pagination cursor
        page_size: Number of results per page
        config: Configuration containing user_id.

    Returns:
        List of Notion comments
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_notion_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Notion credentials")

        url = "https://api.notion.com/v1/comments"
        params = {"block_id": block_id, "page_size": min(page_size, 100)}
        if start_cursor:
            params["start_cursor"] = start_cursor
        response = await execute_notion_request(
            "GET", url, credentials.token, params=params
        )
        comments = response.get("results", [])
        return [parse_comment(comment) for comment in comments]
    except Exception as e:
        logger.error(f"Error retrieving comments for block {block_id}: {str(e)}")
        raise Exception(f"Error retrieving comments for block {block_id}: {str(e)}")


def get_notion_tools() -> List[Tool]:
    """Get all Notion tools."""
    return [
        list_notion_pages,
        search_notion_pages,
        get_notion_page,
        append_notion_block_children,
        retrieve_notion_block,
        retrieve_notion_block_children,
        delete_notion_block,
        retrieve_notion_page,
        update_notion_page_properties,
        create_notion_database,
        query_notion_database,
        retrieve_notion_database,
        update_notion_database,
        create_notion_database_item,
        search_notion,
        list_notion_users,
        retrieve_notion_user,
        retrieve_notion_bot_user,
        create_notion_comment,
        retrieve_notion_comments,
    ]
