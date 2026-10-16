import logging
import time
from typing import Any

from common.infrastructure.postgres import get_pg

logger = logging.getLogger(__name__)

_CATALOG_CACHE: dict[str, tuple[float, str]] = {}
_CATALOG_CACHE_TTL = 600  # 10 minutes

_CATALOG_QUERY = """
    SELECT
        COALESCE("productType", '') AS product_type,
        COUNT(*) AS product_count,
        MIN("minVariantPrice") AS min_price,
        MAX("maxVariantPrice") AS max_price,
        COALESCE("currencyCode", 'USD') AS currency
    FROM "ShopifyProducts"
    WHERE "appId" = $1 AND status = 'ACTIVE'
    GROUP BY "productType", "currencyCode"
    ORDER BY COUNT(*) DESC
"""


def _format_catalog_context(rows: list[Any]) -> str:
    if not rows:
        return ""

    lines = ["## Store Catalog Overview"]
    for row in rows:
        product_type = str(row["product_type"] or "").strip() or "Other"
        count = int(row["product_count"] or 0)
        currency = str(row["currency"] or "").strip()

        try:
            min_p = float(row["min_price"])
            max_p = float(row["max_price"])
        except (TypeError, ValueError):
            min_p = max_p = None

        if min_p is not None and max_p is not None:
            price = (
                f"{currency} {min_p:.0f}".strip()
                if min_p == max_p
                else f"{currency} {min_p:.0f}–{max_p:.0f}".strip()
            )
            lines.append(f"- {product_type}: {count} products, {price}")
        else:
            lines.append(f"- {product_type}: {count} products")

    return "\n".join(lines)


async def fetch_shopify_catalog_context(connector_id: str) -> str:
    """Return a formatted overview of store product types with counts and price ranges.

    Queries the already-ingested ShopifyProducts table (no live Shopify API call).
    Cached per connector_id for 10 minutes. Returns empty string on failure.
    """
    cached = _CATALOG_CACHE.get(connector_id)
    if cached is not None:
        cached_at, context = cached
        if time.monotonic() - cached_at < _CATALOG_CACHE_TTL:
            logger.info("[catalog] cache hit for connector=%s", connector_id)
            return context

    logger.info("[catalog] fetching ingested catalog for connector=%s", connector_id)
    try:
        pg = get_pg()
        async with pg.acquire() as conn:
            rows = await conn.fetch(_CATALOG_QUERY, connector_id)

        logger.info("[catalog] query returned %d product-type rows for connector=%s", len(rows), connector_id)

        if not rows:
            logger.warning("[catalog] no ACTIVE products found for connector=%s — context will be empty", connector_id)

        context = _format_catalog_context(rows)

        if context:
            logger.info("[catalog] built context (%d chars):\n%s", len(context), context)

        _CATALOG_CACHE[connector_id] = (time.monotonic(), context)
        return context
    except Exception as e:
        logger.warning("[catalog] fetch failed for connector=%s: %s", connector_id, e, exc_info=True)
        return ""
