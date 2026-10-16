"""DTDC API client: credentials and HTTP requests."""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from common.integration import get_token

logger = logging.getLogger(__name__)

BASE_URL = "https://blktracksvc.dtdc.com/dtdc-api/rest"

_semaphore = asyncio.Semaphore(5)


async def get_dtdc_credentials(
    user_id: str, connector_id: str | None = None
) -> Optional[Any]:
    """Get DTDC credentials (API key + customer code) from the token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="dtdc",
            connectorId=connector_id or "",
            needs_refresh=False,
        )
        if user_data and user_data.token:
            return user_data
        if user_data and user_data.error:
            logger.warning(
                "DTDC credentials failed for user_id=%s: %s",
                user_id,
                user_data.error,
            )
        else:
            logger.warning(
                "DTDC: no credentials for user_id=%s (connect DTDC in Apps)",
                user_id,
            )
        return None
    except Exception as e:
        logger.error("Error getting DTDC credentials: %s", e)
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=lambda rs: rs.outcome is not None and rs.outcome.failed and not isinstance(rs.outcome.exception(), ValueError),
    reraise=True,
)
async def make_dtdc_request(
    endpoint: str,
    api_key: str,
    method: str = "POST",
    payload: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    """Make a DTDC API request with API key authentication."""
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "X-Access-Token": api_key,
    }

    async with _semaphore:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method.upper() == "GET":
                response = await client.get(url, headers=headers, params=params)
            elif method.upper() == "POST":
                response = await client.post(url, headers=headers, json=payload)
            elif method.upper() == "PUT":
                response = await client.put(url, headers=headers, json=payload)
            else:
                response = await client.request(
                    method.upper(), url, headers=headers, json=payload, params=params,
                )

            if response.status_code == 401:
                detail = (response.text or "").strip()
                logger.warning("DTDC 401 for %s: %s", endpoint, detail[:500])
                raise ValueError(
                    "DTDC authentication failed (401). "
                    "Your API key may be invalid. Reconnect DTDC in the Apps section."
                )
            if response.status_code == 403:
                detail = (response.text or "").strip()
                logger.warning("DTDC 403 for %s: %s", endpoint, detail[:500])
                raise ValueError(f"DTDC returned 403 Forbidden for {endpoint}.")
            if response.status_code == 404:
                raise ValueError(f"DTDC endpoint not found: {endpoint}")
            if response.status_code >= 400:
                raise ValueError(
                    f"DTDC API error {response.status_code}: {response.text[:300]}"
                )

            return response.json()


async def get_credentials_from_config(config) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract DTDC API key, customer code, and optional error from RunnableConfig.

    Returns:
        (api_key, customer_code, error_message)
    """
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    if not user_id:
        return None, None, "No user_id in configuration"

    all_apps = configurable.get("apps_config", []) or configurable.get("apps", [])
    app_config = next(
        (a for a in all_apps if (a.get("slug") or "").lower() == "dtdc"),
        None,
    )
    connector_id = app_config.get("id") if app_config else None

    creds = await get_dtdc_credentials(user_id, connector_id=connector_id)
    if not creds or not creds.token:
        return (
            None,
            None,
            "DTDC is not connected or credentials could not be loaded. "
            "Connect DTDC in the Apps section.",
        )

    api_key = creds.token
    customer_code = getattr(creds, "customer_code", None) or (
        creds.config.get("customer_code") if hasattr(creds, "config") and isinstance(creds.config, dict) else None
    )

    return api_key, customer_code, None
