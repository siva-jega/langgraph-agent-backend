from typing import List, Optional
from langchain_core.tools import Tool
from .tools import get_knowledgebase_tools


class KnowledgeBaseTools:
    """Knowledge base tools provider."""

    def __init__(self):
        self._tools: List[Tool] = []
        # Load knowledge base tools
        tools = get_knowledgebase_tools()
        for tool in tools:
            self._tools.append(tool)

    def get_all(self) -> List[Tool]:
        """Get all knowledge base tools."""
        return self._tools.copy()

    def get_tool_by_name(self, name: str) -> Optional[Tool]:
        """Get a specific knowledge base tool by name."""
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None


# Create global instance
KnowledgeBaseTools = KnowledgeBaseTools()

__all__ = ["KnowledgeBaseTools"]
