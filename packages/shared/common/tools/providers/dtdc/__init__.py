"""DTDC provider: track consignments, create shipments, cancel orders, check serviceability, get rates."""

from typing import List, Optional

from langchain_core.tools import BaseTool

from .tools import get_dtdc_tools


class _DTDCTools:
    """DTDC tools provider."""

    def __init__(self) -> None:
        self._tools: List[BaseTool] = []
        for tool in get_dtdc_tools():
            self._tools.append(tool)

    def get_all(self) -> List[BaseTool]:
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[BaseTool]:
        for t in self._tools:
            if t.name == name:
                return t
        return None


DTDCTools = _DTDCTools()

__all__ = ["DTDCTools", "get_dtdc_tools"]
