"""Shiprocket provider: track orders, create shipments, cancel orders, check serviceability, get rates."""

from typing import List, Optional

from langchain_core.tools import BaseTool

from .tools import get_shiprocket_tools


class _ShiprocketTools:
    """Shiprocket tools provider."""

    def __init__(self) -> None:
        self._tools: List[BaseTool] = []
        for tool in get_shiprocket_tools():
            self._tools.append(tool)

    def get_all(self) -> List[BaseTool]:
        """Return all Shiprocket tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[BaseTool]:
        """Return a tool by name."""
        for t in self._tools:
            if t.name == name:
                return t
        return None


ShiprocketTools = _ShiprocketTools()

__all__ = ["ShiprocketTools", "get_shiprocket_tools"]
