"""Slack tools for managing messages, channels, and users."""

from typing import List, Optional, Dict, Any
from langchain_core.tools import tool, Tool
from langchain_core.runnables import RunnableConfig
import logging
from .models import SlackMessage, SlackChannel, SlackUser
from .utils import (
    get_slack_credentials,
    execute_slack_request,
    parse_slack_message,
    parse_slack_channel,
    parse_slack_user,
    resolve_channel_id,
)
from common.tools.utils.writer import emit_update

logger = logging.getLogger(__name__)


@tool
async def slack_conversations_history(
    channel_id: str,
    include_activity_messages: bool = False,
    cursor: str = "",
    limit: int = 28,
    config: RunnableConfig = None,
) -> List[Dict[str, Any]]:
    """
    Get messages from the channel by channelID.

    Args:
        channel_id: ID of the channel in format Cxxxxxxxxxx or its name starting with #... or @... aka #general or @username_dm
        include_activity_messages: If true, the response will include activity messages such as 'channel_join' or 'channel_leave'
        cursor: Cursor for pagination
        limit: Limit of messages to fetch
        config: Configuration containing user_id.

    Returns:
        List of messages with timestamps, user IDs, and text content
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
            if "slack" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_slack_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        resolved_channel = resolve_channel_id(channel_id)

        url = "https://slack.com/api/conversations.history"
        params = {
            "channel": resolved_channel,
            "limit": limit,
            "inclusive": True,
        }

        if cursor:
            params["cursor"] = cursor
        if not include_activity_messages:
            params["exclude_archived"] = True

        response = await execute_slack_request(
            "GET", url, credentials.token, params=params
        )

        if not response.get("ok"):
            return [
                {"error": f"Slack API error: {response.get('error', 'Unknown error')}"}
            ]

        messages = response.get("messages", [])
        parsed_messages = [parse_slack_message(msg) for msg in messages]

        result = [msg.model_dump() for msg in parsed_messages]
        if response.get("has_more"):
            result.append(
                {
                    "next_cursor": response.get("response_metadata", {}).get(
                        "next_cursor"
                    )
                }
            )

        return result
    except Exception as e:
        logger.error(f"Error getting conversation history: {str(e)}")
        return [{"error": f"Error getting conversation history: {str(e)}"}]


@tool
async def slack_conversations_replies(
    channel_id: str,
    thread_ts: str,
    include_activity_messages: bool = False,
    cursor: str = "",
    limit: int = 28,
    config: RunnableConfig = None,
) -> List[Dict[str, Any]]:
    """
    Get a thread of messages posted to a conversation by channelID and thread_ts.

    Args:
        channel_id: ID of the channel in format Cxxxxxxxxxx or its name starting with #... or @...
        thread_ts: Unique identifier of either a thread's parent message or a message in the thread
        include_activity_messages: If true, the response will include activity messages
        cursor: Cursor for pagination
        limit: Limit of messages to fetch
        config: Configuration containing user_id.

    Returns:
        List of replies with timestamps, user IDs, and text content
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
            if "slack" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_slack_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        resolved_channel = resolve_channel_id(channel_id)

        url = "https://slack.com/api/conversations.replies"
        params = {
            "channel": resolved_channel,
            "ts": thread_ts,
            "limit": limit,
            "inclusive": True,
        }

        if cursor:
            params["cursor"] = cursor
        if not include_activity_messages:
            params["exclude_archived"] = True

        response = await execute_slack_request(
            "GET", url, credentials.token, params=params
        )

        if not response.get("ok"):
            return [
                {"error": f"Slack API error: {response.get('error', 'Unknown error')}"}
            ]

        messages = response.get("messages", [])
        parsed_messages = [parse_slack_message(msg) for msg in messages]

        result = [msg.model_dump() for msg in parsed_messages]
        if response.get("has_more"):
            result.append(
                {
                    "next_cursor": response.get("response_metadata", {}).get(
                        "next_cursor"
                    )
                }
            )

        return result
    except Exception as e:
        logger.error(f"Error getting conversation replies: {str(e)}")
        return [{"error": f"Error getting conversation replies: {str(e)}"}]


@tool
async def slack_channels_list(
    channel_types: str = "public_channel,private_channel",
    sort: str = "popularity",
    limit: int = 100,
    cursor: str = "",
    config: RunnableConfig = None,
) -> List[Dict[str, Any]]:
    """
    Get list of channels.

    Args:
        channel_types: Comma-separated channel types
        sort: Type of sorting
        limit: Limit of channels to fetch
        cursor: Cursor for pagination
        config: Configuration containing user_id.

    Returns:
        List of channels
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
            if "slack" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_slack_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        url = "https://slack.com/api/conversations.list"
        params = {
            "types": channel_types,
            "limit": limit,
            "exclude_archived": False,
        }

        if cursor:
            params["cursor"] = cursor

        response = await execute_slack_request(
            "GET", url, credentials.token, params=params
        )

        if not response.get("ok"):
            return [
                {"error": f"Slack API error: {response.get('error', 'Unknown error')}"}
            ]

        channels = response.get("channels", [])
        parsed_channels = [parse_slack_channel(ch) for ch in channels]

        result = [ch.model_dump() for ch in parsed_channels]
        if response.get("has_more"):
            result.append(
                {
                    "next_cursor": response.get("response_metadata", {}).get(
                        "next_cursor"
                    )
                }
            )

        return result
    except Exception as e:
        logger.error(f"Error listing channels: {str(e)}")
        return [{"error": f"Error listing channels: {str(e)}"}]


@tool
async def slack_send_message(
    channel_id: str,
    text: str,
    thread_ts: Optional[str] = None,
    reply_broadcast: bool = False,
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Send a message to a channel or thread.

    Args:
        channel_id: ID of the channel in format Cxxxxxxxxxx or its name starting with #... or @...
        text: Message text to send
        thread_ts: Optional timestamp of the parent message to reply to (for threading)
        reply_broadcast: If true, the reply will be broadcast to the channel
        config: Configuration containing user_id.

    Returns:
        Message details including timestamp and channel
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
            if "slack" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_slack_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        resolved_channel = resolve_channel_id(channel_id)

        url = "https://slack.com/api/chat.postMessage"
        data = {
            "channel": resolved_channel,
            "text": text,
        }

        if thread_ts:
            data["thread_ts"] = thread_ts
            data["reply_broadcast"] = reply_broadcast

        response = await execute_slack_request(
            "POST", url, credentials.token, json=data
        )

        if not response.get("ok"):
            return {
                "error": f"Slack API error: {response.get('error', 'Unknown error')}"
            }

        return {
            "success": True,
            "ts": response.get("ts"),
            "channel": response.get("channel"),
            "message": response.get("message", {}),
        }
    except Exception as e:
        logger.error(f"Error sending message: {str(e)}")
        return {"error": f"Error sending message: {str(e)}"}


@tool
async def slack_list_users(
    limit: int = 100,
    cursor: str = "",
    config: RunnableConfig = None,
) -> List[Dict[str, Any]]:
    """
    Get list of users in the workspace.

    Args:
        limit: Limit of users to fetch
        cursor: Cursor for pagination
        config: Configuration containing user_id.

    Returns:
        List of users in the workspace
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
            if "slack" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_slack_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        url = "https://slack.com/api/users.list"
        params = {"limit": limit}

        if cursor:
            params["cursor"] = cursor

        response = await execute_slack_request(
            "GET", url, credentials.token, params=params
        )

        if not response.get("ok"):
            return [
                {"error": f"Slack API error: {response.get('error', 'Unknown error')}"}
            ]

        users = response.get("members", [])
        parsed_users = [parse_slack_user(user) for user in users]

        result = [user.model_dump() for user in parsed_users]
        if response.get("has_more"):
            result.append(
                {
                    "next_cursor": response.get("response_metadata", {}).get(
                        "next_cursor"
                    )
                }
            )

        return result
    except Exception as e:
        logger.error(f"Error listing users: {str(e)}")
        return [{"error": f"Error listing users: {str(e)}"}]


@tool
async def slack_get_user_info(
    user_id_or_name: str,
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Get information about a specific user.

    Args:
        user_id_or_name: User ID (e.g., U1234567890) or username
        config: Configuration containing user_id.

    Returns:
        User information including profile details
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        current_user_id = configurable.get("user_id")
        if not current_user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "slack" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_slack_credentials(current_user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        url = "https://slack.com/api/users.info"
        params = {"user": user_id_or_name}

        response = await execute_slack_request(
            "GET", url, credentials.token, params=params
        )

        if not response.get("ok"):
            return {
                "error": f"Slack API error: {response.get('error', 'Unknown error')}"
            }

        user_data = response.get("user", {})
        parsed_user = parse_slack_user(user_data)

        return parsed_user.model_dump()
    except Exception as e:
        logger.error(f"Error getting user info: {str(e)}")
        return {"error": f"Error getting user info: {str(e)}"}


@tool
async def slack_create_channel(
    name: str,
    is_private: bool = False,
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Create a new channel.

    Args:
        name: Name of the channel to create (without # prefix)
        is_private: Whether the channel should be private
        config: Configuration containing user_id.

    Returns:
        Created channel information
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
            if "slack" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_slack_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        url = "https://slack.com/api/conversations.create"
        data = {
            "name": name,
            "is_private": is_private,
        }

        response = await execute_slack_request(
            "POST", url, credentials.token, json=data
        )

        if not response.get("ok"):
            return {
                "error": f"Slack API error: {response.get('error', 'Unknown error')}"
            }

        channel_data = response.get("channel", {})
        parsed_channel = parse_slack_channel(channel_data)

        return parsed_channel.model_dump()
    except Exception as e:
        logger.error(f"Error creating channel: {str(e)}")
        return {"error": f"Error creating channel: {str(e)}"}


@tool
async def slack_set_channel_topic(
    channel_id: str,
    topic: str,
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Set the topic for a channel.

    Args:
        channel_id: ID of the channel in format Cxxxxxxxxxx or its name starting with #
        topic: New topic text for the channel
        config: Configuration containing user_id.

    Returns:
        Success status and updated topic information
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
            if "slack" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_slack_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        resolved_channel = resolve_channel_id(channel_id)

        url = "https://slack.com/api/conversations.setTopic"
        data = {
            "channel": resolved_channel,
            "topic": topic,
        }

        response = await execute_slack_request(
            "POST", url, credentials.token, json=data
        )

        if not response.get("ok"):
            return {
                "error": f"Slack API error: {response.get('error', 'Unknown error')}"
            }

        return {
            "success": True,
            "topic": response.get("topic"),
        }
    except Exception as e:
        logger.error(f"Error setting channel topic: {str(e)}")
        return {"error": f"Error setting channel topic: {str(e)}"}


@tool
async def slack_get_channel_info(
    channel_id: str,
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Get information about a specific channel.

    Args:
        channel_id: ID of the channel in format Cxxxxxxxxxx or its name starting with #
        config: Configuration containing user_id.

    Returns:
        Channel information including topic, purpose, and member count
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_slack_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        resolved_channel = resolve_channel_id(channel_id)

        url = "https://slack.com/api/conversations.info"
        params = {"channel": resolved_channel}

        response = await execute_slack_request(
            "GET", url, credentials.token, params=params
        )

        if not response.get("ok"):
            return {
                "error": f"Slack API error: {response.get('error', 'Unknown error')}"
            }

        channel_data = response.get("channel", {})
        parsed_channel = parse_slack_channel(channel_data)

        return parsed_channel.model_dump()
    except Exception as e:
        logger.error(f"Error getting channel info: {str(e)}")
        return {"error": f"Error getting channel info: {str(e)}"}


@tool
async def slack_search_messages(
    query: str,
    sort: str = "timestamp",
    sort_dir: str = "desc",
    count: int = 20,
    page: int = 1,
    config: RunnableConfig = None,
) -> List[Dict[str, Any]]:
    """
    Search for messages across all channels.

    Args:
        query: Search query string
        sort: Sort order for results (timestamp, score)
        sort_dir: Sort direction (asc, desc)
        count: Number of results to return per page
        page: Page number for pagination
        config: Configuration containing user_id.

    Returns:
        List of matching messages with context
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_slack_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        url = "https://slack.com/api/search.messages"
        params = {
            "query": query,
            "sort": sort,
            "sort_dir": sort_dir,
            "count": count,
            "page": page,
        }

        response = await execute_slack_request(
            "GET", url, credentials.token, params=params
        )

        if not response.get("ok"):
            return [
                {"error": f"Slack API error: {response.get('error', 'Unknown error')}"}
            ]

        messages = response.get("messages", {}).get("matches", [])

        return [
            {
                "text": msg.get("text"),
                "user": msg.get("user"),
                "username": msg.get("username"),
                "ts": msg.get("ts"),
                "channel": msg.get("channel", {}).get("name"),
                "channel_id": msg.get("channel", {}).get("id"),
                "permalink": msg.get("permalink"),
                "score": msg.get("score"),
            }
            for msg in messages
        ]
    except Exception as e:
        logger.error(f"Error searching messages: {str(e)}")
        return [{"error": f"Error searching messages: {str(e)}"}]


@tool
async def slack_get_workspace_info(
    config: RunnableConfig = None,
) -> Dict[str, Any]:
    """
    Get information about the current workspace.

    Args:
        config: Configuration containing user_id.

    Returns:
        Workspace information including name, domain, and team details
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_slack_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Slack credentials")

        url = "https://slack.com/api/team.info"
        response = await execute_slack_request("GET", url, credentials.token)

        if not response.get("ok"):
            return {
                "error": f"Slack API error: {response.get('error', 'Unknown error')}"
            }

        team_data = response.get("team", {})

        return {
            "id": team_data.get("id"),
            "name": team_data.get("name"),
            "domain": team_data.get("domain"),
            "email_domain": team_data.get("email_domain"),
            "icon": team_data.get("icon"),
            "enterprise_id": team_data.get("enterprise_id"),
            "enterprise_name": team_data.get("enterprise_name"),
        }
    except Exception as e:
        logger.error(f"Error getting workspace info: {str(e)}")
        return {"error": f"Error getting workspace info: {str(e)}"}


def get_slack_tools() -> List[Tool]:
    """Get all Slack tools."""
    return [
        slack_conversations_history,
        slack_conversations_replies,
        slack_channels_list,
        slack_send_message,
        slack_list_users,
        slack_get_user_info,
        slack_create_channel,
        slack_set_channel_topic,
        slack_get_channel_info,
        slack_search_messages,
        slack_get_workspace_info,
    ]
