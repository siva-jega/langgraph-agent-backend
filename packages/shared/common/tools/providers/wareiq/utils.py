"""WareIQ API client: credentials and HTTP requests."""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from common.integration import get_token

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(5)


async def get_wareiq_credentials(
    user_id: str, connector_id: str | None = None
) -> Optional[Any]:
    """Get WareIQ credentials (API token) from the token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="wareiq",
            connectorId=connector_id or "",
            needs_refresh=False,
        )
        if user_data and user_data.token:
            return user_data
        if user_data and user_data.error:
            logger.warning(
                "WareIQ credentials failed for user_id=%s: %s",
                user_id,
                user_data.error,
            )
        else:
            logger.warning(
                "WareIQ: no credentials for user_id=%s (connect WareIQ in Apps)",
                user_id,
            )
        return None
    except Exception as e:
        logger.error("Error getting WareIQ credentials: %s", e)
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=lambda rs: rs.outcome is not None and rs.outcome.failed and not isinstance(rs.outcome.exception(), ValueError),
    reraise=True,
)
async def make_wareiq_request(
    endpoint: str,
    credentials: Dict[str, str],
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    """Make a WareIQ request using the API-key settings from Postman.

    Args:
        endpoint: API path (e.g. /orders)
        credentials: base_url, auth_key, and api_key values
        method: HTTP method (GET, POST, PUT, etc.)
        payload: Request body dict (for POST/PUT)
        params: Query parameters (for GET)
    """
    url = f"{credentials['base_url'].rstrip('/')}{endpoint}"
    headers = {
        "Content-Type": "application/json",
        credentials["auth_key"]: credentials["api_key"],
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
                logger.warning("WareIQ 401 for %s: %s", endpoint, detail[:500])
                raise ValueError(
                    "WareIQ authentication failed (401). Check the API key and header name."
                )
            if response.status_code == 403:
                detail = (response.text or "").strip()
                logger.warning("WareIQ 403 for %s: %s", endpoint, detail[:500])
                raise ValueError(f"WareIQ returned 403 Forbidden for {endpoint}.")
            if response.status_code == 404:
                raise ValueError(f"WareIQ endpoint not found: {endpoint}")
            if response.status_code >= 400:
                raise ValueError(
                    f"WareIQ API error {response.status_code}: {response.text[:300]}"
                )

            return response.json()


async def get_credentials_from_config(config) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Extract WareIQ API-key credentials and optional error from RunnableConfig.

    Returns:
        (credentials, error_message)
    """
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    if not user_id:
        return None, "No user_id in configuration"

    all_apps = configurable.get("apps_config", []) or configurable.get("apps", [])
    app_config = next(
        (a for a in all_apps if (a.get("slug") or "").lower() == "wareiq"),
        None,
    )
    connector_id = app_config.get("id") if app_config else None

    creds = await get_wareiq_credentials(user_id, connector_id=connector_id)
    if not creds or not creds.token:
        return (
            None,
            "WareIQ is not connected or credentials could not be loaded. "
            "Connect WareIQ in the Apps section.",
        )

    stored_config = app_config.get("config", {}) if app_config else {}
    base_url = (app_config or {}).get("base_url") or stored_config.get("base_url")
    auth_key = (app_config or {}).get("auth_key") or stored_config.get("auth_key")
    if not base_url or not auth_key:
        return (
            None,
            "WareIQ connection is missing base_url or auth_key. Reconnect WareIQ using the latest credentials.",
        )

    return {
        "base_url": str(base_url),
        "auth_key": str(auth_key),
        "api_key": creds.token,
    }, None
