from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "slack_conversations_history": {
        "id": "slack_conversations_history",
        "name": "Slack Channel History",
        "description": "Fetch recent messages from a Slack channel (optionally including message bodies).",
    },
    "slack_conversations_replies": {
        "id": "slack_conversations_replies",
        "name": "Slack Thread Replies",
        "description": "Fetch the messages in a Slack thread given a conversation and thread timestamp.",
    },
    "slack_channels_list": {
        "id": "slack_channels_list",
        "name": "Slack Channels List",
        "description": "List Slack channels the configured bot/user can access.",
    },
    "slack_send_message": {
        "id": "slack_send_message",
        "name": "Slack Send Message",
        "description": "Send a message to a Slack channel or a specific thread.",
    },
    "slack_list_users": {
        "id": "slack_list_users",
        "name": "Slack Users List",
        "description": "List users in the Slack workspace.",
    },
    "slack_get_user_info": {
        "id": "slack_get_user_info",
        "name": "Slack User Info",
        "description": "Get profile information for a specific Slack user.",
    },
    "slack_create_channel": {
        "id": "slack_create_channel",
        "name": "Slack Create Channel",
        "description": "Create a new Slack channel.",
    },
    "slack_set_channel_topic": {
        "id": "slack_set_channel_topic",
        "name": "Slack Set Channel Topic",
        "description": "Set or update the topic for a Slack channel.",
    },
    "slack_get_channel_info": {
        "id": "slack_get_channel_info",
        "name": "Slack Channel Info",
        "description": "Get metadata for a Slack channel.",
    },
    "slack_search_messages": {
        "id": "slack_search_messages",
        "name": "Slack Search Messages",
        "description": "Search messages across Slack channels for matching terms.",
    },
    "slack_get_workspace_info": {
        "id": "slack_get_workspace_info",
        "name": "Slack Workspace Info",
        "description": "Fetch high-level information about the current Slack workspace.",
    },
}

__all__ = ["TOOLS_DATA"]

