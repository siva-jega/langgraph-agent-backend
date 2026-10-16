"""WooCommerce provider utils: credentials, REST API client, AI product validation, process_retrieved_products."""
import base64
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from common.llm_utils import apply_openai_service_tier
from common.project_settings import project_settings
from common.db_queries import load_query
from common.infrastructure import get_pg
from common.modules.retrieval import RetrievalResponse
from common.modules.token import TokenResponse
from common.integration import get_token
from common.modules.token.encryption import TokenEncryption
from common.tools.providers.woocommerce.models import (
    ProductValidationResult,
    WooCommerceProduct,
    WooCommerceVariant,
)

logger = logging.getLogger(__name__)

_encryption: Optional[TokenEncryption] = None


def _get_encryption() -> TokenEncryption:
    global _encryption
    if _encryption is None:
        _encryption = TokenEncryption(project_settings().ENCRYPTION_KEY)
    return _encryption


async def get_woocommerce_credentials(
    user_id: str, connector_id: str | None = None
) -> Optional[Dict[str, str]]:
    """Return decrypted WooCommerce credentials: store_url, consumer_key, consumer_secret.

    The token service doesn't know how to decrypt consumer_secret (it only
    handles access_token), so we decrypt it here using the shared encryption key.
    """
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="woocommerce",
            connectorId=connector_id,
            needs_refresh=False,
        )
        if not user_data or user_data.error:
            return None

        meta = user_data.metadata or {}
        consumer_key = meta.get("consumer_key", "")
        enc_secret = meta.get("consumer_secret", "")
        secret_iv = meta.get("consumer_secret_iv", "")
        store_url = meta.get("store_url") or meta.get("config", {}).get("storeUrl", "")

        if not consumer_key or not enc_secret or not secret_iv or not store_url:
            logger.error("WooCommerce credentials incomplete for user %s", user_id)
            return None

        consumer_secret = _get_encryption().decrypt(enc_secret, secret_iv)

        return {
            "store_url": store_url.rstrip("/"),
            "consumer_key": consumer_key,
            "consumer_secret": consumer_secret,
        }
    except Exception as e:
        logger.error("Error getting WooCommerce credentials: %s", e)
        return None


async def make_woocommerce_request(
    store_url: str,
    consumer_key: str,
    consumer_secret: str,
    method: str,
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Any:
    """Make an authenticated WooCommerce REST API request (Basic Auth)."""
    url = f"{store_url}/wp-json/wc/v3/{endpoint.lstrip('/')}"
    auth = httpx.BasicAuth(consumer_key, consumer_secret)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method, url, auth=auth, params=params, json=json_body,
        )

        if response.status_code == 401:
            raise ValueError("WooCommerce authentication failed. Check your consumer key and secret.")
        if response.status_code == 404:
            raise ValueError(f"WooCommerce resource not found: {endpoint}")
        if response.status_code >= 400:
            body = response.text[:300]
            raise ValueError(f"WooCommerce API error {response.status_code}: {body}")

        return response.json()


async def validate_products_with_ai(
    user_query: str,
    products: List[WooCommerceProduct],
    artifacts: List[Dict[str, Any]],
    min_relevance_score: float = 0.5,
) -> Tuple[List[WooCommerceProduct], List[Dict[str, Any]]]:
    """Filter and rank products by relevance to user query using LLM."""
    if not products:
        return products[:5], artifacts[:5]

    settings = project_settings()
    if not getattr(settings, "ECOMMERCE_PRODUCT_AI_VALIDATION_ENABLED", True):
        logger.info(
            "WooCommerce product AI validation disabled (ECOMMERCE_PRODUCT_AI_VALIDATION_ENABLED=false); using top candidates"
        )
        return products[:5], artifacts[:5]

    try:
        model_name = (
            getattr(settings, "SHOPIFY_PRODUCT_VALIDATION_MODEL", None)
            or getattr(settings, "DEFAULT_AGENT_MODEL", None)
        )
        llm = ChatOpenAI(
            **apply_openai_service_tier(
                {
                    "model": model_name,
                    "api_key": settings.OPENAI_API_KEY,
                    "base_url": settings.OPENAI_API_BASE,
                },
                settings,
            )
        )
        model = llm.with_structured_output(ProductValidationResult)
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """You are a product relevance expert. Evaluate whether products match a user's search query.
                    For each product: 1) Is it relevant? 2) Score 0.0-1.0. 3) Brief reasoning.
                    Consider name, description, attributes, price, and stock.""",
                ),
                (
                    "user",
                    "User Query: {query}\n\nProducts:\n{products_info}\n\nEvaluate each product.",
                ),
            ]
        )
        products_info = []
        for i, product in enumerate(products):
            variants_str = ""
            if product.variants:
                variants_str = "\n".join(
                    f"- {v.name or ''} regular price={v.regular_price} sale price={v.sale_price} stock={v.stock_status}"
                    for v in product.variants[:10]
                )
            products_info.append(
                f"Product {i+1}: id={product.id} name={product.name} "
                f"price={product.price or 'N/A'} stock_status={product.stock_status or 'N/A'} "
                f"on_sale={product.on_sale} variants: {variants_str or 'N/A'}"
            )
        chain = prompt | model
        result: ProductValidationResult = await chain.ainvoke(
            {"query": user_query, "products_info": "\n\n".join(products_info)},
            config={"callbacks": []},
        )
        relevance_map = {s.product_id: s for s in result.evaluated_products}
        filtered = []
        for product, artifact in zip(products, artifacts):
            rel = relevance_map.get(product.id)
            if (
                rel
                and rel.is_relevant
                and rel.relevance_score >= min_relevance_score
            ):
                product.reason_of_selection = rel.reasoning
                filtered.append((product, artifact, rel.relevance_score))
        filtered.sort(key=lambda x: x[2], reverse=True)
        filtered = filtered[:5]
        return [f[0] for f in filtered], [f[1] for f in filtered]
    except Exception as e:
        logger.error("Error in AI validation: %s", e)
        return products[:5], artifacts[:5]


def _extract_item_ids(
    retrieved_nodes: List[RetrievalResponse],
) -> List[str]:
    """Extract unique item_ids from retrieval metadata, preserving order."""
    seen: set = set()
    item_ids: List[str] = []
    for node in retrieved_nodes:
        try:
            if not node.metadata:
                continue
            if isinstance(node.metadata, str):
                meta = json.loads(node.metadata)
            elif isinstance(node.metadata, dict):
                meta = node.metadata
            else:
                continue
            item_id = meta.get("item_id", "")
            if item_id and item_id not in seen:
                seen.add(item_id)
                item_ids.append(item_id)
        except (json.JSONDecodeError, Exception):
            continue
    return item_ids


async def process_retrieved_products(
    retrieved_nodes: List[RetrievalResponse],
) -> Tuple[List[WooCommerceProduct], List[Dict[str, Any]]]:
    """Extract item_ids from retrieval metadata, fetch full product+variant data from Postgres.

    Returns the same (products, artifacts) shape as before.
    """
    item_ids = _extract_item_ids(retrieved_nodes)
    if not item_ids:
        return [], []

    query = load_query("woocommerce_products.sql")
    pg = get_pg()
    async with pg.acquire() as conn:
        rows = await conn.fetch(query, item_ids)

    # Build a map keyed by id so we can preserve retrieval order
    row_map: Dict[str, Any] = {row["id"]: row for row in rows}

    products: List[WooCommerceProduct] = []
    artifacts: List[Dict[str, Any]] = []

    for item_id in item_ids:
        row = row_map.get(item_id)
        if not row:
            continue

        # Parse variants JSON array
        variants_raw = row["variants"]
        if isinstance(variants_raw, str):
            variants_raw = json.loads(variants_raw)

        variants = []
        for v in variants_raw:
            stock_qty = v.get("stockQuantity")
            if stock_qty is not None:
                try:
                    stock_qty = int(stock_qty)
                except (TypeError, ValueError):
                    stock_qty = None
            variants.append(
                WooCommerceVariant(
                    id=str(v.get("id", "")),
                    name=v.get("name"),
                    permalink=v.get("permalink"),
                    regular_price=v.get("regularPrice"),
                    sale_price=v.get("salePrice") or None,
                    stock_status=v.get("stockStatus"),
                    stock_quantity=stock_qty,
                    purchasable=v.get("purchasable"),
                    attributes=v.get("attributes"),
                    image=v["image"].get("src") or v["image"].get("url") if isinstance(v.get("image"), dict) else v.get("image"),
                )
            )

        # Extract image URLs from images JSON column
        images_raw = row["images"]
        if isinstance(images_raw, str):
            images_raw = json.loads(images_raw)
        image_urls = None
        if images_raw and isinstance(images_raw, list):
            image_urls = [
                img.get("src") or img.get("url") for img in images_raw if isinstance(img, dict)
            ]
            image_urls = [u for u in image_urls if u]

        product = WooCommerceProduct(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            permalink=row["permalink"],
            price=row["price"],
            stock_status=row["stockStatus"],
            purchasable=row["purchasable"],
            image_urls=image_urls or None,
            variants=variants if variants else None,
        )
        products.append(product)

        # Artifact mirrors the DB row as a plain dict
        artifacts.append({
            "item_id": row["id"],
            "name": row["name"],
            "slug": row["slug"],
            "permalink": row["permalink"],
            "price": row["price"],
            "stockStatus": row["stockStatus"],
            "purchasable": row["purchasable"],
            "image_urls": image_urls,
            "variants": variants_raw,
        })

    return products, artifacts
