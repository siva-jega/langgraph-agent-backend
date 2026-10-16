"""
Shopify tool implementations: get_products, get_order_status, get_checkout.

Tool entry points are at the top of the file; all helpers and data-parsing utilities
follow below, grouped by concern.
"""

import ast
import asyncio
import json
import logging
import re
from typing import Any, List, Optional

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, Tool, tool

from common.integration import get_retrieval
from common.modules.retrieval import RetrievalResponse
from common.tools.utils.writer import emit_status

from .models import (
    GetCheckoutResult,
    GetOrderStatusResult,
    GetProductsResult,
    ShopifyCheckout,
    ShopifyOrder,
    ShopifyProduct,
)
from .utils import (
    _sanitize_html_description,
    get_shopify_credentials,
    is_english_language,
    make_shopify_request,
    process_retrieved_products,
    translate_search_queries_to_english,
    validate_products_with_ai,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_SHOPIFY_API_VERSION = "2025-04"

_PRODUCT_FIELDS = """
    id
    title
    handle
    description
    status
    onlineStoreUrl
    onlineStorePreviewUrl
    totalInventory
    priceRangeV2 {
        minVariantPrice {
            amount
            currencyCode
        }
        maxVariantPrice {
            amount
            currencyCode
        }
    }
    variants(first: 250) {
        edges {
            node {
                id
                title
                displayName
                price
                compareAtPrice
                availableForSale
                inventoryQuantity
                sellableOnlineQuantity
            }
        }
    }
    media(first: 6, sortKey: POSITION) {
        nodes {
            ... on MediaImage {
                id
                alt
                image {
                    url
                }
            }
        }
    }
"""

_ORDER_FIELDS = """
    id
    name
    statusPageUrl
    displayFinancialStatus
    displayFulfillmentStatus
    cancelledAt
    closedAt
    createdAt
    totalPriceSet {
        shopMoney {
            amount
            currencyCode
        }
    }
    fulfillmentOrders(first: 10) {
        nodes {
            id
            status
            requestStatus
        }
    }
    fulfillments(first: 10) {
        id
        status
        trackingInfo {
            company
            number
            url
        }
    }
"""


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool(response_format="content_and_artifact")
async def get_products(
    config: RunnableConfig,
    original_user_query: str,
    query_for_dense_search: str,
    query_for_sparse_search: str,
    search_title: str,
    original_language: str = "en",
    limit: int = 10,
) -> tuple[GetProductsResult, dict[str, Any] | list[Any]]:
    """Get products from Shopify by providing an original_user_query, query_for_dense_search,
     query_for_sparse_search, search_title, original_language and optional limit.

    Args:
        original_user_query: The original user query without any modifications, in the
            user's language.
        query_for_dense_search: The query text for optimal dense search in the vector
            database. Pass in the user's language; the server will translate to English
            internally when needed.
        query_for_sparse_search: The query text for optimal sparse search in the vector
            database. Pass in the user's language; the server will translate to English
            internally when needed.
        search_title: The title of the product to search for using the Shopify API,
            in the user's language.
        original_language: ISO 639-1 code of the user's message language ("en", "es",
            "fr", "hi", "ta", "ar", "zh", …). Pass "en" only when the message is already
            in English. The assistant's reply MUST be produced entirely in this language,
            including all hyperlink anchor text.
        limit: Maximum number of products to return (default: 10)

    Returns:
        A GetProductsResult object containing the success status, message, and products.
        Each product includes:
        - image_url: Primary image URL (first image)
        - images: List of all image URLs (up to 6)
        - media_images: List of MediaImage objects with URL and alt text
    """
    try:
        emit_status("Browsing the catalog...")

        user_id, collection_name, apps = _extract_config_fields(config)
        if not user_id:
            return _products_error("No user_id provided in configuration")

        connector_id = _get_shopify_connector_id(apps)
        if not connector_id:
            logger.info(
                "Shopify get_products: Shopify app is not connected for this agent; refusing product retrieval"
            )
            return _products_error("Shopify app is not connected")

        creds = await _resolve_shopify_credentials(user_id, connector_id)
        shop_domain, access_token = creds if creds else ("", "")

        if not is_english_language(original_language):
            translation = await translate_search_queries_to_english(
                original_user_query=original_user_query,
                query_for_dense_search=query_for_dense_search,
                query_for_sparse_search=query_for_sparse_search,
                search_title=search_title,
            )
            logger.info(
                "Shopify get_products: translated search args from %s -> en "
                "(search_title=%r)",
                original_language,
                translation.search_title_en,
            )
            original_user_query = translation.original_user_query_en
            query_for_dense_search = translation.query_for_dense_search_en
            query_for_sparse_search = translation.query_for_sparse_search_en
            search_title = translation.search_title_en

        if connector_id and shop_domain and access_token:
            result = await _search_catalog_and_hydrate(
                connector_id=connector_id,
                collection_name=collection_name,
                shop_domain=shop_domain,
                access_token=access_token,
                query_for_dense_search=query_for_dense_search,
                query_for_sparse_search=query_for_sparse_search,
                original_user_query=original_user_query,
                limit=limit,
            )
            if result is not None:
                return result

        if not shop_domain or not access_token:
            logger.info(
                "Shopify get_products: missing Shopify credentials; cannot fetch products "
                "(connectorId=%r shop_domain_empty=%s access_token_empty=%s)",
                connector_id,
                not bool(shop_domain),
                not bool(access_token),
            )
            return _products_error("Shopify credentials are missing")

        return await _search_shopify_live_api(
            search_title=search_title,
            shop_domain=shop_domain,
            access_token=access_token,
            original_user_query=original_user_query,
            limit=limit,
        )

    except Exception as e:
        logger.exception("get_products failed")
        return _products_error(f"Error getting products: {str(e)}")


@tool(response_format="content_and_artifact")
async def get_order_status(
    config: RunnableConfig,
    order_id: Optional[str] = None,
    customer_email: Optional[str] = None,
) -> tuple[GetOrderStatusResult, list[Any] | dict[str, Any]]:
    """Get the status of a Shopify order by order ID or fetch all orders for a customer email.

    Provide either order_id to fetch a single order, or customer_email to fetch all orders
    for that customer.

    Args:
        config: Configuration containing user_id.
        order_id: The order ID (numeric or GID) or the customer-facing order number (e.g. "#1001"). Use when looking up a specific order.
        customer_email: The customer's email address. Use when looking up all orders for a customer.

    Returns:
        A GetOrderStatusResult containing order status details.
        - If order_id provided: result.order contains the single order.
        - If customer_email provided: result.orders contains the list of orders.
    """
    try:
        emit_status("Checking your order...")
        logger.info(f"get_order_status called: order_id={order_id!r} customer_email={customer_email!r}")

        if not order_id and not customer_email:
            logger.warning("get_order_status: neither order_id nor customer_email provided")
            return (
                GetOrderStatusResult(success=False, message="Provide either order_id or customer_email."),
                [],
            )

        user_id, _, apps = _extract_config_fields(config)
        logger.info(f"get_order_status: user_id={user_id!r}")
        if not user_id:
            logger.error("get_order_status: no user_id in configurable")
            return (
                GetOrderStatusResult(success=False, message="No user_id provided in configuration"),
                [],
            )

        connector_id = _get_shopify_connector_id(apps)
        logger.info(f"get_order_status: connectorId={connector_id!r}")

        # Fetch credentials directly here to preserve the error-field diagnostic log.
        credentials = await get_shopify_credentials(user_id, connector_id)
        cred_error = repr(credentials.error) if credentials else "None (no credentials)"
        logger.info(f"get_order_status: credentials fetched, error={cred_error}")
        if not credentials:
            logger.error("get_order_status: failed to get Shopify credentials")
            return (
                GetOrderStatusResult(success=False, message="Failed to get Shopify credentials"),
                [],
            )

        shop_domain, access_token = _extract_credential_fields(credentials)
        logger.info(f"get_order_status: shop_domain={shop_domain!r} token_present={bool(access_token)}")

        if not shop_domain:
            logger.error("get_order_status: shop_domain is empty")
            return (
                GetOrderStatusResult(success=False, message="Some problem occurred while getting order status"),
                [],
            )

        if order_id:
            order_gid = f"gid://shopify/Order/{order_id}" if order_id.isdigit() else order_id
            logger.info(f"get_order_status: fetching single order order_gid={order_gid!r}")

            query = f"""
            query GetOrderStatus($orderId: ID!) {{
                order(id: $orderId) {{
                    {_ORDER_FIELDS}
                }}
            }}
            """
            data = await make_shopify_request(
                shop_domain=shop_domain,
                access_token=access_token,
                api_version=_SHOPIFY_API_VERSION,
                query=query,
                variables={"orderId": order_gid},
            )
            logger.info(f"get_order_status: response keys={list(data.keys()) if isinstance(data, dict) else type(data)}")

            invalid_id = False
            if "errors" in data:
                error_messages = [e.get("message", "Unknown error") for e in data["errors"]]
                logger.error(f"get_order_status: GraphQL errors={error_messages}")
                # A malformed order ID makes Shopify reject the ID! variable outright,
                # e.g. {'errors': [{'message': 'Variable $orderId of type ID! was
                # provided invalid value', ...}]}. The value may actually be a
                # customer-facing order number, so fall through to the name search.
                if any("provided invalid value" in msg for msg in error_messages):
                    invalid_id = True
                else:
                    raise Exception(f"GraphQL errors: {', '.join(error_messages)}")

            # When the ID is valid but no matching order exists, Shopify returns
            # {'data': {'order': None}, ...}.
            order_data = None if invalid_id else data.get("data", {}).get("order")

            # Fallback: the value may be a customer-facing order number (e.g. "#1001")
            # rather than an internal order ID. Look it up via the orders(query:) search.
            if not order_data:
                logger.info(f"get_order_status: id lookup failed (invalid_id={invalid_id}), trying order number search for {order_id!r}")
                name_query = f"""
                query GetOrderByNumber($query: String!, $first: Int!) {{
                    orders(first: $first, query: $query) {{
                        edges {{ node {{ {_ORDER_FIELDS} }} }}
                    }}
                }}
                """
                name_data = await make_shopify_request(
                    shop_domain=shop_domain,
                    access_token=access_token,
                    api_version=_SHOPIFY_API_VERSION,
                    query=name_query,
                    variables={"query": f"name:{order_id}", "first": 1},
                )
                if "errors" in name_data:
                    error_messages = [e.get("message", "Unknown error") for e in name_data["errors"]]
                    logger.error(f"get_order_status: order number search GraphQL errors={error_messages}")
                    raise Exception(f"GraphQL errors: {', '.join(error_messages)}")
                edges = name_data.get("data", {}).get("orders", {}).get("edges", [])
                order_data = edges[0]["node"] if edges else None

            if not order_data:
                logger.warning(f"get_order_status: no order found for order_id={order_id!r} (id or order number)")
                return (
                    GetOrderStatusResult(
                        success=False,
                        message=f"No order was found matching '{order_id}'. Please double-check that you are providing the right order ID or order number and try again.",
                    ),
                    [],
                )

            order = _parse_order_node(order_data)
            logger.info(f"get_order_status: found order name={order.name!r} financial={order.display_financial_status!r} fulfillment={order.display_fulfillment_status!r}")
            return (
                GetOrderStatusResult(success=True, message=f"Found order status for {order.name}", order=order),
                {"name": "Shopify", "tool": "Get Order Status", "data": order.model_dump()},
            )

        emit_status("Checking your order history...")
        logger.info(f"get_order_status: fetching orders for email={customer_email!r}")

        query = f"""
        query GetOrdersByEmail($query: String!, $first: Int!) {{
            orders(first: $first, query: $query) {{
                edges {{
                    node {{
                        {_ORDER_FIELDS}
                    }}
                }}
            }}
        }}
        """
        data = await make_shopify_request(
            shop_domain=shop_domain,
            access_token=access_token,
            api_version=_SHOPIFY_API_VERSION,
            query=query,
            variables={"query": f"email:{customer_email}", "first": 20},
        )
        logger.info(f"get_order_status: response keys={list(data.keys()) if isinstance(data, dict) else type(data)}")

        if "errors" in data:
            error_messages = [e.get("message", "Unknown error") for e in data["errors"]]
            logger.error(f"get_order_status: GraphQL errors={error_messages}")
            raise Exception(f"GraphQL errors: {', '.join(error_messages)}")

        edges = data.get("data", {}).get("orders", {}).get("edges", [])
        logger.info(f"get_order_status: raw order edges returned={len(edges)}")
        orders = [_parse_order_node(edge["node"]) for edge in edges]

        if not orders:
            logger.warning(f"get_order_status: no orders found for email={customer_email!r}")
            return (
                GetOrderStatusResult(success=False, message=f"No orders found for {customer_email}"),
                [],
            )

        logger.info(f"get_order_status: found {len(orders)} order(s) for email={customer_email!r}")
        return (
            GetOrderStatusResult(
                success=True,
                message=f"Found {len(orders)} order(s) for {customer_email}",
                orders=orders,
            ),
            {"name": "Shopify", "tool": "Get Order Status", "data": [o.model_dump() for o in orders]},
        )

    except Exception as e:
        logger.error(f"get_order_status: unhandled exception: {str(e)}", exc_info=True)
        return (
            GetOrderStatusResult(success=False, message=f"Error getting order status: {str(e)}"),
            [],
        )


@tool(response_format="content_and_artifact")
async def get_checkout(
    config: RunnableConfig,
    checkout_token: str,
) -> tuple[GetCheckoutResult, list[Any] | dict[str, Any]]:
    """Check if a Shopify checkout has been completed or is still abandoned.

    Use this after a checkouts/create webhook to determine if the customer
    completed their purchase or abandoned the cart.

    Args:
        config: Configuration containing user_id.
        checkout_token: The checkout token (from the webhook payload's cart.id).

    Returns:
        A GetCheckoutResult with checkout details and is_abandoned flag.
    """
    try:
        emit_status("Checking your checkout status...")
        logger.info(f"get_checkout called: token={checkout_token!r}")

        user_id, _, apps = _extract_config_fields(config)

        # get_checkout uses exact slug match and only app.get("id") — intentionally kept inline.
        connector_id = None
        for app in apps:
            if (app.get("slug") or "").lower() == "shopify":
                connector_id = app.get("id")
                break

        creds = await _resolve_shopify_credentials(user_id, connector_id)
        if not creds:
            return (
                GetCheckoutResult(
                    success=False,
                    message="Shopify credentials not found. Please connect your Shopify store.",
                ),
                [],
            )

        shop_domain, access_token = creds
        if not shop_domain or not access_token:
            return (
                GetCheckoutResult(success=False, message="Missing Shopify shop domain or access token."),
                [],
            )

        url = f"https://{shop_domain}/admin/api/{_SHOPIFY_API_VERSION}/checkouts/{checkout_token}.json"
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"},
                timeout=15.0,
            )
            if response.status_code == 404:
                return (
                    GetCheckoutResult(success=False, message=f"Checkout {checkout_token} not found."),
                    [],
                )
            response.raise_for_status()
            data = response.json()

        checkout_data = data.get("checkout") or data
        completed_at = checkout_data.get("completed_at")
        is_abandoned = completed_at is None

        checkout = ShopifyCheckout(
            token=checkout_data.get("token", checkout_token),
            cart_token=checkout_data.get("cart_token"),
            email=checkout_data.get("email"),
            phone=checkout_data.get("phone"),
            completed_at=completed_at,
            abandoned_checkout_url=checkout_data.get("abandoned_checkout_url"),
            total_price=checkout_data.get("total_price"),
            currency=checkout_data.get("currency"),
            created_at=checkout_data.get("created_at"),
            updated_at=checkout_data.get("updated_at"),
            line_items=checkout_data.get("line_items"),
        )
        msg = (
            f"Checkout is abandoned (no order placed). Total: {checkout.currency} {checkout.total_price}"
            if is_abandoned
            else f"Checkout completed at {completed_at}. Customer already purchased."
        )
        logger.info(f"get_checkout result: abandoned={is_abandoned} token={checkout_token}")
        return (
            GetCheckoutResult(success=True, message=msg, checkout=checkout, is_abandoned=is_abandoned),
            checkout_data,
        )

    except Exception as e:
        logger.exception("get_checkout failed")
        return (
            GetCheckoutResult(success=False, message=f"Error checking checkout: {str(e)}"),
            [],
        )


@tool(response_format="content_and_artifact")
async def get_collection(
    config: RunnableConfig,
) -> tuple[str, dict[str, Any] | list[Any]]:
    """Fetch the live products in the collection the shopper is currently viewing.

    Call this to answer questions about "this collection", "these products", or "this"
    after the shopper has opened a collection.
    """
    try:
        configurable = config.get("configurable") or {}
        agent_id = configurable.get("agent_id")
        active_collection_id = configurable.get("active_collection_id")
        if not agent_id or not active_collection_id:
            return ("No collection is currently open.", [])

        emit_status("Opening this collection...")

        from common.project_settings import project_settings as _ps
        s = _ps()
        base_url = (s.NESTJS_URL or "").rstrip("/")
        secret = s.INTERNAL_SECRET or ""

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/internal/storefront/{agent_id}/collection/{active_collection_id}",
                headers={"x-internal-secret": secret},
            )
            resp.raise_for_status()
            data = resp.json()

        payload = data.get("result", data)
        collection_name = payload.get("name") or "this collection"
        cards = payload.get("products") or []

        artifacts = [_collection_card_to_artifact(card) for card in cards if isinstance(card, dict)]
        content = _build_collection_content(collection_name, cards)
        return (content, {"name": "Shopify", "tool": "Get Products", "data": artifacts})

    except Exception as e:
        logger.exception("get_collection failed")
        return (f"Error loading the collection: {str(e)}", [])


def _shopify_cart_token_from_context(workflow_context: dict) -> str:
    """Walk the workflow context to find the cart token from the trigger node's output.

    Checks two payload shapes:
      - output.data.cartToken  (cart.active trigger)
      - output.data.cart.token (legacy nested shape)
    """
    for node_id, node_data in workflow_context.items():
        if node_id.startswith("__"):
            continue
        if not isinstance(node_data, dict):
            continue
        data = node_data.get("output", {}).get("data", {})
        if not isinstance(data, dict):
            continue
        token = data.get("cartToken") or (data.get("cart") or {}).get("token")
        if token:
            return str(token)
    return ""


def _shopify_checkout_token_from_context(workflow_context: dict) -> str:
    """Walk the workflow context to find checkoutToken from the trigger node's output.

    Trigger payload shape: output.data.checkoutToken
    """
    for node_id, node_data in workflow_context.items():
        if node_id.startswith("__"):
            continue
        if not isinstance(node_data, dict):
            continue
        data = node_data.get("output", {}).get("data", {})
        if not isinstance(data, dict):
            continue
        token = data.get("checkoutToken")
        if token:
            return str(token)
    return ""


@tool
async def check_cart_status(
    config: RunnableConfig,
    mark_abandoned: bool = True,
    cart_token: Optional[str] = None,
) -> dict:
    """Check the status of a Shopify visitor cart session stored internally.

    Returns the current cart session status.

    Status values: CART_ACTIVE, CHECKOUT_STARTED, ORDER_COMPLETED, CART_ABANDONED.

    The cart token is automatically pulled from the workflow trigger context.
    Only provide cart_token explicitly to override.

    Args:
        mark_abandoned: If True (default), atomically marks CART_ACTIVE sessions as
            CART_ABANDONED on read — use after a WAIT step to detect abandonment.
            Set to False to read status without changing it.
        cart_token: Override the cart token (auto-detected from trigger context if omitted).

    Returns:
        dict with 'found' (bool) and 'status' (str).
    """
    from common.project_settings import project_settings as _ps
    s = _ps()
    base_url = (s.NESTJS_URL or "").rstrip("/")
    secret = s.INTERNAL_SECRET or ""
    configurable = config.get("configurable") or {}
    workspace_id = configurable.get("workspace_id", "")
    resolved_token = cart_token or _shopify_cart_token_from_context(
        configurable.get("workflow_context") or {}
    )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{base_url}/internal/shopify/cart-status",
            params={
                "cartToken": resolved_token,
                "workspaceId": workspace_id,
                "markAbandoned": "true" if mark_abandoned else "false",
            },
            headers={"x-internal-secret": secret},
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", data)
        if isinstance(result, dict):
            status = result.get("status", "")
            result["branch"] = "true" if status == "ORDER_COMPLETED" else "false"
        return result


@tool
async def check_checkout_status(
    config: RunnableConfig,
    mark_abandoned: bool = True,
    checkout_token: Optional[str] = None,
) -> dict:
    """Check the status of a Shopify checkout stored in the internal event log.

    Returns whether the checkout was completed, is still pending, or has been abandoned.

    Status values: PENDING, COMPLETED, ABANDONED.

    The checkout token is automatically pulled from the workflow trigger context.
    Only provide checkout_token explicitly to override.

    Args:
        mark_abandoned: If True (default), treats incomplete checkouts as ABANDONED on read —
            use after a WAIT step to detect abandonment.
            Set to False to check status without marking as abandoned.
        checkout_token: Override the checkout token (auto-detected from trigger context if omitted).

    Returns:
        dict with 'found' (bool), 'status' (str), 'email', 'recoverUrl', 'items'.
    """
    from common.project_settings import project_settings as _ps
    s = _ps()
    base_url = (s.NESTJS_URL or "").rstrip("/")
    secret = s.INTERNAL_SECRET or ""
    configurable = config.get("configurable") or {}
    workspace_id = configurable.get("workspace_id", "")
    resolved_token = checkout_token or _shopify_checkout_token_from_context(
        configurable.get("workflow_context") or {}
    )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{base_url}/internal/shopify/checkout-status",
            params={
                "checkoutToken": resolved_token,
                "workspaceId": workspace_id,
                "markAbandoned": "true" if mark_abandoned else "false",
            },
            headers={"x-internal-secret": secret},
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", data)
        if isinstance(result, dict):
            status = result.get("status", "")
            result["branch"] = "true" if status == "COMPLETED" else "false"
        return result


@tool
async def update_cart_status(
    config: RunnableConfig,
    status: str,
    cart_token: Optional[str] = None,
) -> dict:
    """Update the status of a Shopify visitor cart session stored internally.

    Valid status values: CART_ACTIVE, CHECKOUT_STARTED, ORDER_COMPLETED, CART_ABANDONED.

    The cart token is automatically pulled from the workflow trigger context.
    Only provide cart_token explicitly to override.

    Args:
        status: The new status to set.
        cart_token: Override the cart token (auto-detected from trigger context if omitted).

    Returns:
        dict with 'updated' (bool).
    """
    from common.project_settings import project_settings as _ps
    s = _ps()
    base_url = (s.NESTJS_URL or "").rstrip("/")
    secret = s.INTERNAL_SECRET or ""
    configurable = config.get("configurable") or {}
    workspace_id = configurable.get("workspace_id", "")
    resolved_token = cart_token or _shopify_cart_token_from_context(
        configurable.get("workflow_context") or {}
    )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{base_url}/internal/shopify/session-status",
            json={"cartToken": resolved_token, "workspaceId": workspace_id, "status": status},
            headers={"x-internal-secret": secret},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", data)


@tool
async def resolve_identity(
    config: RunnableConfig,
    client_id: Optional[str] = None,
) -> dict:
    """Resolve a Shopify visitor's identity — mapping their browser ID to contact info.

    Use this after any pixel trigger (product.viewed, product.added_to_cart, etc.) to
    find out whether the visitor is a known customer with an email or phone on record.
    If found, use the email/phone to route to EMAIL or WhatsApp. If not found, fall back
    to WIDGET.

    The Shopify client ID is automatically pulled from the workflow trigger context.
    Only provide client_id explicitly to override.

    Args:
        client_id: Override the Shopify client ID (auto-detected from trigger context if omitted).

    Returns:
        dict with 'found' (bool), and when found: 'sessionId', 'customerId', 'email', 'phone'.
    """
    from common.project_settings import project_settings as _ps
    s = _ps()
    base_url = (s.NESTJS_URL or "").rstrip("/")
    secret = s.INTERNAL_SECRET or ""
    configurable = config.get("configurable") or {}
    workspace_id = configurable.get("workspace_id", "")
    resolved_client_id = client_id or _extract_client_id_from_context(
        configurable.get("workflow_context") or {}
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/internal/shopify/identity-resolve",
                params={
                    "shopifyClientId": resolved_client_id,
                    "workspaceId": workspace_id,
                },
                headers={"x-internal-secret": secret},
            )
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", data)
            if isinstance(result, dict):
                result["branch"] = "true" if result.get("found") else "false"
            return result
    except Exception as e:
        logger.warning("resolve_identity failed: %s", e)
        return {"found": False, "branch": "false", "error": str(e)}


@tool
async def fetch_session_events(
    config: RunnableConfig,
    event_types: Optional[List[str]] = None,
    lookback_minutes: int = 30,
    session_id: Optional[str] = None,
    client_id: Optional[str] = None,
) -> dict:
    """Fetch all Shopify pixel events for the current visitor session within a lookback window.

    Use after a product.viewed, collection.viewed, or search.submitted trigger to retrieve
    everything the visitor did in the past N minutes — all products browsed, collections visited,
    searches made — so downstream nodes can personalise messages.

    Events are grouped by type. Each group contains a 'count' and an 'items' list.

    Available event_types: product_viewed, collection_viewed, search_submitted,
    product_added_to_cart, cart_viewed, checkout_started, checkout_completed.

    Args:
        event_types: List of event types to fetch (e.g. ["product_viewed", "search_submitted"]).
            Defaults to ["product_viewed"] if omitted.
        lookback_minutes: How far back to look (default 30).
        session_id: Override the session ID (auto-detected from trigger context if omitted).
        client_id: Override the Shopify client ID (auto-detected from trigger context if omitted).

    Returns:
        dict keyed by event type, each with 'count' (int) and 'items' (list of event data dicts).
    """
    from common.project_settings import project_settings as _ps
    s = _ps()
    base_url = (s.NESTJS_URL or "").rstrip("/")
    secret = s.INTERNAL_SECRET or ""
    configurable = config.get("configurable") or {}
    workspace_id = configurable.get("workspace_id", "")
    workflow_context = configurable.get("workflow_context") or {}

    resolved_session_id = session_id or _extract_session_id_from_context(workflow_context)
    resolved_client_id = client_id or _extract_client_id_from_context(workflow_context)
    types = event_types or ["product_viewed"]

    params: dict = {
        "workspaceId": workspace_id,
        "clientId": resolved_client_id,
        "eventTypes": ",".join(types),
        "lookbackMinutes": str(lookback_minutes),
    }
    if resolved_session_id:
        params["sessionId"] = resolved_session_id

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{base_url}/internal/shopify/session-events",
            params=params,
            headers={"x-internal-secret": secret},
        )
        resp.raise_for_status()
        body: dict = resp.json()

    # Backend wraps all responses in { status, statusCode, result, version }
    raw = body.get("result") if isinstance(body.get("result"), dict) else body

    result: dict = {}
    for event_type, items in raw.items():
        if isinstance(items, list):
            result[event_type] = {"count": len(items), "items": items}
    return result


def _extract_session_id_from_context(workflow_context: dict) -> str:
    """Walk workflow context to find sessionId from the trigger node's output."""
    for node_id, node_data in workflow_context.items():
        if node_id.startswith("__"):
            continue
        if not isinstance(node_data, dict):
            continue
        data = node_data.get("output", {}).get("data", {})
        if isinstance(data, dict) and data.get("sessionId"):
            return str(data["sessionId"])
    return ""


def _extract_client_id_from_context(workflow_context: dict) -> str:
    """Walk workflow context to find clientId from the trigger node's output."""
    for node_id, node_data in workflow_context.items():
        if node_id.startswith("__"):
            continue
        if not isinstance(node_data, dict):
            continue
        data = node_data.get("output", {}).get("data", {})
        if isinstance(data, dict) and data.get("clientId"):
            return str(data["clientId"])
    return ""


def get_shopify_tools() -> List[BaseTool]:
    """Return all registered Shopify tools."""
    return [get_products, get_collection, get_order_status, get_checkout, check_cart_status, check_checkout_status, update_cart_status, fetch_session_events, resolve_identity]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: configuration and credentials
# ─────────────────────────────────────────────────────────────────────────────

def _extract_config_fields(config: RunnableConfig) -> tuple[str | None, str, list]:
    """Pull user_id, collection_name, and apps list from the LangChain configurable."""
    configurable = config.get("configurable", {})
    return (
        configurable.get("user_id"),
        configurable.get("collection_name", "b2c"),
        configurable.get("apps") or [],
    )


def _get_shopify_connector_id(apps: list[Any]) -> str | None:
    """Return the connectorId of the first Shopify app in the apps list, or None."""
    for app in apps or []:
        if not isinstance(app, dict):
            continue
        slug = str(app.get("slug") or app.get("name") or "").strip().lower()
        if "shopify" not in slug:
            continue
        connector_id = app.get("connectorId") or app.get("id")
        if connector_id:
            return str(connector_id)
    return None


def _extract_credential_fields(credentials: Any) -> tuple[str, str]:
    """Extract (shop_domain, access_token) from a credentials object."""
    metadata = credentials.metadata or {}
    shop_domain = (
        metadata.get("shop_domain")
        or metadata.get("myshopifyDomain")
        or metadata.get("shop", "")
    )
    access_token = (
        credentials.token
        or getattr(credentials, "access_token", None)
        or metadata.get("access_token", "")
    )
    return shop_domain, access_token


async def _resolve_shopify_credentials(
    user_id: str,
    connector_id: str | None,
) -> tuple[str, str] | None:
    """Fetch Shopify OAuth credentials and return (shop_domain, access_token), or None if unavailable."""
    credentials = await get_shopify_credentials(user_id, connector_id)
    if not credentials:
        return None
    return _extract_credential_fields(credentials)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: get_products — catalog search path
# ─────────────────────────────────────────────────────────────────────────────

def _coerce_to_dict(raw: Any) -> dict[str, Any] | None:
    """Parse a raw value into a dict, tolerating JSON strings and Python-literal strings."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            parsed2 = json.loads(parsed)
            if isinstance(parsed2, dict):
                return parsed2
    except Exception:
        pass
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return None


def _is_shopify_node(n: RetrievalResponse) -> bool:
    """Return True if a retrieval hit originated from a Shopify embedding."""
    try:
        text_obj = _coerce_to_dict(n.text)
        if isinstance(text_obj, dict):
            if text_obj.get("connector_type") == "shopify":
                return True
            # Some retrieval paths wrap payload under entity.data
            entity_obj = text_obj.get("entity")
            if isinstance(entity_obj, dict):
                inner_obj = _coerce_to_dict(entity_obj.get("data"))
                if isinstance(inner_obj, dict) and inner_obj.get("connector_type") == "shopify":
                    return True
    except Exception:
        pass
    try:
        meta_obj = _coerce_to_dict(n.metadata)
        if isinstance(meta_obj, dict):
            return meta_obj.get("connector_type") == "shopify"
    except Exception:
        pass
    return False



async def _retrieve_products(
    query_for_dense_search: str,
    query_for_sparse_search: str,
    kids: List[str],
    collection_name: str = "b2c",
) -> tuple[list[RetrievalResponse], list[RetrievalResponse]]:
    """Run dense and sparse vector searches in parallel and return both result lists."""
    retrieval_service = get_retrieval()
    return await asyncio.gather(
        retrieval_service.retrieve(
            text=query_for_dense_search,
            entity_ids=[],
            folder_ids=kids,
            collection_name=collection_name,
        ),
        retrieval_service.retrieve_sparse(
            text=query_for_sparse_search,
            entity_ids=[],
            folder_ids=kids,
            collection_name=collection_name,
        ),
    )


async def _search_catalog_and_hydrate(
    connector_id: str,
    collection_name: str,
    shop_domain: str,
    access_token: str,
    query_for_dense_search: str,
    query_for_sparse_search: str,
    original_user_query: str,
    limit: int,
) -> tuple[GetProductsResult, dict[str, Any]] | None:
    """Run the full catalog path: vector search → Shopify filter → AI rerank + live hydration.

    Returns a result tuple on success, or None to signal fall-through to the live API path.
    """
    try:
        dense_nodes, sparse_nodes = await _retrieve_products(
            query_for_dense_search=query_for_dense_search,
            query_for_sparse_search=query_for_sparse_search,
            kids=[connector_id],
            collection_name=collection_name,
        )
        shopify_nodes = [n for n in dense_nodes + sparse_nodes if _is_shopify_node(n)]
    except Exception as retrieval_err:
        logger.info(
            "Shopify get_products: connector-scoped retrieval failed for connectorId=%s; trying live Shopify API: %s",
            connector_id,
            retrieval_err,
        )
        shopify_nodes = []

    if not shopify_nodes:
        logger.info(
            "Shopify get_products: no connector-scoped crawled products found for connectorId=%s; trying live Shopify API",
            connector_id,
        )
        return None

    products, artifacts = process_retrieved_products(shopify_nodes)
    if not products:
        return None

    return await _rerank_with_ai_and_hydrate(
        connector_id=connector_id,
        products=products,
        artifacts=artifacts,
        original_user_query=original_user_query,
        shop_domain=shop_domain,
        access_token=access_token,
        limit=limit,
    )


async def _rerank_with_ai_and_hydrate(
    connector_id: str,
    products: List[ShopifyProduct],
    artifacts: List[dict[str, Any]],
    original_user_query: str,
    shop_domain: str,
    access_token: str,
    limit: int,
) -> tuple[GetProductsResult, dict[str, Any]] | None:
    """Race AI relevance ranking against live Shopify product hydration in parallel.

    Returns a filtered result tuple on success, or None to fall through to the live API path.
    """
    emit_status("Picking the best matches...")
    candidate_ids = [p.id for p in products]
    try:
        validation_task = asyncio.create_task(
            validate_products_with_ai(
                user_query=original_user_query,
                products=products,
                artifacts=artifacts,
                min_relevance_score=0.7,
            )
        )
        live_fetch_task = asyncio.create_task(
            _fetch_products_by_ids(
                product_ids=candidate_ids,
                shop_domain=shop_domain,
                access_token=access_token,
            )
        )
        relevant_ids, live_fetch_result = await asyncio.gather(
            validation_task, live_fetch_task, return_exceptions=True
        )
        if isinstance(live_fetch_result, BaseException):
            raise live_fetch_result
        live_products, live_artifacts = live_fetch_result
    except Exception as live_fetch_err:
        logger.warning(
            "Shopify get_products: live product hydration failed for connectorId=%s; trying live Shopify search: %s",
            connector_id,
            live_fetch_err,
        )
        return None
    if isinstance(relevant_ids, BaseException):
        logger.warning("AI product validation failed during parallel fetch: %s", relevant_ids)
        relevant_ids = candidate_ids[:limit]
    elif not relevant_ids:
        logger.info(
            "Shopify get_products: AI validator excluded all candidates for query=%r; "
            "returning empty result",
            original_user_query,
        )
        return _build_products_result([], [])

    filtered_products, filtered_artifacts = _filter_products_by_relevance(
        relevant_ids, live_products, live_artifacts
    )
    if not filtered_products:
        return None

    return _build_products_result(filtered_products, filtered_artifacts)


def _filter_products_by_relevance(
    relevant_ids: list[str],
    live_products: List[ShopifyProduct],
    live_artifacts: List[dict[str, Any]],
) -> tuple[List[ShopifyProduct], List[dict[str, Any]]]:
    """Order live-fetched products by AI-ranked IDs, dropping any that weren't hydrated."""
    live_by_id = {p.id: (p, a) for p, a in zip(live_products, live_artifacts)}
    filtered_products: List[ShopifyProduct] = []
    filtered_artifacts: List[dict[str, Any]] = []
    for pid in relevant_ids:
        pair = live_by_id.get(pid)
        if pair:
            filtered_products.append(pair[0])
            filtered_artifacts.append(pair[1])
    return filtered_products, filtered_artifacts


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: get_products — live Shopify API path
# ─────────────────────────────────────────────────────────────────────────────

async def _fetch_products_by_ids(
    product_ids: List[str],
    shop_domain: str,
    access_token: str,
) -> tuple[List[ShopifyProduct], List[dict[str, Any]]]:
    """Fetch full product data from Shopify GraphQL for a list of product IDs."""
    if not product_ids:
        return [], []

    query = f"""
    query GetProductsByIds($ids: [ID!]!) {{
        nodes(ids: $ids) {{
            ... on Product {{
                {_PRODUCT_FIELDS}
            }}
        }}
    }}
    """
    data = await make_shopify_request(
        shop_domain=shop_domain,
        access_token=access_token,
        api_version=_SHOPIFY_API_VERSION,
        query=query,
        variables={"ids": product_ids},
    )
    if "errors" in data:
        error_messages = [e.get("message", "Unknown error") for e in data["errors"]]
        raise Exception(f"GraphQL errors: {', '.join(error_messages)}")

    node_map = {
        node["id"]: node
        for node in data.get("data", {}).get("nodes", [])
        if node
    }
    products: List[ShopifyProduct] = []
    artifacts: List[dict[str, Any]] = []
    for product_id in product_ids:
        node = node_map.get(product_id)
        if node:
            node_status = (node.get("status") or "ACTIVE").upper()
            if node_status not in ("ACTIVE", ""):
                continue
            product, artifact = _parse_product_node(node)
            products.append(product)
            artifacts.append(artifact)
    return products, artifacts


async def _search_shopify_live_api(
    search_title: str,
    shop_domain: str,
    access_token: str,
    original_user_query: str,
    limit: int,
) -> tuple[GetProductsResult, dict[str, Any] | list]:
    """Search Shopify directly via GraphQL, AI-rerank the results, and return the best matches."""
    normalized_title = (search_title or "").strip()
    if not normalized_title:
        logger.info(
            "Shopify get_products: no search_title provided for live Shopify search; returning no products"
        )
        return _products_error("Nothing found")

    query = f"""
    query GetProducts($first: Int!, $query: String) {{
        products(first: $first, query: $query) {{
            edges {{
                node {{
                    {_PRODUCT_FIELDS}
                }}
            }}
        }}
    }}
    """
    data = await make_shopify_request(
        shop_domain=shop_domain,
        access_token=access_token,
        api_version=_SHOPIFY_API_VERSION,
        query=query,
        variables={"first": limit, "query": normalized_title},
    )
    if "errors" in data:
        error_messages = [e.get("message", "Unknown error") for e in data["errors"]]
        raise Exception(f"GraphQL errors: {', '.join(error_messages)}")

    edges = data.get("data", {}).get("products", {}).get("edges", [])
    products: List[ShopifyProduct] = []
    artifacts: List[dict[str, Any]] = []
    for edge in edges:
        node = edge["node"]
        node_status = (node.get("status") or "ACTIVE").upper()
        if node_status not in ("ACTIVE", ""):
            continue
        product, artifact = _parse_product_node(node)
        products.append(product)
        artifacts.append(artifact)

    emit_status("Picking the best matches...")
    relevant_ids = await validate_products_with_ai(
        user_query=original_user_query,
        products=products,
        artifacts=artifacts,
        min_relevance_score=0.7,
    )
    if not relevant_ids:
        # Validator excluded every candidate — return a clean no-match result rather
        # than silently dumping unfiltered products from the live Shopify search.
        logger.info(
            "Shopify get_products: AI validator excluded all live-API candidates for "
            "query=%r; returning empty result",
            original_user_query,
        )
        return _build_products_result([], [])

    id_to_product = {p.id: p for p in products}
    id_to_artifact = {a["id"]: a for a in artifacts}
    final_products = [id_to_product[pid] for pid in relevant_ids if pid in id_to_product]
    final_artifacts = [id_to_artifact[pid] for pid in relevant_ids if pid in id_to_artifact]

    return _build_products_result(final_products, final_artifacts)


def _build_products_result(
    products: List[ShopifyProduct],
    artifacts: List[dict[str, Any]],
) -> tuple[GetProductsResult, dict[str, Any]]:
    """Build the standard success return tuple for any get_products path."""
    message = "Nothing found" if not products else "Found"
    return (
        GetProductsResult(success=True, message=message, products=products),
        {"name": "Shopify", "tool": "Get Products", "data": artifacts},
    )


def _products_error(message: str) -> tuple[GetProductsResult, list]:
    """Build a failure return tuple for get_products."""
    return (
        GetProductsResult(success=False, message=message, products=[]),
        [],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: data parsing
# ─────────────────────────────────────────────────────────────────────────────

def _coerce_float(raw: Any) -> float | None:
    """Parse a price-like value to float, tolerating strings and None."""
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _collection_variant_to_info(v: dict) -> dict:
    """Map a user-backend ProductCard variant into the variant shape get_products emits."""
    return {
        "id": v.get("id"),
        "externalId": v.get("externalId") or v.get("id"),
        "name": v.get("name") or v.get("title"),
        "displayName": v.get("displayName"),
        "price": _coerce_float(v.get("price")),
        "compareAtPrice": _coerce_float(v.get("compareAtPrice")),
        "availableForSale": v.get("availableForSale", v.get("inStock")),
        "inventoryQuantity": v.get("inventoryQuantity"),
        "sellableOnlineQuantity": v.get("sellableOnlineQuantity"),
    }


def _collection_card_to_artifact(card: dict) -> dict:
    """Map a user-backend ProductCard into the same artifact shape `get_products` emits,
    so the widget renders collection products with identical product cards.
    """
    image_url = card.get("image")
    images = card.get("images") or ([image_url] if image_url else [])
    currency = card.get("currency") or "USD"
    price = _coerce_float(card.get("price"))
    price_range = f"{currency} {card.get('price')}" if price is not None else None
    variants = [
        _collection_variant_to_info(v)
        for v in (card.get("variants") or [])
        if isinstance(v, dict)
    ]
    return {
        "id": card.get("id"),
        "externalId": None,
        "name": card.get("name"),
        "handle": card.get("handle") or "",
        "description": card.get("description"),
        "image_url": image_url,
        "images": images,
        "product_url": card.get("productUrl"),
        "total_inventory": None,
        "availableForSale": card.get("inStock"),
        "price_range": price_range,
        "currencyCode": currency,
        "variants": variants,
    }


def _build_collection_content(collection_name: str, cards: list) -> str:
    """Build the LLM-facing prose summary of a collection's live products."""
    lines = [f"Collection: {collection_name}"]
    for card in cards:
        if not isinstance(card, dict):
            continue
        name = (card.get("name") or "").strip()
        if not name:
            continue
        price = card.get("price")
        currency = card.get("currency") or ""
        price_str = f" — {currency} {price}".rstrip() if price is not None else ""
        stock = " (out of stock)" if card.get("inStock") is False else ""
        lines.append(f"- {name}{price_str}{stock}")
    if len(lines) == 1:
        lines.append("(no products in this collection)")
    return "\n".join(lines)


def _parse_product_node(node: dict) -> tuple[ShopifyProduct, dict]:
    """Parse a Shopify GraphQL product node into a (ShopifyProduct, artifact dict) pair."""
    image_url = None
    images = []
    for media_node in node.get("media", {}).get("nodes", []):
        image_data = media_node.get("image", {})
        if image_data and image_data.get("url"):
            images.append(image_data["url"])
    if images:
        image_url = images[0]

    price_range_data = node.get("priceRangeV2")
    currency_code = "USD"
    if price_range_data:
        min_price = price_range_data.get("minVariantPrice", {})
        max_price = price_range_data.get("maxVariantPrice", {})
        if min_price and max_price:
            min_amount = min_price.get("amount", "0")
            max_amount = max_price.get("amount", "0")
            currency_code = min_price.get("currencyCode", "USD")
            if min_amount == max_amount:
                price_range_data = f"{currency_code} {min_amount}"
            else:
                price_range_data = f"{currency_code} {min_amount} - {max_amount}"
        else:
            price_range_data = None

    variant_edges = node.get("variants", {}).get("edges", [])
    variants = [e["node"] for e in variant_edges]
    total_inventory = node.get("totalInventory")
    if total_inventory is None and variants:
        all_null = all(v.get("inventoryQuantity") is None for v in variants)
        total_inventory = (
            None if all_null
            else sum((v.get("inventoryQuantity") or 0) for v in variants)
        )

    any_available_for_sale: bool | None = (
        any(v.get("availableForSale", False) for v in variants) if variants else None
    )
    product_url = node.get("onlineStoreUrl") or node.get("onlineStorePreviewUrl") or None

    product = ShopifyProduct(
        id=node["id"],
        name=node["title"],
        description=_sanitize_html_description(node.get("description")),
        product_url=product_url,
        total_inventory=total_inventory,
        price_range=price_range_data,
    )
    variant_info = [
        {
            "id": v.get("id"),
            "externalId": v.get("id"),
            "name": v.get("title"),
            "displayName": v.get("displayName"),
            "price": float(v["price"]) if v.get("price") else None,
            "compareAtPrice": float(v["compareAtPrice"]) if v.get("compareAtPrice") else None,
            "availableForSale": v.get("availableForSale"),
            "inventoryQuantity": v.get("inventoryQuantity"),
            "sellableOnlineQuantity": v.get("sellableOnlineQuantity"),
        }
        for v in variants
    ]
    artifact = {
        "id": node["id"],
        "externalId": None,
        "name": node["title"],
        "handle": node.get("handle") or "",
        "description": _sanitize_html_description(node.get("description")),
        "image_url": image_url,
        "images": images,
        "product_url": product_url,
        "total_inventory": total_inventory,
        "availableForSale": any_available_for_sale,
        "price_range": price_range_data,
        "currencyCode": currency_code,
        "variants": variant_info,
    }
    return product, artifact


def _parse_order_node(order_data: dict) -> ShopifyOrder:
    """Parse a Shopify GraphQL order node into a ShopifyOrder model."""
    return ShopifyOrder(
        id=order_data["id"],
        name=order_data["name"],
        status_url=order_data.get("statusPageUrl"),
        display_financial_status=order_data.get("displayFinancialStatus"),
        display_fulfillment_status=order_data.get("displayFulfillmentStatus"),
        cancelled_at=order_data.get("cancelledAt"),
        closed_at=order_data.get("closedAt"),
        fulfillment_orders=order_data.get("fulfillmentOrders", {}).get("nodes", []),
        fulfillments=order_data.get("fulfillments", []),
        created_at=order_data.get("createdAt"),
        total_price=order_data.get("totalPriceSet", {}).get("shopMoney", {}).get("amount"),
        currency_code=order_data.get("totalPriceSet", {}).get("shopMoney", {}).get("currencyCode"),
    )
