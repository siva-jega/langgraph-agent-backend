"""Cal.com API client: credentials and HTTP requests."""

import asyncio
import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from common.project_settings import project_settings
from common.integration import get_token

logger = logging.getLogger(__name__)

CALCOM_API_BASE = "https://api.cal.com"
# Per Cal.com docs: event-types 2024-06-14, schedules 2024-06-11, slots 2024-09-04, bookings 2026-02-25.
CALCOM_API_VERSION = "2026-02-25"
CALCOM_API_VERSION_EVENT_TYPES = "2024-06-14"
CALCOM_API_VERSION_SCHEDULES = "2024-06-11"
CALCOM_API_VERSION_SLOTS = "2024-09-04"
CALCOM_API_VERSION_BOOKINGS = "2026-02-25"
CALCOM_API_VERSION_BOOKINGS_READ = "2024-08-13"
_semaphore = asyncio.Semaphore(5)


async def get_calcom_credentials(user_id: str, connector_id: str | None = None):
    """Get Cal.com OAuth credentials from the token service (DB + optional refresh)."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="calcom",
            connectorId=connector_id or "",
            needs_refresh=True,
        )
        if user_data and user_data.token:
            return user_data
        if user_data and user_data.error:
            logger.warning(
                "Cal.com credentials failed for user_id=%s connector_id=%s: %s",
                user_id,
                connector_id,
                user_data.error,
            )
        else:
            logger.warning(
                "Cal.com: no credentials for user_id=%s connector_id=%s (connect Cal.com in Apps; ensure ai-backend DATABASE_URL and ENCRYPTION_KEY match user-backend)",
                user_id,
                connector_id,
            )
        return None
    except Exception as e:
        logger.error("Error getting Cal.com credentials: %s", e)
        return None


def _default_headers(
    access_token: str,
    api_version: str | None = None,
) -> dict:
    """Build headers matching the working curl: Authorization Bearer + Content-Type + cal-api-version.
    No x-cal-client-id / x-cal-secret-key — those are for Cal.com "Platform" OAuth apps only.
    Developer OAuth apps (app.cal.com/settings/developer/oauth) get 401 if those headers are sent."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "cal-api-version": api_version or CALCOM_API_VERSION,
    }


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=lambda rs: rs.outcome is not None and rs.outcome.failed and not isinstance(rs.outcome.exception(), ValueError),
    reraise=True,
)
async def make_calcom_request(
    access_token: str,
    method: str,
    endpoint: str,
    api_version: str | None = None,
    skip_auth: bool = False,
    **kwargs: Any,
) -> Any:
    """Make a Cal.com API v2 request.
    Cal.com blocks some endpoints for third-party OAuth tokens (403 "insufficient_scope").
    For those public endpoints (e.g. GET /v2/event-types with ?username=, GET /v2/slots),
    set skip_auth=True to omit the Authorization header."""
    url = endpoint if endpoint.startswith("http") else f"{CALCOM_API_BASE}{endpoint}"
    if skip_auth:
        headers = {
            "Content-Type": "application/json",
            "cal-api-version": api_version or CALCOM_API_VERSION,
        }
    else:
        headers = _default_headers(access_token, api_version=api_version)
    if "headers" in kwargs:
        headers = {**headers, **kwargs.pop("headers")}

    async with _semaphore:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(method, url, headers=headers, **kwargs)

            if response.status_code == 401:
                detail = (response.text or "").strip()
                logger.warning(
                    "Cal.com 401 for %s %s: %s",
                    method, endpoint, detail[:500],
                )
                msg = (
                    "Cal.com authentication failed (401). The token may be expired. "
                    "Fix: Reconnect Cal.com in the Apps section to get a fresh token."
                )
                if detail:
                    msg += f" Response: {detail[:350]}"
                raise ValueError(msg)
            if response.status_code == 403:
                detail = (response.text or "").strip()
                logger.warning(
                    "Cal.com 403 for %s %s: %s",
                    method, endpoint, detail[:500],
                )
                msg = (
                    "Cal.com returned 403 Forbidden. "
                    "(1) Ensure your OAuth client at https://app.cal.com/settings/developer/oauth is approved. "
                    "(2) Disconnect and reconnect Cal.com in Apps."
                )
                if detail:
                    msg += f" Response: {detail[:400]}"
                raise ValueError(msg)
            if response.status_code == 404:
                raise ValueError(f"Cal.com resource not found: {endpoint}")
            if response.status_code >= 400:
                raise ValueError(f"Cal.com API error {response.status_code}: {response.text[:200]}")

            if response.status_code == 204:
                return {}
            return response.json()
