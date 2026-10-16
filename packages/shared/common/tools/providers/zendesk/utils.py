import httpx
from typing import Optional
from common.integration import get_token
from common.modules.token import TokenResponse
import logging

logger = logging.getLogger(__name__)


async def get_zendesk_credentials(user_id: str, connectorId: str | None = None) -> Optional[TokenResponse]:
    """Get Zendesk credentials from token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id, connector_name="Zendesk", connectorId=connectorId, needs_refresh=False
        )
        return user_data
    except Exception as e:
        logger.error(f"Error getting Zendesk credentials: {str(e)}")
        return None


async def make_zendesk_request(
    zendesk_domain: str,
    email: str,
    api_token: str,
    data: dict,
) -> dict:
    """Make a lightweight Zendesk request."""
    import base64

    api_url = f"https://{zendesk_domain}.zendesk.com/api/v2/tickets.json"

    # Create Basic Auth header
    credentials = f"{email}/token:{api_token}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    auth_header = f"Basic {encoded_credentials}"

    headers = {
        "Content-Type": "application/json",
        "Authorization": auth_header,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            api_url,
            json=data,
            headers=headers,
            timeout=30.0,
        )

        if response.status_code == 401:
            raise ValueError("Invalid Zendesk credentials")

        response.raise_for_status()
        return response.json()
