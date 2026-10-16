"""WooCommerce tools: get_products, get_order_status."""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from common.modules.retrieval import RetrievalResponse
from common.integration import get_retrieval
from common.tools.providers.woocommerce.models import (
    GetOrderStatusResult,
    GetProductsResult,
    WooCommerceOrder,
)
from common.tools.providers.woocommerce.utils import (
    get_woocommerce_credentials,
    make_woocommerce_request,
    process_retrieved_products,
    validate_products_with_ai,
)

logger = logging.getLogger(__name__)


async def _retrieve_products(
    query_for_dense_search: str,
    query_for_sparse_search: str,
    folder_ids: List[str],
) -> tuple[list[RetrievalResponse], list[RetrievalResponse]]:
    """Run dense and sparse retrieval for product search."""
    retrieval_service = get_retrieval()
    retrieved_dense_nodes, retrieved_sparse_nodes = await asyncio.gather(
        retrieval_service.retrieve(
            text=query_for_dense_search,
            entity_ids=[],
            folder_ids=folder_ids,
            collection_name="b2c",
        ),
        retrieval_service.retrieve_sparse(
            text=query_for_sparse_search,
            entity_ids=[],
            folder_ids=folder_ids,
            collection_name="b2c",
        ),
    )
    return retrieved_dense_nodes, retrieved_sparse_nodes


@tool(response_format="content_and_artifact")
async def get_products(
    config: RunnableConfig,
    original_user_query: str,
    query_for_dense_search: str,
    query_for_sparse_search: str,
    limit: int = 10,
) -> Tuple[GetProductsResult, Any]:
    """Get products from WooCommerce by providing an original_user_query, query_for_dense_search,
    query_for_sparse_search and optional limit.

    Args:
        original_user_query: The original user query without any modifications.
        query_for_dense_search: The query text for optimal dense search in vector database.
        query_for_sparse_search: The query text for optimal sparse search in vector database.
        limit: Maximum number of products to return (default: 10).

    Returns:
        A GetProductsResult containing success status, message, and products.
    """
    try:
        logger.info(
            "WooCommerce get_products: query=%s",
            original_user_query[:80],
        )
        configurable = config.get("configurable", {})
        user_id = configurable.get("user_id")
        if not user_id:
            return (
                GetProductsResult(
                    success=False,
                    message="No user_id provided in configuration",
                    products=[],
                ),
                [],
            )

        apps_config = configurable.get("apps_config", [])
        wc_app = next(
            (a for a in apps_config if a.get("slug") == "woocommerce"), None
        )
        if wc_app and wc_app.get("ingestion_status") == "SUCCESS":
            app_id = wc_app.get("id")
            if app_id:
                dense_nodes, sparse_nodes = await _retrieve_products(
                    query_for_dense_search=query_for_dense_search,
                    query_for_sparse_search=query_for_sparse_search,
                    folder_ids=[app_id],
                )
                all_retrieved_nodes = dense_nodes + sparse_nodes
                products, artifacts = await process_retrieved_products(all_retrieved_nodes)
                if products:
                    final_products, final_artifacts = await validate_products_with_ai(
                        user_query=original_user_query,
                        products=products,
                        artifacts=artifacts,
                        min_relevance_score=0.7,
                    )
                    if final_products:
                        return (
                            GetProductsResult(
                                success=True,
                                message="Found",
                                products=final_products,
                            ),
                            {
                                "name": "WooCommerce",
                                "tool": "Get Products",
                                "data": final_artifacts,
                            },
                        )
        return (
            GetProductsResult(
                success=True,
                message="Nothing found",
                products=[],
            ),
            {"name": "WooCommerce", "tool": "Get Products", "data": []},
        )
    except Exception as e:
        logger.error("Error getting WooCommerce products: %s", e)
        return (
            GetProductsResult(
                success=False,
                message="Error getting products: %s" % str(e),
                products=[],
            ),
            [],
        )


def _get_wc_connector_id(config: RunnableConfig) -> Optional[str]:
    """Extract the WooCommerce connector ID from the runtime config."""
    configurable = config.get("configurable", {})
    all_apps = configurable.get("apps_config", []) or configurable.get("apps", [])
    wc_app = next(
        (a for a in all_apps if (a.get("slug") or "").lower() == "woocommerce"),
        None,
    )
    return wc_app.get("id") if wc_app else None


def _parse_wc_order(data: Dict[str, Any]) -> WooCommerceOrder:
    """Build a WooCommerceOrder from a WC REST API order dict."""
    status = data.get("status", "")
    return WooCommerceOrder(
        id=str(data.get("id", "")),
        name=str(data.get("number") or data.get("id", "")),
        display_financial_status=status,
        display_fulfillment_status=data.get("status"),
        created_at=data.get("date_created"),
        total_price=data.get("total"),
        currency_code=data.get("currency"),
        cancelled_at=data.get("date_completed") if status == "cancelled" else None,
        closed_at=data.get("date_completed") if status in ("completed", "refunded") else None,
        fulfillment_orders=None,
        fulfillments=None,
    )


@tool(response_format="content_and_artifact")
async def get_order_status(
    config: RunnableConfig,
    order_id: Optional[str] = None,
    customer_email: Optional[str] = None,
) -> Tuple[GetOrderStatusResult, Any]:
    """Get WooCommerce order status by order ID or customer email.

    Provide either order_id for a single order lookup, or customer_email
    to fetch all orders for that customer. Do NOT ask the user for technical
    details — infer the order ID or email from context.

    Args:
        config: Runtime configuration.
        order_id: Numeric order ID (e.g. "1234").
        customer_email: Customer email to look up all their orders.
    """
    try:
        if not order_id and not customer_email:
            return (
                GetOrderStatusResult(
                    success=False,
                    message="Please provide an order ID or customer email to look up.",
                ),
                [],
            )

        configurable = config.get("configurable", {})
        user_id = configurable.get("user_id")
        if not user_id:
            return (
                GetOrderStatusResult(
                    success=False,
                    message="No user_id provided in configuration",
                ),
                [],
            )

        connector_id = _get_wc_connector_id(config)
        creds = await get_woocommerce_credentials(user_id, connector_id)
        if not creds:
            return (
                GetOrderStatusResult(
                    success=False,
                    message="WooCommerce is not connected. Please connect it in the Apps section first.",
                ),
                [],
            )

        store_url = creds["store_url"]
        consumer_key = creds["consumer_key"]
        consumer_secret = creds["consumer_secret"]

        if order_id:
            data = await make_woocommerce_request(
                store_url=store_url,
                consumer_key=consumer_key,
                consumer_secret=consumer_secret,
                method="GET",
                endpoint=f"orders/{order_id}",
            )

            if not data or not isinstance(data, dict):
                return (
                    GetOrderStatusResult(
                        success=False,
                        message=f"Order #{order_id} not found",
                    ),
                    [],
                )

            order = _parse_wc_order(data)
            return (
                GetOrderStatusResult(
                    success=True,
                    message=f"Found order status for #{order.name}",
                    order=order,
                ),
                {
                    "name": "WooCommerce",
                    "tool": "Get Order Status",
                    "data": order.model_dump(),
                },
            )

        data = await make_woocommerce_request(
            store_url=store_url,
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            method="GET",
            endpoint="orders",
            params={"search": customer_email, "per_page": 20},
        )

        if not data or not isinstance(data, list) or len(data) == 0:
            return (
                GetOrderStatusResult(
                    success=False,
                    message=f"No orders found for {customer_email}",
                ),
                [],
            )

        orders = [_parse_wc_order(o) for o in data]
        return (
            GetOrderStatusResult(
                success=True,
                message=f"Found {len(orders)} order(s) for {customer_email}",
                orders=orders,
            ),
            {
                "name": "WooCommerce",
                "tool": "Get Order Status",
                "data": [o.model_dump() for o in orders],
            },
        )

    except Exception as e:
        logger.error("Error getting WooCommerce order status: %s", e)
        return (
            GetOrderStatusResult(
                success=False,
                message=f"Error getting order status: {e}",
            ),
            [],
        )


def get_woocommerce_tools() -> List[BaseTool]:
    """Return all WooCommerce tools."""
    return [get_products, get_order_status]
