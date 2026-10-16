import hashlib
import hmac
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from common.modules._protocols import ApiClientLike, RedisLike
from common.modules.settings_protocols import SupportsTokenSettings
from common.modules.token.credentials import (
    credentials_from_cached,
    extract_credentials_from_db_response,
    extract_shopify_domain_from_db_response,
)
from common.modules.token.encryption import TokenEncryption
from common.modules.token.models import TokenResponse
from common.modules.token.repository import TokenRepository

logger = logging.getLogger(__name__)

_TOKEN_KEYS = ("accessToken", "token", "access_token")


class TokenService:
    """Get user token: cache → DB → decrypt; refresh via NestJS when needed."""

    def __init__(
        self,
        settings: SupportsTokenSettings,
        redis_service: Optional[RedisLike] = None,
        api_service: Optional[ApiClientLike] = None,
    ):
        self._s = settings
        self._redis = redis_service
        self._api = api_service
        self._repo = TokenRepository(redis_service)
        self._encryption: Optional[TokenEncryption] = None
        self._init = False

    async def connect(self) -> None:
        if self._init:
            return
        if not self._s.ENCRYPTION_KEY:
            raise ValueError("ENCRYPTION_KEY is required for token service")
        self._encryption = TokenEncryption(self._s.ENCRYPTION_KEY)
        self._init = True

    def _ensure_api(self) -> None:
        if not self._api:
            raise RuntimeError("API service not provided to TokenService")

    async def _refresh_token(
        self, user_id: str, connector_id: str, connector_name: str
    ) -> str:
        self._ensure_api()
        if not self._s.NESTJS_URL:
            raise ValueError(
                "Token refresh requires NESTJS_URL. Set in config or implement OAuth here."
            )
        name = connector_name.lower()
        if name == "googledrive":
            name = "google-drive"
        elif name == "googlecalendar":
            name = "google-calendar"

        url = f"{self._s.NESTJS_URL}/apps/{name}/refresh-token"
        payload = {"userId": user_id, "connectorId": connector_id}

        try:
            resp = await self._api.post_json(url, payload)
            if not resp or not isinstance(resp, dict):
                raise ValueError(
                    f"No valid response from refresh endpoint for {name} (ID: {connector_id})"
                )

            data = (
                resp.get("result", resp)
                if isinstance(resp.get("result"), dict)
                else resp
            )
            for k in _TOKEN_KEYS:
                if k in data:
                    return data[k]
            raise ValueError(f"Unexpected refresh response: {resp}")
        except Exception as e:
            logger.error("Refresh token error: %s", e)
            raise ValueError(f"Failed to refresh token: {e}") from e

    async def get_user_data(
        self,
        user_id: str,
        connector_name: str,
        connectorId: str,
        needs_refresh: bool = True,
        min_expiry_minutes: int = 3,
    ) -> TokenResponse:
        """Return token + metadata. Tries cache, then DB; decrypts; refreshes if needed."""
        if not self._init:
            await self.connect()
        try:
            data = await self._repo.get_app_credentials(
                user_id, connectorId, connector_name
            )
            logger.info(
                "Fetched credentials for %s/%s from cache/DB: %s",
                user_id,
                connector_name,
                data,
            )
            if not data:
                raise ValueError(
                    f"No credentials for {connector_name} (ID: {connectorId}) in cache or database"
                )

            credentials = None
            if "credentials" in data and isinstance(data["credentials"], dict):
                credentials = extract_credentials_from_db_response(data, connector_name)
                await self._repo.cache_credentials(user_id, connector_name, credentials)
            else:
                credentials = credentials_from_cached(data, connector_name)

            if not credentials:
                raise ValueError("Could not extract valid credentials from data")

            domain = extract_shopify_domain_from_db_response(data)
            if domain:
                credentials["shop_domain"] = domain
            elif connector_name.lower() == "shopify":
                # DB may store shop hostname in credentials.shop
                credentials["shop_domain"] = credentials.get("shop") or ""

            # Merge connector config (shop name, currency, timezone, etc.) into metadata
            connector_config = data.get("config") or {}
            if isinstance(connector_config, dict):
                credentials["config"] = connector_config

            expiry = None
            is_refreshed = False
            token = None

            if "expiry_date" in credentials and credentials["expiry_date"]:
                raw = credentials["expiry_date"]
                ts = float(raw) if isinstance(raw, Decimal) else float(raw)
                expiry = datetime.fromtimestamp(ts / 1000)

                if needs_refresh:
                    mins_left = (expiry - datetime.now()).total_seconds() / 60
                    if mins_left < min_expiry_minutes:
                        try:
                            token = await self._refresh_token(
                                user_id, connectorId, connector_name
                            )
                            await self._repo.delete_credentials(user_id, connector_name)
                            is_refreshed = True
                        except Exception as err:
                            logger.warning(
                                "Token refresh failed for %s: %s. Using existing.",
                                connectorId,
                                err,
                            )

            if not token:
                acc_enc = credentials.get("access_token")
                acc_iv = credentials.get("access_token_iv")
                tok_enc = credentials.get("token")
                tok_iv = credentials.get("token_iv")
                if acc_enc and acc_iv:
                    token = self._encryption.decrypt(acc_enc, acc_iv)
                elif tok_enc and tok_iv:
                    token = self._encryption.decrypt(tok_enc, tok_iv)
                else:
                    token = credentials.get("access_token") or credentials.get("token")

            return TokenResponse(
                token=token or "",
                expires_at=expiry,
                is_refreshed=is_refreshed,
                metadata=credentials,
            )

        except ValueError as e:
            logger.error("Token error %s/%s: %s", user_id, connector_name, e)
            return TokenResponse(
                token="", metadata={}, expires_at=datetime.now(), error=str(e)
            )
        except Exception as e:
            logger.exception("Unexpected token error %s/%s", user_id, connector_name)
            return TokenResponse(
                token="", metadata={}, expires_at=datetime.now(), error=str(e)
            )

    def generate_signature(self, payload: str, webhook_secret: str) -> str:
        """HMAC-SHA256 hex digest for webhook verification."""
        return hmac.new(
            webhook_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_signature(
        self, payload: str, signature: str, webhook_secret: str
    ) -> bool:
        return hmac.compare_digest(
            self.generate_signature(payload, webhook_secret), signature
        )
