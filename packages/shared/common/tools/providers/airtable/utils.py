"""Utility functions for Airtable integration."""

import asyncio
from typing import Optional, Dict, Any
import aiohttp
from common.integration import get_token
from common.modules.token import TokenResponse
import logging
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(5)


async def get_airtable_credentials(user_id: str, connectorId: str | None = None) -> Optional[TokenResponse]:
    """Get Airtable credentials from token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="airtable",
            connectorId=connectorId,
            needs_refresh=True,
        )
        return user_data
    except Exception as e:
        logger.error(f"Error getting Airtable credentials: {str(e)}")
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
)
async def make_airtable_request(
    method: str,
    url: str,
    access_token: str,
    data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Make HTTP request to Airtable API with retry mechanism."""
    async with _semaphore:
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=data,
                    params=params,
                ) as response:
                    if response.status == 401:
                        logger.warning(
                            "Airtable token unauthorized. Attempting refresh..."
                        )
                        # Token refresh would be handled by caller
                        raise ValueError("Airtable credentials expired")
                    elif response.status >= 400:
                        error_text = await response.text()
                        raise Exception(
                            f"Airtable API error {response.status}: {error_text}"
                        )
                    return await response.json()
        except Exception as e:
            if "credentials expired" in str(e):
                raise
            raise Exception(f"Airtable API request failed: {str(e)}")
