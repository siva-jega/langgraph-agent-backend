"""Shopify Draft Orders REST API helpers for cart management."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_API_VERSION = "2025-04"


def _extract_numeric_id(gid: str) -> str:
    """Extract numeric ID from a Shopify GID.

    ``gid://shopify/ProductVariant/123456`` → ``123456``
    Already-numeric strings are returned as-is.
    """
    if not gid:
        return ""
    return gid.rsplit("/", 1)[-1]


def _draft_order_url(shop_domain: str, api_version: str, draft_order_id: str = "") -> str:
    base = f"https://{shop_domain}/admin/api/{api_version}/draft_orders"
    return f"{base}/{draft_order_id}.json" if draft_order_id else f"{base}.json"


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": access_token,
    }


async def create_draft_order(
    shop_domain: str,
    access_token: str,
    variant_id: str,
    quantity: int = 1,
    api_version: str = _DEFAULT_API_VERSION,
) -> dict[str, Any]:
    """Create a new Shopify draft order with a single line item.

    Returns dict with ``id`` (str) and ``checkout_url``.
    Raises ``ValueError`` on API errors.
    """
    numeric_variant_id = _extract_numeric_id(variant_id)
    if not numeric_variant_id:
        raise ValueError(f"Cannot extract numeric variant ID from: {variant_id!r}")

    body = {
        "draft_order": {
            "line_items": [
                {"variant_id": int(numeric_variant_id), "quantity": quantity}
            ]
        }
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _draft_order_url(shop_domain, api_version),
            json=body,
            headers=_headers(access_token),
        )

    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code >= 400:
        error = _extract_error(data, response.text)
        raise ValueError(f"Shopify create_draft_order failed ({response.status_code}): {error}")

    draft = data.get("draft_order") or {}
    return {
        "id": str(draft.get("id") or ""),
        "checkout_url": str(draft.get("invoice_url") or ""),
    }


async def get_draft_order(
    shop_domain: str,
    access_token: str,
    draft_order_id: str,
    api_version: str = _DEFAULT_API_VERSION,
) -> dict[str, Any]:
    """Fetch an existing draft order by numeric ID. Returns the ``draft_order`` dict."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            _draft_order_url(shop_domain, api_version, draft_order_id),
            headers=_headers(access_token),
        )

    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code == 404:
        raise LookupError(f"Draft order {draft_order_id!r} not found")
    if response.status_code >= 400:
        raise ValueError(f"Shopify get_draft_order failed ({response.status_code}): {_extract_error(data, response.text)}")

    return data.get("draft_order") or {}


async def add_item_to_draft_order(
    shop_domain: str,
    access_token: str,
    draft_order_id: str,
    variant_id: str,
    quantity: int = 1,
    api_version: str = _DEFAULT_API_VERSION,
) -> dict[str, Any]:
    """Add a line item to an existing draft order (merges qty if same variant).

    Returns dict with ``id`` and ``checkout_url``.
    Raises ``LookupError`` if draft order no longer exists (use to trigger new cart).
    """
    numeric_variant_id = _extract_numeric_id(variant_id)
    if not numeric_variant_id:
        raise ValueError(f"Cannot extract numeric variant ID from: {variant_id!r}")

    existing = await get_draft_order(shop_domain, access_token, draft_order_id, api_version)

    # Completed orders can't be modified — signal caller to start a fresh cart.
    if existing.get("status") == "completed":
        raise LookupError(f"Draft order {draft_order_id!r} is already completed")

    # Build updated line items — merge quantity if same variant already present
    existing_lines: list[dict[str, Any]] = existing.get("line_items") or []
    updated_lines: list[dict[str, Any]] = []
    merged = False
    for line in existing_lines:
        vid = str(line.get("variant_id") or "")
        if vid == numeric_variant_id:
            updated_lines.append({
                "variant_id": int(numeric_variant_id),
                "quantity": int(line.get("quantity") or 1) + quantity,
            })
            merged = True
        else:
            updated_lines.append({
                "variant_id": int(vid),
                "quantity": int(line.get("quantity") or 1),
            })

    if not merged:
        updated_lines.append({"variant_id": int(numeric_variant_id), "quantity": quantity})

    body = {"draft_order": {"line_items": updated_lines}}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.put(
            _draft_order_url(shop_domain, api_version, draft_order_id),
            json=body,
            headers=_headers(access_token),
        )

    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code == 404:
        raise LookupError(f"Draft order {draft_order_id!r} not found")
    if response.status_code >= 400:
        raise ValueError(f"Shopify add_item_to_draft_order failed ({response.status_code}): {_extract_error(data, response.text)}")

    draft = data.get("draft_order") or {}
    return {
        "id": str(draft.get("id") or ""),
        "checkout_url": str(draft.get("invoice_url") or ""),
    }


def _extract_error(data: Any, raw_text: str) -> str:
    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, dict):
            return "; ".join(f"{k}: {v}" for k, v in errors.items())
        if isinstance(errors, str):
            return errors
        msg = data.get("error") or data.get("message")
        if msg:
            return str(msg)
    return raw_text[:300]
