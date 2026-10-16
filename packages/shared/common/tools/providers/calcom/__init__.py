"""Cal.com provider: list/create event types, book meetings, cancel bookings."""

from typing import List, Optional

from langchain_core.tools import BaseTool

from .tools import get_calcom_tools


class CalComTools:
    """Cal.com tools provider."""

    def __init__(self) -> None:
        self._tools: List[BaseTool] = []
        for tool in get_calcom_tools():
            self._tools.append(tool)

    def get_all(self) -> List[BaseTool]:
        """Return all Cal.com tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[BaseTool]:
        """Return a tool by name."""
        for t in self._tools:
            if t.name == name:
                return t
        return None


CalComTools = CalComTools()

__all__ = ["CalComTools", "get_calcom_tools"]
