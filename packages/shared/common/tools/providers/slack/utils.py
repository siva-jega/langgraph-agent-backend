"""Utility functions for Slack integration."""

import asyncio
from typing import Optional, Dict, Any
import requests
from common.integration import get_token
from common.modules.token import TokenResponse
import logging
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(5)


async def get_slack_credentials(user_id: str, connectorId: str | None = None) -> Optional[TokenResponse]:
    """Get Slack credentials from token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="Slack",
            connectorId=connectorId,
            needs_refresh=True,
        )
        return user_data
    except Exception as e:
        logger.error(f"Error getting Slack credentials: {str(e)}")
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
)
async def execute_slack_request(
    method: str, url: str, access_token: str, **kwargs
) -> Any:
    """Execute Slack API request with retry logic."""
    async with _semaphore:
        for attempt in range(3):
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {access_token}"
            headers["Content-Type"] = "application/json"
            try:
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(
                    None,
                    lambda: requests.request(method, url, headers=headers, **kwargs),
                )
                if resp.status_code == 401 and attempt < 2:
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == 2:
                    if hasattr(e, "response") and e.response.status_code in [
                        429,
                        500,
                        502,
                        503,
                        504,
                    ]:
                        raise Exception(f"Slack API error: {e.response.status_code}")
                    raise Exception(f"Slack API error: {str(e)}")
                await asyncio.sleep(2**attempt)


def parse_slack_message(message: Dict[str, Any]):
    """Parse raw Slack message into SlackMessage object."""
    from .models import SlackMessage

    return SlackMessage(
        ts=message.get("ts", ""),
        user=message.get("user"),
        text=message.get("text"),
        thread_ts=message.get("thread_ts"),
        reply_count=message.get("reply_count"),
        replies=message.get("replies"),
        attachments=message.get("attachments"),
        reactions=message.get("reactions"),
        permalink=message.get("permalink"),
        channel=message.get("channel"),
    )


def parse_slack_channel(channel: Dict[str, Any]):
    """Parse raw Slack channel into SlackChannel object."""
    from .models import SlackChannel

    return SlackChannel(
        id=channel.get("id", ""),
        name=channel.get("name", ""),
        is_channel=channel.get("is_channel", False),
        is_group=channel.get("is_group", False),
        is_im=channel.get("is_im", False),
        is_mpim=channel.get("is_mpim", False),
        is_private=channel.get("is_private", False),
        is_archived=channel.get("is_archived", False),
        is_general=channel.get("is_general", False),
        is_shared=channel.get("is_shared", False),
        is_org_shared=channel.get("is_org_shared", False),
        is_member=channel.get("is_member", False),
        created=channel.get("created"),
        creator=channel.get("creator"),
        topic=channel.get("topic"),
        purpose=channel.get("purpose"),
        num_members=channel.get("num_members"),
    )


def parse_slack_user(user: Dict[str, Any]):
    """Parse raw Slack user into SlackUser object."""
    from .models import SlackUser

    return SlackUser(
        id=user.get("id", ""),
        name=user.get("name", ""),
        real_name=user.get("real_name"),
        display_name=user.get("profile", {}).get("display_name"),
        email=user.get("profile", {}).get("email"),
        is_admin=user.get("is_admin"),
        is_owner=user.get("is_owner"),
        is_primary_owner=user.get("is_primary_owner"),
        is_restricted=user.get("is_restricted"),
        is_ultra_restricted=user.get("is_ultra_restricted"),
        is_bot=user.get("is_bot"),
        is_app_user=user.get("is_app_user"),
        profile=user.get("profile"),
        tz=user.get("tz"),
        tz_label=user.get("tz_label"),
        tz_offset=user.get("tz_offset"),
    )


def resolve_channel_id(channel_id: str) -> str:
    """Resolve channel name to ID if needed."""
    if channel_id.startswith("#"):
        return channel_id[1:]
    elif channel_id.startswith("@"):
        return channel_id[1:]
    return channel_id
