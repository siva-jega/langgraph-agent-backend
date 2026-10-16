import re
import html
import httpx
import json
import ast
from typing import Optional, List, Dict, Any, cast
from common.integration import get_token
from common.modules.token import TokenResponse
from common.modules.retrieval import RetrievalResponse
from common.llm_utils import apply_openai_service_tier
from common.project_settings import project_settings
from .models import ShopifyProduct, ProductRelevanceScore, ProductValidationResult, QueryTranslation
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
import logging
import time

logger = logging.getLogger(__name__)

_SHOPIFY_CREDS_CACHE: dict[tuple[str, str], tuple[float, TokenResponse]] = {}

# Module-level cached models — built once on first call, reused forever.
_VALIDATION_STRUCT_MODEL: Any = None
_TRANSLATION_STRUCT_MODEL: Any = None

# Pre-built constant system message so nothing is constructed per-call.
_VALIDATION_SYSTEM_MSG = SystemMessage(
    content=(
        "You are a product relevance scorer. For each product return product_id, "
        "is_relevant, and relevance_score (0.0–1.0). Match strictly against the user "
        "query: product name, description, price, and variant attributes.\n\n"
        "Annotations: the product line carries [IN] or [OOS] at the product level. "
        "Each listed variant also carries [IN] or [OOS] and may include qty=N. A line "
        "may say '(no variants listed)' for products with no real options (single "
        "default variant) or where variant data is missing.\n\n"
        "Attribute-match rules (CRITICAL):\n"
        "- **Size is exact, never approximate.** If the user asks for size N (e.g. "
        "  'size 13', '13 UK', 'large', 'XXL'), a variant matches only when its size "
        "  token equals N exactly. Size 12 does NOT match a request for size 13; "
        "  'large' does NOT match a request for 'medium'. Numeric and lettered sizes "
        "  never substitute for each other.\n"
        "- **Color, gender, material, fit, occasion are also exact.** If the user "
        "  asks for 'white' and the product name or description names a different "
        "  specific color ('Black', 'Navy', 'Mehroon', 'Grey'), the product is a "
        "  mismatch — set `is_relevant=false` and score ≤ 0.3. Same for men's vs "
        "  women's, cotton vs polyester, formal vs casual, etc. Do NOT treat the "
        "  product name's color as a soft signal that can be averaged against other "
        "  matching terms.\n"
        "- If the user specifies one or more attributes, identify the listed "
        "  variant(s) (and product-level name/description) that match those "
        "  attributes. The product is relevant ONLY when every user-specified "
        "  attribute is satisfied AND at least one matching variant is [IN].\n"
        "- If no listed variant matches the user's specified attributes (e.g. user "
        "  asks for size 13 and only 5–12 UK are listed; or user asks for a color the "
        "  product doesn't offer), that's a product mismatch — `is_relevant=false`, "
        "  score ≤ 0.3. This is a hard exclude, not a soft penalty.\n\n"
        "Availability rules (CRITICAL):\n"
        "- If every variant matching the user's requested attributes is [OOS], set "
        "  `is_relevant=false` and score ≤ 0.3 — even if other (non-matching) variants "
        "  of the same product are in stock.\n"
        "- If the user did NOT specify attributes, the product is relevant only when "
        "  at least one variant is [IN] — or, when '(no variants listed)' is shown, "
        "  the product-level tag is [IN]. Fully-OOS products (every variant [OOS], or "
        "  product-level [OOS] when no variants are listed) must be marked "
        "  `is_relevant=false`. Do not recommend products the customer cannot buy.\n"
        "- For products with '(no variants listed)' and the user specifies attributes, "
        "  treat attribute matching as a name/description match against the product as "
        "  a whole — variant-level matching does not apply.\n"
        "- When variant `qty` is shown, treat qty ≤ 2 as 'low stock' — still in stock "
        "  but slightly downrank vs. comfortably-stocked alternatives.\n"
        "- If neither variant tags nor product-level tag are present, fall back to "
        "  product-level `inv=N` if shown (inv > 0 → assume in stock; inv == 0 → OOS); "
        "  if nothing is available at all, default to in-stock to avoid over-filtering.\n\n"
        "When in doubt about whether a user-specified attribute is satisfied, prefer "
        "is_relevant=false. Returning fewer accurate matches is better than returning "
        "wrong ones."
    )
)

_TRANSLATION_SYSTEM_MSG = SystemMessage(
    content=(
        "You translate Shopify product-search queries into English so they match an "
        "English-indexed catalog. Translate each of the four input fields into natural, "
        "search-friendly English. Preserve brand names, product codes, sizes, and "
        "numeric attributes verbatim. If a field is already English, return it unchanged. "
        "Never invent details. Set `detected_language` to the ISO 639-1 code of the "
        "source language."
    )
)


def _get_validation_model() -> Any:
    global _VALIDATION_STRUCT_MODEL
    if _VALIDATION_STRUCT_MODEL is None:
        settings = project_settings()
        model_name = (
            getattr(settings, "SHOPIFY_PRODUCT_VALIDATION_MODEL", None)
            or getattr(settings, "DEFAULT_AGENT_MODEL", None)
        )
        llm = ChatOpenAI(
            **apply_openai_service_tier(
                {
                    "model": model_name,
                    "api_key": settings.OPENAI_API_KEY,  # type: ignore[arg-type]
                    "base_url": settings.OPENAI_API_BASE,
                    "temperature": 0,
                },
                settings,
            )
        )
        _VALIDATION_STRUCT_MODEL = llm.with_structured_output(ProductValidationResult)
    return _VALIDATION_STRUCT_MODEL


def _get_translation_model() -> Any:
    global _TRANSLATION_STRUCT_MODEL
    if _TRANSLATION_STRUCT_MODEL is None:
        settings = project_settings()
        model_name = (
            getattr(settings, "SHOPIFY_QUERY_TRANSLATION_MODEL", None)
            or getattr(settings, "SHOPIFY_PRODUCT_VALIDATION_MODEL", None)
            or getattr(settings, "DEFAULT_AGENT_MODEL", None)
        )
        llm = ChatOpenAI(
            **apply_openai_service_tier(
                {
                    "model": model_name,
                    "api_key": settings.OPENAI_API_KEY,  # type: ignore[arg-type]
                    "base_url": settings.OPENAI_API_BASE,
                    "temperature": 0,
                },
                settings,
            )
        )
        _TRANSLATION_STRUCT_MODEL = llm.with_structured_output(QueryTranslation)
    return _TRANSLATION_STRUCT_MODEL


def is_english_language(original_language: str | None) -> bool:
    """Return True when `original_language` indicates English (or is missing)."""
    if not original_language:
        return True
    code = original_language.strip().lower()
    return code in {"en", "eng", "english"} or code.startswith("en-") or code.startswith("en_")


async def translate_search_queries_to_english(
    *,
    original_user_query: str,
    query_for_dense_search: str,
    query_for_sparse_search: str,
    search_title: str,
) -> QueryTranslation:
    """Translate the four Shopify search-query fields to English via a single LLM call.

    Caller is responsible for gating on `original_language`; this function always invokes
    the translation model. On LLM failure it logs and returns the inputs unchanged so the
    downstream search still runs."""
    user_msg = HumanMessage(
        content=(
            "Translate the following Shopify search fields into English.\n\n"
            f"original_user_query: {original_user_query}\n"
            f"query_for_dense_search: {query_for_dense_search}\n"
            f"query_for_sparse_search: {query_for_sparse_search}\n"
            f"search_title: {search_title}"
        )
    )
    try:
        model = _get_translation_model()
        result = await model.ainvoke(
            [_TRANSLATION_SYSTEM_MSG, user_msg],
            config={"callbacks": []},
        )
        if isinstance(result, QueryTranslation):
            return result
        return QueryTranslation.model_validate(result)
    except Exception as e:
        logger.warning(
            "Shopify query translation failed (%s); falling back to original inputs",
            e,
        )
        return QueryTranslation(
            detected_language="unknown",
            original_user_query_en=original_user_query,
            query_for_dense_search_en=query_for_dense_search,
            query_for_sparse_search_en=query_for_sparse_search,
            search_title_en=search_title,
        )


def _sanitize_html_description(text: str | None) -> str:
    """Strip all HTML tags, style/script blocks, and unescape entities."""
    if not text:
        return ""
    # Remove <style>...</style> and <script>...</script> blocks
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    # Convert <br> / <br/> to newline before stripping
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Unescape HTML entities (&amp; &lt; &nbsp; etc.)
    text = html.unescape(text)
    # Collapse excessive whitespace / newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


async def get_shopify_credentials(user_id: str, connectorId: str | None = None) -> Optional[TokenResponse]:
    """Get Shopify credentials from token service."""
    key = (user_id, connectorId or "")
    ttl = 0.0
    try:
        ttl = float(project_settings().SHOPIFY_CREDENTIALS_CACHE_TTL_SECONDS)
    except Exception:
        pass

    if ttl > 0:
        hit = _SHOPIFY_CREDS_CACHE.get(key)
        if hit is not None:
            cached_at, cached = hit
            if time.monotonic() - cached_at < ttl and cached is not None:
                return cached

    try:
        user_data = await get_token().get_user_data(
            user_id=user_id,
            connector_name="shopify",
            connectorId=connectorId or "",
            needs_refresh=False,
        )
    except Exception as e:
        logger.error(f"Error getting Shopify credentials: {str(e)}")
        return None

    if ttl > 0 and user_data is not None:
        _SHOPIFY_CREDS_CACHE[key] = (time.monotonic(), user_data)

    return user_data


async def make_shopify_request(
    shop_domain: str,
    access_token: str,
    api_version: str,
    query: str,
    variables: dict,
) -> dict:
    """Make a lightweight Shopify GraphQL request."""
    api_url = f"https://{shop_domain}/admin/api/{api_version}/graphql.json"

    headers = {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": access_token,
    }
    payload = {"query": query, "variables": variables}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=30.0,
        )

        if response.status_code == 401:
            raise ValueError("Invalid Shopify credentials")

        response.raise_for_status()
        return response.json()


def _parse_price(price_val) -> Optional[float]:
    """Parse price from string, int/float, or Decimal128 dict."""
    if price_val is None:
        return None
    if isinstance(price_val, (int, float)):
        return float(price_val)
    if isinstance(price_val, str):
        try:
            return float(price_val)
        except (ValueError, TypeError):
            return None
    if isinstance(price_val, dict):
        return parse_decimal128(price_val)
    return None


def parse_decimal128(decimal_obj):
    """Convert Decimal128 format (s, e, d) to float."""
    if not isinstance(decimal_obj, dict):
        return None
    if "d" not in decimal_obj or not decimal_obj["d"]:
        return None

    digits = decimal_obj["d"]
    exponent = decimal_obj.get("e", 0)
    sign = decimal_obj.get("s", 1)

    # Handle different Decimal128 formats
    if len(digits) == 1:
        # Simple case: single number, e.g., [20] with e=1 means 20.0
        num = float(digits[0])
    elif len(digits) == 2:
        # Two-part number: combine integer and fractional parts
        # e.g., [22, 7900000] might represent 22.79
        integer_part = float(digits[0])
        fractional_raw = float(digits[1])
        # Convert fractional part: 7900000 -> 0.79 (assuming 7 digits means 2 decimal places)
        if fractional_raw > 0:
            # Count digits to determine decimal places
            num_digits = len(str(int(fractional_raw)))
            # Typically fractional parts are stored with leading zeros
            # e.g., 7900000 might represent .79 (scaled by 10^7)
            # Try to infer the scale - if it's 7 digits, might be 2 decimal places
            if num_digits >= 7:
                # Likely 2 decimal places (e.g., 7900000 = 0.79)
                fractional_part = fractional_raw / (10 ** (num_digits - 2))
                # But this seems wrong... let's try a simpler approach
                # Maybe it's just: integer + fractional / 10^7
                fractional_part = fractional_raw / 10000000.0
            else:
                fractional_part = fractional_raw / (10**num_digits)
            num = integer_part + fractional_part
        else:
            num = integer_part
    else:
        # Multiple parts: use first element as primary value
        num = float(digits[0]) if digits else 0.0

    # Apply sign (exponent seems to be metadata, not a multiplier)
    num = num * sign

    return num


_MAX_VARIANTS_PER_PRODUCT_FOR_VALIDATION = 12


def _build_products_info(products: List[ShopifyProduct], artifacts: List[Dict[str, Any]]) -> str:
    """Build compact product info string for the LLM — product-level availability +
    all variants (capped) with name, price, availability, and stock so the validator
    can match the user's requested attributes (size/color/etc.) against per-variant
    stock and fall back to product-level signals when no real variants exist."""
    lines: List[str] = []
    for i, (product, artifact) in enumerate(zip(products, artifacts)):
        variants = artifact.get("variants", [])
        variant_parts: List[str] = []
        truncated = max(0, len(variants) - _MAX_VARIANTS_PER_PRODUCT_FOR_VALIDATION)
        for v in variants[:_MAX_VARIANTS_PER_PRODUCT_FOR_VALIDATION]:
            name = v.get("name") or v.get("displayName") or ""
            price = v.get("price")
            avail = v.get("availableForSale")
            inv = v.get("inventoryQuantity")
            part = name
            if price:
                part += f" ${price}"
            if avail is False:
                part += " [OOS]"
            elif avail is True:
                part += " [IN]"
            if isinstance(inv, int):
                part += f" qty={inv}"
            if part.strip():
                variant_parts.append(part.strip())
        if truncated:
            variant_parts.append(f"(+{truncated} more variants)")

        product_avail = artifact.get("availableForSale")
        if product_avail is True:
            avail_tag = "[IN]"
        elif product_avail is False:
            avail_tag = "[OOS]"
        else:
            avail_tag = None

        desc = (product.description or "")[:120]  # truncate long descriptions
        lines.append(
            f"[{i+1}] id={product.id} | {product.name}"
            + (f" {avail_tag}" if avail_tag else "")
            + (f" | {product.price_range}" if product.price_range else "")
            + (f" | inv={product.total_inventory}" if product.total_inventory is not None else "")
            + (f" | {desc}" if desc else "")
            + (f" | variants: {', '.join(variant_parts)}" if variant_parts else " | (no variants listed)")
        )
    return "\n".join(lines)


async def validate_products_with_ai(
    user_query: str,
    products: List[ShopifyProduct],
    artifacts: List[Dict[str, Any]],
    min_relevance_score: float = 0.5,
) -> List[str]:
    """Returns a list of relevant product IDs sorted by relevance score descending (max 5)."""
    if not products:
        return []

    settings = project_settings()
    if not getattr(settings, "ECOMMERCE_PRODUCT_AI_VALIDATION_ENABLED", False):
        logger.info("Shopify AI validation disabled; using top candidates")
        return [p.id for p in products[:5]]

    if not settings.OPENAI_API_KEY or not settings.OPENAI_API_BASE:
        logger.warning("Shopify AI validation skipped: OPENAI_API_KEY or OPENAI_API_BASE not set")
        return [p.id for p in products[:5]]

    try:
        model = _get_validation_model()
        products_info = _build_products_info(products, artifacts)
        user_msg = HumanMessage(
            content=(
                f"Query: {user_query}\n\n"
                f"Products:\n{products_info}\n\n"
                "Return relevance scores for each product."
            )
        )

        result = cast(
            ProductValidationResult,
            await model.ainvoke(
                [_VALIDATION_SYSTEM_MSG, user_msg],
                config={"callbacks": []},
            ),
        )

        relevance_map = {s.product_id: s for s in result.evaluated_products}
        filtered = [
            {"product_id": p.id, "relevance_score": relevance_map[p.id].relevance_score}
            for p in products
            if (r := relevance_map.get(p.id)) and r.is_relevant and r.relevance_score >= min_relevance_score
        ]
        filtered.sort(key=lambda x: x["relevance_score"], reverse=True)
        return [r["product_id"] for r in filtered[:5]]

    except Exception as e:
        logger.error("Error in AI validation: %s", e)
        return [p.id for p in products[:5]]


def process_retrieved_products(
    retrieved_nodes: List[RetrievalResponse],
) -> tuple[List[ShopifyProduct], List[Dict[str, Any]]]:
    """
    Process retrieved nodes from vector database into ShopifyProduct objects and artifacts.

    Args:
        retrieved_nodes: List of RetrievalResponse objects containing product data

    Returns:
        Tuple of (products list, artifacts list)
    """
    products = []
    artifacts = []
    def _canonicalize_product_id(product_data: dict[str, Any]) -> str:
        """
        Deduping helper:
        - Prefer numeric product id extracted from `externalId` (gid://shopify/Product/{id})
        - Fallback to `id`/`item_id`
        """
        ext_id = product_data.get("externalId")
        if isinstance(ext_id, str) and ext_id:
            parts = ext_id.rstrip("/").split("/")
            if parts:
                return str(parts[-1])

        pid = product_data.get("id") or product_data.get("item_id") or ""
        return str(pid) if pid is not None else ""

    seen_canonical_product_ids: set[str] = set()

    for node in retrieved_nodes:
        try:
            # Handle empty or None text
            if not node.text or not node.text.strip():
                logger.warning(f"Skipping node with empty text")
                continue

            # Try to parse the JSON text from the retrieval response
            # The text may be:
            # 1. Direct JSON string with product data
            # 2. Python dict string with nested structure: {'entity': {'data': '{"id": "..."}'}}
            # 3. Already a dict object

            product_data = None

            # Step 1: try to parse node.text as JSON / Python literal
            parsed_dict = None
            if isinstance(node.text, dict):
                parsed_dict = node.text
            elif isinstance(node.text, str):
                try:
                    parsed_dict = json.loads(node.text)
                except json.JSONDecodeError:
                    try:
                        parsed_dict = ast.literal_eval(node.text)
                    except (ValueError, SyntaxError):
                        pass  # plain text – will fall through to metadata

            if isinstance(parsed_dict, dict):
                root = cast(Dict[str, Any], parsed_dict)
                if "entity" in root and isinstance(root["entity"], dict):
                    entity = cast(Dict[str, Any], root["entity"])
                    if "data" in entity:
                        data_str = entity["data"]
                        if isinstance(data_str, dict):
                            product_data = cast(Dict[str, Any], data_str)
                        elif isinstance(data_str, str):
                            try:
                                loaded = json.loads(data_str)
                                product_data = (
                                    cast(Dict[str, Any], loaded)
                                    if isinstance(loaded, dict)
                                    else None
                                )
                            except json.JSONDecodeError:
                                # entity.data is plain text; leave product_data=None
                                # so Step 2 (node.metadata) can handle it
                                logger.debug(
                                    f"entity.data is plain text, trying metadata. "
                                    f"Preview: {data_str[:100]}"
                                )
                    else:
                        # No "data" key — entity itself may be the product record
                        product_data = entity
                else:
                    product_data = root

            # Step 2: if text gave no structured product, try node.metadata
            if product_data is None and node.metadata:
                try:
                    meta = (
                        json.loads(node.metadata)
                        if isinstance(node.metadata, str)
                        else node.metadata
                    )
                    if isinstance(meta, dict) and meta:
                        product_data = meta
                except (json.JSONDecodeError, Exception):
                    pass

            if product_data is None:
                logger.warning(
                    f"Could not extract structured product data from node, skipping. "
                    f"Text preview: {str(node.text)[:100]}"
                )
                continue

            if not isinstance(product_data, dict):
                logger.warning(
                    "Structured product data is not an object, skipping. "
                    f"Text preview: {str(node.text)[:100]}"
                )
                continue
            pd = cast(Dict[str, Any], product_data)

            # Extract product information
            # Metadata uses externalId (Shopify GID) or item_id; legacy JSON used "id"
            product_id = (
                pd.get("externalId")
                or pd.get("id")
                or pd.get("item_id")
                or ""
            )
            canonical_product_id = _canonicalize_product_id(pd)
            if not canonical_product_id:
                logger.debug("Skipping node with no product ID")
                continue
            if canonical_product_id in seen_canonical_product_ids:
                logger.debug(
                    "Skipping duplicate product (canonical=%s, raw_id=%s)",
                    canonical_product_id,
                    product_id,
                )
                continue
            seen_canonical_product_ids.add(canonical_product_id)

            product_title = pd.get("title", "")
            product_description = _sanitize_html_description(pd.get("description", ""))
            # Metadata stores images as "image_urls"; legacy JSON used "images"
            product_images = pd.get("image_urls") or pd.get("images", [])
            image_url = product_images[0] if product_images else None

            # Format price range — metadata stores prices as strings, legacy as Decimal128
            currency_code = pd.get("currencyCode", "CAD")
            min_price = _parse_price(pd.get("minVariantPrice"))
            max_price = _parse_price(pd.get("maxVariantPrice"))

            price_range = None
            if min_price is not None and max_price is not None:
                if min_price == max_price:
                    price_range = f"{currency_code} {min_price:.2f}"
                else:
                    price_range = f"{currency_code} {min_price:.2f} - {max_price:.2f}"

            # Get product URL
            product_url = (
                pd.get("onlineStoreUrl")
                or pd.get("onlineStorePreviewUrl")
                or pd.get("product_url")
                or pd.get("url")
                or None
            )

            # Calculate total inventory from variants.
            # If ALL inventoryQuantity values are None the store does not track inventory —
            # keep total_inventory as None so the widget falls back to availableForSale.
            variants = pd.get("variants", [])
            if variants:
                all_null_inventory = all(v.get("inventoryQuantity") is None for v in variants)
                total_inventory = (
                    None
                    if all_null_inventory
                    else sum((v.get("inventoryQuantity") or 0) for v in variants)
                )
            else:
                total_inventory = None

            # Product-level availability: True if at least one variant is available for sale.
            any_available_for_sale: bool | None = (
                any(v.get("availableForSale", False) for v in variants) if variants else None
            )

            # Create ShopifyProduct for products list (according to model)
            shopify_product = ShopifyProduct(
                id=product_id,
                name=product_title,
                description=product_description,
                product_url=product_url,
                total_inventory=total_inventory,
                price_range=price_range,
            )
            products.append(shopify_product)

            # Create artifact with variant information
            variant_info = []
            for variant in variants:
                variant_price = _parse_price(variant.get("price"))
                variant_compare_price = _parse_price(variant.get("compareAtPrice"))

                variant_info.append(
                    {
                        "id": variant.get("id"),
                        "externalId": variant.get("externalId"),
                        "name": variant.get("title"),
                        "displayName": variant.get("displayName"),
                        "price": variant_price,
                        "compareAtPrice": variant_compare_price,
                        "availableForSale": variant.get("availableForSale"),
                        "inventoryQuantity": variant.get("inventoryQuantity"),
                        "sellableOnlineQuantity": variant.get("sellableOnlineQuantity"),
                    }
                )

            # Create artifact entry with product and variant information
            artifact_entry = {
                "id": product_id,
                "externalId": pd.get("externalId"),
                "name": product_title,
                "description": product_description,
                "image_url": image_url,
                "images": product_images,
                "product_url": product_url,
                "total_inventory": total_inventory,
                "availableForSale": any_available_for_sale,
                "price_range": price_range,
                "currencyCode": currency_code,
                "variants": variant_info,
            }
            artifacts.append(artifact_entry)

        except json.JSONDecodeError as e:
            logger.error(
                f"Error parsing product JSON: {str(e)}. "
                f"Text preview: {node.text[:500] if node.text and len(node.text) > 500 else (node.text or 'None')}"
            )
            continue
        except Exception as e:
            logger.error(
                f"Error processing product: {str(e)}. "
                f"Text preview: {node.text[:500] if node.text and len(node.text) > 500 else (node.text or 'None')}"
            )
            continue

    return products, artifacts
