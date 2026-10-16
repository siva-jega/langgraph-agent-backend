"""Shiprocket API client: credentials and HTTP requests."""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from common.integration import get_token

logger = logging.getLogger(__name__)

BASE_URL = "https://apiv2.shiprocket.in/v1/external"

_semaphore = asyncio.Semaphore(5)


async def get_shiprocket_credentials(
    user_id: str, connector_id: str | None = None
) -> Optional[Any]:
    """Get Shiprocket credentials (bearer token) from the token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="shiprocket",
            connectorId=connector_id or "",
            needs_refresh=False,
        )
        if user_data and user_data.token:
            return user_data
        if user_data and user_data.error:
            logger.warning(
                "Shiprocket credentials failed for user_id=%s: %s",
                user_id,
                user_data.error,
            )
        else:
            logger.warning(
                "Shiprocket: no credentials for user_id=%s (connect Shiprocket in Apps)",
                user_id,
            )
        return None
    except Exception as e:
        logger.error("Error getting Shiprocket credentials: %s", e)
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=lambda rs: rs.outcome is not None and rs.outcome.failed and not isinstance(rs.outcome.exception(), ValueError),
    reraise=True,
)
async def make_shiprocket_request(
    endpoint: str,
    token: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    """Make a Shiprocket API request with Bearer token authentication.

    Args:
        endpoint: API path (e.g. /orders/create/adhoc)
        token: Shiprocket bearer token
        method: HTTP method (GET, POST, PUT, etc.)
        payload: Request body dict (for POST/PUT)
        params: Query parameters (for GET)
    """
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    async with _semaphore:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers, params=params)
            elif method.upper() == "POST":
                response = await client.post(url, headers=headers, json=payload)
            elif method.upper() == "PUT":
                response = await client.put(url, headers=headers, json=payload)
            elif method.upper() == "PATCH":
                response = await client.patch(url, headers=headers, json=payload)
            else:
                response = await client.request(
                    method.upper(), url, headers=headers, json=payload, params=params,
                )

            if response.status_code == 401:
                detail = (response.text or "").strip()
                logger.warning("Shiprocket 401 for %s: %s", endpoint, detail[:500])
                raise ValueError(
                    "Shiprocket authentication failed (401). "
                    "Your token may have expired. Reconnect Shiprocket in the Apps section."
                )
            if response.status_code == 403:
                detail = (response.text or "").strip()
                logger.warning("Shiprocket 403 for %s: %s", endpoint, detail[:500])
                raise ValueError(f"Shiprocket returned 403 Forbidden for {endpoint}.")
            if response.status_code == 404:
                raise ValueError(f"Shiprocket endpoint not found: {endpoint}")
            if response.status_code >= 400:
                raise ValueError(
                    f"Shiprocket API error {response.status_code}: {response.text[:300]}"
                )

            return response.json()


async def get_credentials_from_config(config) -> Tuple[Optional[str], Optional[str]]:
    """Extract Shiprocket bearer token and optional error from RunnableConfig.

    Returns:
        (token, error_message)
    """
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    if not user_id:
        return None, "No user_id in configuration"

    all_apps = configurable.get("apps_config", []) or configurable.get("apps", [])
    app_config = next(
        (a for a in all_apps if (a.get("slug") or "").lower() == "shiprocket"),
        None,
    )
    connector_id = app_config.get("id") if app_config else None

    creds = await get_shiprocket_credentials(user_id, connector_id=connector_id)
    if not creds or not creds.token:
        return (
            None,
            "Shiprocket is not connected or credentials could not be loaded. "
            "Connect Shiprocket in the Apps section.",
        )

    return creds.token, None
