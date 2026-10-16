"""Utility functions for Outlook integration."""

import asyncio
from typing import Optional, Dict, Any
import requests
from common.integration import get_token
from common.modules.token import TokenResponse
import logging
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(5)


async def get_outlook_credentials(user_id: str, connectorId: str | None = None) -> Optional[TokenResponse]:
    """Get Outlook credentials from token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="outlook",
            connectorId=connectorId,
            needs_refresh=True,
        )
        return user_data
    except Exception as e:
        logger.error(f"Error getting Outlook credentials: {str(e)}")
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
)
async def execute_outlook_request(
    method: str, url: str, access_token: str, **kwargs
) -> Any:
    """Execute Outlook API request with retry logic."""
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
                    raise Exception(f"Outlook API error: {str(e)}")
                await asyncio.sleep(2**attempt)


def parse_outlook_message(message: Dict[str, Any]):
    """Parse raw Outlook message into OutlookMessage object."""
    from .models import OutlookMessage

    sender = message.get("from", {}).get("emailAddress", {}).get("address", "")
    recipients = [r["emailAddress"]["address"] for r in message.get("toRecipients", [])]
    body = message.get("body", {}).get("content", "")
    attachments = message.get("attachments", [])
    return OutlookMessage(
        id=message["id"],
        subject=message.get("subject", ""),
        sender=sender,
        recipients=recipients,
        body=body,
        date=message.get("receivedDateTime", ""),
        is_read=message.get("isRead", False),
        importance=message.get("importance", "normal"),
        attachments=attachments if attachments else None,
    )
