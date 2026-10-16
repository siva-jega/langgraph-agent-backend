"""
Helpers to extract credentials and connector-specific data from DB/cache responses.

Used by TokenService when loading from NestJS user-backend or Redis cache.
"""

import json
from typing import Any, Dict, Optional


def extract_credentials_from_db_response(
    db_response: Dict[str, Any], connector_name: str
) -> Dict[str, Any]:
    """Return credentials dict from DB response. Raises ValueError if missing."""
    if "credentials" in db_response and isinstance(db_response["credentials"], dict):
        return db_response["credentials"]
    if "access_token" in db_response:
        return db_response
    raise ValueError(f"No credentials in database response for {connector_name}")


def extract_shopify_domain_from_db_response(
    db_response: Dict[str, Any],
) -> Optional[str]:
    """Extract myshopifyDomain from config. Returns None if absent or invalid."""
    config = db_response.get("config") or {}

    # Config is a flat dict with myshopifyDomain/domain directly on it
    domain = config.get("myshopifyDomain") or config.get("domain")
    if domain:
        return domain

    # Legacy fallback: nested under config.metadata JSON string
    meta_str = config.get("metadata")
    if not meta_str:
        return None
    try:
        meta = json.loads(meta_str)
        return meta.get("myshopifyDomain") or meta.get("domain")
    except json.JSONDecodeError:
        return None


def credentials_from_cached(cached: Dict[str, Any], connector_name: str) -> Dict[str, Any]:
    """Return credentials dict from cached payload. Raises ValueError if invalid."""
    if "credentials" in cached and isinstance(cached["credentials"], dict):
        return cached["credentials"]
    if "access_token" in cached:
        return cached
    raise ValueError(f"Unexpected cached data structure for {connector_name}")
