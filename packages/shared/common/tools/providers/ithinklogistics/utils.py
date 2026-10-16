"""iThink Logistics API client: credentials and HTTP requests."""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from common.project_settings import project_settings
from common.integration import get_token
from common.modules.token.encryption import TokenEncryption

logger = logging.getLogger(__name__)

# Production URLs
BASE_URL_TRACKING = "https://api.ithinklogistics.com"
BASE_URL_ORDERS = "https://my.ithinklogistics.com"

_semaphore = asyncio.Semaphore(5)


async def get_ithinklogistics_credentials(
    user_id: str, connector_id: str | None = None
) -> Optional[Any]:
    """Get iThink Logistics credentials (access_token + secret_key) from the token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="ithinklogistics",
            connectorId=connector_id or "",
            needs_refresh=False,
        )
        if user_data and user_data.token:
            return user_data
        if user_data and user_data.error:
            logger.warning(
                "iThink Logistics credentials failed for user_id=%s: %s",
                user_id,
                user_data.error,
            )
        else:
            logger.warning(
                "iThink Logistics: no credentials for user_id=%s (connect iThink Logistics in Apps)",
                user_id,
            )
        return None
    except Exception as e:
        logger.error("Error getting iThink Logistics credentials: %s", e)
        return None


def _inject_auth(payload: Dict[str, Any], access_token: str, secret_key: str) -> Dict[str, Any]:
    """Inject access_token and secret_key into the payload's data dict."""
    data = payload.get("data", {})
    data["access_token"] = access_token
    data["secret_key"] = secret_key
    payload["data"] = data
    return payload


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=lambda rs: rs.outcome is not None and rs.outcome.failed and not isinstance(rs.outcome.exception(), ValueError),
    reraise=True,
)
async def make_ithinklogistics_request(
    endpoint: str,
    payload: Dict[str, Any],
    access_token: str,
    secret_key: str,
    base_url: str = BASE_URL_ORDERS,
) -> Any:
    """Make an iThink Logistics API request.

    Args:
        endpoint: API path (e.g. /api_v2/order/track.json)
        payload: Request body dict (auth will be injected into payload["data"])
        access_token: iThink access token
        secret_key: iThink secret key
        base_url: Base URL to use (tracking vs orders)
    """
    url = f"{base_url}{endpoint}"
    body = _inject_auth(payload, access_token, secret_key)
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }

    async with _semaphore:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=body)

            if response.status_code == 401:
                detail = (response.text or "").strip()
                logger.warning("iThink Logistics 401 for %s: %s", endpoint, detail[:500])
                raise ValueError(
                    "iThink Logistics authentication failed (401). "
                    "Check your access_token and secret_key. Reconnect iThink Logistics in the Apps section."
                )
            if response.status_code == 403:
                detail = (response.text or "").strip()
                logger.warning("iThink Logistics 403 for %s: %s", endpoint, detail[:500])
                raise ValueError(f"iThink Logistics returned 403 Forbidden for {endpoint}.")
            if response.status_code == 404:
                raise ValueError(f"iThink Logistics endpoint not found: {endpoint}")
            if response.status_code >= 400:
                raise ValueError(
                    f"iThink Logistics API error {response.status_code}: {response.text[:300]}"
                )

            return response.json()


async def get_credentials_from_config(config) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract access_token, secret_key, and optional error from RunnableConfig.

    Returns:
        (access_token, secret_key, error_message)
    """
    configurable = config.get("configurable", {})
    user_id = configurable.get("user_id")
    if not user_id:
        return None, None, "No user_id in configuration"

    all_apps = configurable.get("apps_config", []) or configurable.get("apps", [])
    app_config = next(
        (a for a in all_apps if (a.get("slug") or "").lower() == "ithinklogistics"),
        None,
    )
    connector_id = app_config.get("id") if app_config else None

    creds = await get_ithinklogistics_credentials(user_id, connector_id=connector_id)
    if not creds or not creds.token:
        return (
            None,
            None,
            "iThink Logistics is not connected or credentials could not be loaded. Connect iThink Logistics in the Apps section.",
        )

    access_token = creds.token
    metadata = creds.metadata or {}

    # secret_key is stored encrypted in credentials as secret_key + secret_key_iv
    secret_key_enc = metadata.get("secret_key", "")
    secret_key_iv = metadata.get("secret_key_iv", "")

    if secret_key_enc and secret_key_iv:
        try:
            encryption = TokenEncryption(project_settings().ENCRYPTION_KEY or "")
            secret_key = encryption.decrypt(secret_key_enc, secret_key_iv)
        except Exception as e:
            logger.error("Failed to decrypt iThink secret_key: %s", e)
            return None, None, "Failed to decrypt iThink Logistics secret key."
    else:
        # Fallback: check config metadata (unencrypted)
        config_data = metadata.get("config", {})
        if isinstance(config_data, dict):
            secret_key = config_data.get("secret_key", "")
        else:
            secret_key = ""

    if not secret_key:
        return None, None, "iThink Logistics secret_key not found in credentials."

    return access_token, secret_key, None
