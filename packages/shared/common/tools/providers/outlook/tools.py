"""Outlook tools for managing messages."""

from typing import List, Optional
from langchain_core.tools import tool, Tool
from langchain_core.runnables import RunnableConfig
import logging
from .models import OutlookMessage
from .utils import (
    get_outlook_credentials,
    execute_outlook_request,
    parse_outlook_message,
)
from common.tools.utils.writer import emit_update

logger = logging.getLogger(__name__)


@tool
async def list_outlook_messages(
    max_results: int = 10,
    folder_id: Optional[str] = None,
    include_body: bool = True,
    config: RunnableConfig = None,
) -> List[OutlookMessage]:
    """
    List recent Outlook messages with optional folder filtering and body inclusion.

    Args:
        max_results: Maximum number of messages to return
        folder_id: Folder ID to filter messages
        include_body: Whether to include message body
        config: Configuration containing user_id.

    Returns:
        List of Outlook messages
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
            if "outlook" in slug or "microsoft" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_outlook_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Outlook credentials")

        emit_update(
            {
                "tool": "Outlook",
                "update": f"Listing messages with max_results: {max_results}, folder_id: {folder_id}",
            },
            "outlook",
        )

        if folder_id:
            url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder_id}/messages?$top={max_results}"
        else:
            url = f"https://graph.microsoft.com/v1.0/me/messages?$top={max_results}"

        response = await execute_outlook_request("GET", url, credentials.token)
        messages = response.get("value", [])
        parsed_messages = [parse_outlook_message(msg) for msg in messages]

        emit_update(
            {"tool": "Outlook", "update": f"Found {len(parsed_messages)} messages"},
            "outlook",
        )
        return parsed_messages
    except Exception as e:
        logger.error(f"Error listing messages: {str(e)}")
        raise Exception(f"Error listing messages: {str(e)}")


@tool
async def search_outlook_messages(
    query: str,
    max_results: int = 10,
    config: RunnableConfig = None,
) -> List[OutlookMessage]:
    """
    Search Outlook messages using Microsoft Graph search syntax.

    Args:
        query: Search query
        max_results: Maximum number of messages to return
        config: Configuration containing user_id.

    Returns:
        List of Outlook messages
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
            if "outlook" in slug or "microsoft" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_outlook_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Outlook credentials")

        emit_update(
            {
                "tool": "Outlook",
                "update": f"Searching messages with query: {query}, max_results: {max_results}",
            },
            "outlook",
        )

        url = f"https://graph.microsoft.com/v1.0/me/messages?$search={query}&$top={max_results}"
        response = await execute_outlook_request("GET", url, credentials.token)
        messages = response.get("value", [])
        parsed_messages = [parse_outlook_message(msg) for msg in messages]

        emit_update(
            {
                "tool": "Outlook",
                "update": f"Found {len(parsed_messages)} matching messages",
            },
            "outlook",
        )
        return parsed_messages
    except Exception as e:
        logger.error(f"Error searching messages: {str(e)}")
        raise Exception(f"Error searching messages: {str(e)}")


@tool
async def get_outlook_message(
    message_id: str,
    config: RunnableConfig = None,
) -> Optional[OutlookMessage]:
    """
    Get a single Outlook message by ID.

    Args:
        message_id: Outlook message ID
        config: Configuration containing user_id.

    Returns:
        Parsed message if found
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
            if "outlook" in slug or "microsoft" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_outlook_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Outlook credentials")

        emit_update(
            {"tool": "Outlook", "update": f"Getting message with id: {message_id}"},
            "outlook",
        )

        url = f"https://graph.microsoft.com/v1.0/me/messages/{message_id}"
        response = await execute_outlook_request("GET", url, credentials.token)
        message = parse_outlook_message(response)

        emit_update(
            {"tool": "Outlook", "update": f"Found message: {message.subject}"},
            "outlook",
        )
        return message
    except Exception as e:
        logger.error(f"Error getting message {message_id}: {str(e)}")
        raise Exception(f"Error getting message {message_id}: {str(e)}")


def get_outlook_tools() -> List[Tool]:
    """Get all Outlook tools."""
    return [
        list_outlook_messages,
        search_outlook_messages,
        get_outlook_message,
    ]
