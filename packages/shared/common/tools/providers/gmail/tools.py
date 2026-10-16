"""Gmail tools for managing emails and threads."""

from typing import List, Optional
from langchain_core.tools import tool, Tool
from langchain_core.runnables import RunnableConfig
import logging
from .models import GmailMessage, GmailThread
from .utils import (
    build_gmail_service,
    execute_gmail_request,
    get_full_message,
    get_full_thread,
    get_gmail_credentials,
    get_partial_message,
    get_partial_thread,
)
from common.tools.utils.writer import emit_update

logger = logging.getLogger(__name__)


# Message tools
@tool(response_format="content_and_artifact")
async def list_messages(
    max_results: int = 10,
    label_ids: Optional[List[str]] = None,
    include_body: bool = True,
    config: RunnableConfig = None,
) -> List[GmailMessage]:
    """
    List recent Gmail messages with concurrent fetching.

    Args:
        max_results: Maximum number of messages to return.
        label_ids: Filter messages by label IDs.
        include_body: Whether to fetch full message bodies.
        config: Configuration containing user_id.

    Returns:
        List of parsed Gmail messages.
    """
    try:
        if max_results > 20:
            raise ValueError("Max results must be less than 20")

        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        connectorId = None

        apps = configurable.get("apps") or []
        for app in apps:
            if app and app.get("slug") == "gmail":
                connectorId = app.get("connectorId") or app.get("id")
                break
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_gmail_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Gmail credentials")

        gmail_service = build_gmail_service(credentials.token)
        emit_update(
            {
                "tool": "Gmail",
                "update": f"Listing messages with max_results: {max_results} and label_ids: {label_ids}",
            },
            "gmail",
        )

        params = {"userId": "me", "maxResults": max_results}
        if label_ids:
            params["labelIds"] = label_ids

        request = gmail_service.users().messages().list(**params)
        response = await execute_gmail_request(request)

        if not response.get("messages"):
            return [], {"name": "List Gmail Messages", "data": []}

        parsed_messages: List[GmailMessage] = []
        if include_body:
            logger.info("Fetching full messages")
            for msg in response["messages"]:
                full_msg = await get_full_message(gmail_service, msg["id"])
                if isinstance(full_msg, GmailMessage):
                    parsed_messages.append(full_msg)
        else:
            logger.info("Fetching partial messages")
            for msg in response["messages"]:
                partial = await get_partial_message(gmail_service, msg["id"])
                if isinstance(partial, GmailMessage):
                    parsed_messages.append(partial)

        return parsed_messages, {
            "name": "Gmail",
            "tool": "List Messages",
            "data": [
                {"id": m.id, "subject": m.subject, "sender": m.sender, "date": m.date}
                for m in parsed_messages
            ],
        }

    except Exception as e:
        logger.error(f"Error listing messages: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def search_messages(
    query: str,
    max_results: int = 10,
    config: RunnableConfig = None,
) -> List[GmailMessage]:
    """
    Search Gmail messages with concurrent fetching.

    Args:
        query: Gmail search query, strictly follow the search syntax.
        max_results: Maximum number of results.
        config: Configuration containing user_id.

    Returns:
        List of matching messages.
    """
    try:

        if max_results > 20:
            raise ValueError("Max results must be less than 20")

        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        connectorId = None
        apps = configurable.get("apps") or []
        for app in apps:
            if app and app.get("slug") == "gmail":
                connectorId = app.get("connectorId") or app.get("id")
                break
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_gmail_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Gmail credentials")

        gmail_service = build_gmail_service(credentials.token)
        emit_update(
            {
                "tool": "Gmail",
                "update": f"Searching messages with query: {query} and max_results: {max_results}",
            },
            "gmail",
        )

        request = (
            gmail_service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
        )
        response = await execute_gmail_request(request)

        if not response.get("messages"):
            return [], {"name": "Search Gmail Messages", "data": []}

        parsed_messages: List[GmailMessage] = []
        for msg in response["messages"]:
            message = await get_full_message(gmail_service, msg["id"])
            if isinstance(message, GmailMessage):
                parsed_messages.append(message)

        emit_update(
            {"tool": "Gmail", "update": f"Found {len(parsed_messages)} messages"},
            "gmail",
        )

        return parsed_messages, {
            "name": "Gmail",
            "tool": "Search Messages",
            "data": parsed_messages,
        }

    except Exception as e:
        logger.error(f"Error searching messages: {str(e)}")
        raise


@tool
async def get_message(
    message_id: str,
    config: RunnableConfig = None,
) -> Optional[GmailMessage]:
    """
    Get a single Gmail message by ID.

    Args:
        message_id: Gmail message ID.
        config: Configuration containing user_id.

    Returns:
        Parsed message if found.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        connectorId = None
        apps = configurable.get("apps") or []
        for app in apps:
            if app and app.get("slug") == "gmail":
                connectorId = app.get("connectorId") or app.get("id")
                break
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_gmail_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Gmail credentials")

        gmail_service = build_gmail_service(credentials.token)
        emit_update(
            {"tool": "Gmail", "update": f"Getting message with id: {message_id}"},
            "gmail",
        )
        message = await get_full_message(gmail_service, message_id)
        emit_update(
            {
                "tool": "Gmail",
                "update": f"Found message: {message.subject if message else 'Unknown'}",
            },
            "gmail",
        )
        return message, {
            "name": "Gmail",
            "tool": "Get Message",
            "data": message,
        }

    except Exception as e:
        logger.error(f"Error getting message {message_id}: {str(e)}")
        raise


# Thread tools
@tool(response_format="content_and_artifact")
async def list_threads(
    max_results: int = 10,
    label_ids: Optional[List[str]] = None,
    include_messages: bool = True,
    query: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[GmailThread]:
    """
    List recent Gmail conversation threads with concurrent fetching.

    Args:
        max_results: Maximum number of threads to return.
        label_ids: Filter threads by label IDs.
        include_messages: Whether to fetch full thread messages.
        query: Search query to filter threads.
        config: Configuration containing user_id.

    Returns:
        List of parsed Gmail threads.
    """
    try:
        if max_results > 20:
            raise ValueError("Max results must be less than 20")

        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        connectorId = None
        apps = configurable.get("apps") or []
        for app in apps:
            if app and app.get("slug") == "gmail":
                connectorId = app.get("connectorId") or app.get("id")
                break
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_gmail_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Gmail credentials")

        gmail_service = build_gmail_service(credentials.token)
        emit_update(
            {
                "tool": "Gmail",
                "update": f"Listing threads with max_results: {max_results} and label_ids: {label_ids} and query: {query}",
            },
            "gmail",
        )

        params = {"userId": "me", "maxResults": max_results}

        if label_ids:
            params["labelIds"] = label_ids

        if query:
            params["q"] = query

        request = gmail_service.users().threads().list(**params)
        response = await execute_gmail_request(request)

        if not response.get("threads"):
            return [], {"name": "List Gmail Threads", "data": []}

        emit_update(
            {"tool": "Gmail", "update": f"Found {len(response['threads'])} threads"},
            "gmail",
        )

        parsed_threads: List[GmailThread] = []
        if include_messages:
            logger.info("Fetching full threads with messages")
            for thread in response["threads"]:
                full_thread = await get_full_thread(gmail_service, thread["id"])
                if isinstance(full_thread, GmailThread):
                    parsed_threads.append(full_thread)
        else:
            logger.info("Fetching partial threads")
            for thread in response["threads"]:
                partial_thread = await get_partial_thread(gmail_service, thread["id"])
                if isinstance(partial_thread, GmailThread):
                    parsed_threads.append(partial_thread)

        return parsed_threads, {
            "name": "Gmail",
            "tool": "List Threads",
            "data": parsed_threads,
        }

    except Exception as e:
        logger.error(f"Error listing threads: {str(e)}")
        raise


@tool
async def get_thread_detail(
    thread_id: str,
    config: RunnableConfig = None,
) -> Optional[GmailThread]:
    """
    Get detailed information for a Gmail thread including all messages.

    Args:
        thread_id: Gmail thread ID.
        config: Configuration containing user_id.

    Returns:
        Parsed thread with all messages if found.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_gmail_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Gmail credentials")

        gmail_service = build_gmail_service(credentials.token)
        emit_update(
            {"tool": "Gmail", "update": f"Getting thread with id: {thread_id}"},
            "gmail",
        )
        thread = await get_full_thread(gmail_service, thread_id)
        emit_update(
            {
                "tool": "Gmail",
                "update": f"Found thread: {thread.subject if thread else 'Unknown'}",
            },
            "gmail",
        )
        return thread, {
            "name": "Gmail",
            "tool": "Get Thread Detail",
            "data": thread,
        }

    except Exception as e:
        logger.error(f"Error getting thread detail {thread_id}: {str(e)}")
        raise


def get_gmail_tools() -> List[Tool]:
    """Get all Gmail tools."""
    return [
        list_messages,
        search_messages,
        get_message,
        list_threads,
        get_thread_detail,
    ]
