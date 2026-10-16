from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "get_products": {
        "id": "get_products",
        "name": "Get Products",
        "description": "Search WooCommerce products and return relevant product details for the user.",
    },
    "get_order_status": {
        "id": "get_order_status",
        "name": "Get Order Status",
        "description": "Fetch WooCommerce order status by order id, or list orders for a customer email.",
    },
}

__all__ = ["TOOLS_DATA"]

