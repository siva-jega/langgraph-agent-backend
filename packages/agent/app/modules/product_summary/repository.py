"""Product Summary module: repository for product DB access."""

import logging
from typing import Any, Dict, Optional

from common.infrastructure import get_pg

logger = logging.getLogger(__name__)


class ProductSummaryRepository:
    """Database access for product summary feature."""

    async def get_shopify_product(
        self, product_id: str, workspace_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a Shopify product by its internal UUID or externalId + workspaceId."""
        try:
            pg = get_pg()
            async with pg.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        sp.id,
                        sp."externalId",
                        sp.title,
                        sp.description,
                        sp."descriptionHtml",
                        sp."aiSummary",
                        sp.status,
                        sp.handle,
                        sp."productType",
                        sp."onlineStoreUrl",
                        sp."minVariantPrice"::text AS "minVariantPrice",
                        sp."maxVariantPrice"::text AS "maxVariantPrice",
                        sp."currencyCode",
                        sp.images,
                        sp."workspaceId"
                    FROM "ShopifyProducts" sp
                    WHERE (sp.id = $1 OR sp."externalId" = $1)
                      AND sp."workspaceId" = $2
                    LIMIT 1
                    """,
                    product_id,
                    workspace_id,
                )
                if row:
                    result = dict(row)
                    # Fetch variants
                    variants = await conn.fetch(
                        """
                        SELECT
                            sv.id,
                            sv."externalId",
                            sv.title,
                            sv."displayName",
                            sv.price::text AS price,
                            sv."compareAtPrice"::text AS "compareAtPrice",
                            sv."availableForSale",
                            sv.sku,
                            sv.image
                        FROM "ShopifyVariants" sv
                        WHERE sv."productId" = $1
                        ORDER BY sv.position
                        """,
                        result["id"],
                    )
                    result["variants"] = [dict(v) for v in variants]
                    return result
                return None
        except RuntimeError:
            logger.warning("DATABASE_URL not set; cannot fetch Shopify product")
            return None
        except Exception as e:
            logger.error("Error fetching Shopify product: %s", e, exc_info=True)
            return None

    async def get_woocommerce_product(
        self, product_id: str, workspace_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a WooCommerce product by its internal UUID or productId + workspaceId."""
        try:
            pg = get_pg()
            async with pg.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        p.id,
                        p."productId",
                        p.name,
                        p.slug,
                        p.permalink,
                        p.description,
                        p."aiSummary",
                        p.price::text AS price,
                        p."stockStatus",
                        p.purchasable,
                        p."averageRating"::text AS "averageRating",
                        p."ratingCount",
                        p."totalSales",
                        p.categories,
                        p.images,
                        p.attributes,
                        p."workspaceId"
                    FROM "WooCommerceProduct" p
                    WHERE (p.id = $1 OR p."productId"::text = $1)
                      AND p."workspaceId" = $2
                    LIMIT 1
                    """,
                    product_id,
                    workspace_id,
                )
                if row:
                    result = dict(row)
                    # Fetch variants
                    variants = await conn.fetch(
                        """
                        SELECT
                            v.id,
                            v.name,
                            v.permalink,
                            v.price::text AS price,
                            v."regularPrice"::text AS "regularPrice",
                            v."salePrice"::text AS "salePrice",
                            v."onSale",
                            v."stockStatus",
                            v."stockQuantity",
                            v.purchasable,
                            v.attributes,
                            v.image
                        FROM "WooCommerceProductVariant" v
                        WHERE v."productId" = $1
                        """,
                        result["id"],
                    )
                    result["variants"] = [dict(v) for v in variants]
                    return result
                return None
        except RuntimeError:
            logger.warning("DATABASE_URL not set; cannot fetch WooCommerce product")
            return None
        except Exception as e:
            logger.error("Error fetching WooCommerce product: %s", e, exc_info=True)
            return None

    async def update_shopify_summary(
        self, product_id: str, workspace_id: str, summary: str
    ) -> bool:
        """Save AI summary to a Shopify product."""
        try:
            pg = get_pg()
            async with pg.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE "ShopifyProducts"
                    SET "aiSummary" = $1, "updatedAt" = NOW()
                    WHERE (id = $2 OR "externalId" = $2)
                      AND "workspaceId" = $3
                    """,
                    summary,
                    product_id,
                    workspace_id,
                )
                return "UPDATE" in result
        except Exception as e:
            logger.error("Error updating Shopify summary: %s", e, exc_info=True)
            return False

    async def update_woocommerce_summary(
        self, product_id: str, workspace_id: str, summary: str
    ) -> bool:
        """Save AI summary to a WooCommerce product."""
        try:
            pg = get_pg()
            async with pg.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE "WooCommerceProduct"
                    SET "aiSummary" = $1, "updatedAt" = NOW()
                    WHERE (id = $2 OR "productId"::text = $2)
                      AND "workspaceId" = $3
                    """,
                    summary,
                    product_id,
                    workspace_id,
                )
                return "UPDATE" in result
        except Exception as e:
            logger.error("Error updating WooCommerce summary: %s", e, exc_info=True)
            return False
