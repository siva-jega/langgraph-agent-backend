"""WareIQ provider: track orders, create shipments, cancel orders, check serviceability, get rates."""

from typing import List, Optional

from langchain_core.tools import BaseTool

from .tools import get_wareiq_tools


class _WareIQTools:
    """WareIQ tools provider."""

    def __init__(self) -> None:
        self._tools: List[BaseTool] = []
        for tool in get_wareiq_tools():
            self._tools.append(tool)

    def get_all(self) -> List[BaseTool]:
        """Return all WareIQ tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[BaseTool]:
        """Return a tool by name."""
        for t in self._tools:
            if t.name == name:
                return t
        return None


WareIQTools = _WareIQTools()

__all__ = ["WareIQTools", "get_wareiq_tools"]
