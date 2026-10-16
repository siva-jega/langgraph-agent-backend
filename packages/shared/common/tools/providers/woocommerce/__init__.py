"""WooCommerce provider: get_products and get_order_status tools."""

from typing import List, Optional

from langchain_core.tools import BaseTool

from .tools import (
    get_order_status,
    get_products,
    get_woocommerce_tools,
)


class WooCommerceTools:
    """WooCommerce tools provider."""

    def __init__(self) -> None:
        self._tools: List[BaseTool] = []
        tools = get_woocommerce_tools()
        for tool in tools:
            self._tools.append(tool)

    def get_all(self) -> List[BaseTool]:
        """Return all WooCommerce tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[BaseTool]:
        """Return a tool by name."""
        for t in self._tools:
            if t.name == name:
                return t
        return None


WooCommerceTools = WooCommerceTools()

__all__ = ["WooCommerceTools", "get_products", "get_order_status", "get_woocommerce_tools"]
