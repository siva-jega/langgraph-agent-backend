"""Shopify implementation of EcommerceProvider."""

from __future__ import annotations

import logging
from typing import Any

from app.modules.channels.ecommerce.base import EcommerceProvider
from app.modules.channels.repository import (
    fetch_shopify_product,
    fetch_shopify_product_handles,
    get_shopify_cart_by_session,
    upsert_shopify_cart,
)
from common.tools.providers.shopify.draft_orders import (
    add_item_to_draft_order,
    create_draft_order,
)
from common.tools.providers.shopify.utils import get_shopify_credentials

logger = logging.getLogger(__name__)


class ShopifyProvider(EcommerceProvider):
    source = "SHOPIFY"

    # ------------------------------------------------------------------ #
    # Credentials                                                         #
    # ------------------------------------------------------------------ #

    async def get_credentials(self, agent_config: dict) -> Any | None:
        user_id = agent_config.get("user_id", "")
        apps = agent_config.get("config", {}).get("apps", [])
        connector_id = next(
            (a["id"] for a in apps if "shopify" in (a.get("slug") or "").lower()), None
        )
        if not user_id or not connector_id:
            return None
        return await get_shopify_credentials(user_id, connector_id)

    # ------------------------------------------------------------------ #
    # Products                                                            #
    # ------------------------------------------------------------------ #

    async def fetch_product(self, product_id: str) -> dict | None:
        return await fetch_shopify_product(product_id)

    async def fetch_product_handles(self, product_ids: list[str]) -> dict[str, str]:
        return await fetch_shopify_product_handles(product_ids)

    # ------------------------------------------------------------------ #
    # Cart                                                                #
    # ------------------------------------------------------------------ #

    async def get_cart(self, session_id: str) -> dict | None:
        return await get_shopify_cart_by_session(session_id)

    async def upsert_cart(
        self,
        *,
        session_id: str,
        workspace_id: str,
        creds: Any,
        existing_cart: dict | None,
        variant_id: str,
    ) -> dict:
        shop_domain = creds.metadata.get("shop_domain") or creds.metadata.get("shop") or ""
        access_token = creds.token or ""

        if existing_cart and existing_cart.get("shopifyDraftOrderId"):
            draft_order_id = str(existing_cart["shopifyDraftOrderId"])
            try:
                result = await add_item_to_draft_order(
                    shop_domain, access_token, draft_order_id, variant_id
                )
            except Exception:
                # Existing cart is completed, deleted, or otherwise unusable — start fresh.
                logger.info(
                    "Existing draft order %s unusable, creating new cart", draft_order_id
                )
                result = await create_draft_order(shop_domain, access_token, variant_id)
        else:
            result = await create_draft_order(shop_domain, access_token, variant_id)

        return {
            "cart_id": result.get("id", ""),
            "checkout_url": result.get("checkout_url", ""),
        }

    async def save_cart(
        self,
        *,
        session_id: str,
        workspace_id: str,
        cart_id: str,
        checkout_url: str,
    ) -> None:
        await upsert_shopify_cart(session_id, workspace_id, cart_id, checkout_url)

    # ------------------------------------------------------------------ #
    # Source extraction                                                   #
    # ------------------------------------------------------------------ #

    def extract_products_from_sources(self, sources: list[dict]) -> list[dict]:
        if not isinstance(sources, list):
            return []
        products: list[dict] = []
        seen_ids: set[str] = set()
        for source in sources:
            if not isinstance(source, dict):
                continue
            data = source.get("data")
            items = data if isinstance(data, list) else None
            if items is None and all(k in source for k in ("id", "name", "product_url")):
                items = [source]
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                product_id = str(item.get("id") or "").strip()
                product_name = str(item.get("name") or "").strip()
                if not product_id or not product_name or product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                products.append(item)
        return products

    def extract_order_from_sources(self, sources: list[dict]) -> dict | None:
        if not isinstance(sources, list):
            return None
        for source in sources:
            if not isinstance(source, dict):
                continue
            tool_name = str(source.get("tool") or source.get("name") or "").strip().lower()
            if tool_name not in {"get order status", "shopify"}:
                continue
            data = source.get("data")
            if (
                isinstance(data, dict)
                and str(data.get("id") or "").strip()
                and str(data.get("name") or "").strip()
            ):
                return data
        return None
