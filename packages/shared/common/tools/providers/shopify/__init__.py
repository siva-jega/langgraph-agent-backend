from typing import List, Optional
from langchain_core.tools import Tool
from .tools import get_shopify_tools


class _ShopifyToolsProvider:
    """Shopify tools provider."""

    def __init__(self):
        self._tools: List[Tool] = []
        tools = get_shopify_tools()
        for tool in tools:
            self._tools.append(tool)

    def get_all(self) -> List[Tool]:
        """Get all Shopify tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """Get a specific Shopify tool by name."""
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None


# Registry expects a singleton instance (not the class).
ShopifyTools = _ShopifyToolsProvider()

__all__ = ["ShopifyTools"]
