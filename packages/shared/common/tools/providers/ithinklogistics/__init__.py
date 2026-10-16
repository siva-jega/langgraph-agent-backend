"""iThink Logistics provider: track orders, create shipments, cancel orders, check pincode, get rates."""

from typing import List, Optional

from langchain_core.tools import BaseTool

from .tools import get_ithinklogistics_tools


class _IThinkLogisticsTools:
    """iThink Logistics tools provider."""

    def __init__(self) -> None:
        self._tools: List[BaseTool] = []
        for tool in get_ithinklogistics_tools():
            self._tools.append(tool)

    def get_all(self) -> List[BaseTool]:
        """Return all iThink Logistics tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[BaseTool]:
        """Return a tool by name."""
        for t in self._tools:
            if t.name == name:
                return t
        return None


IThinkLogisticsTools = _IThinkLogisticsTools()

__all__ = ["IThinkLogisticsTools", "get_ithinklogistics_tools"]
