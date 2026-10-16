from typing import List
import logging
from tavily import AsyncTavilyClient
from langchain_core.tools import tool, Tool
from pydantic import BaseModel
import os
import dotenv
from .models import WebSearchResult
from common.tools.utils.writer import emit_update


dotenv.load_dotenv()

logger = logging.getLogger(__name__)
@tool(response_format="content_and_artifact")
async def web_search(query_text: str) -> List[WebSearchResult]:
    """Search the web with Tavily by providing a query optimized for web search.

    Args:
        query_text: The search query to execute

    Returns:
        A list of WebSearchResult objects containing URLs, titles, content snippets, and scores
    """
    try:
        emit_update(
            {"tool": "Web Search", "update": f"Searching the web for: {query_text}"},
            "web_search",
        )
        search_response = await AsyncTavilyClient(
            api_key=os.getenv("TAVILY_API_KEY")
        ).search(query_text, max_results=5, search_depth="basic")

        web_search_results = []
        for result in search_response["results"]:
            web_search_results.append(
                WebSearchResult(
                    url=result["url"],
                    title=result["title"],
                    content=result["content"],
                    score=result["score"],
                )
            )

        if web_search_results:
            top_titles = ", ".join(r.title for r in web_search_results[:3])
            emit_update(
                {
                    "tool": "Web Search",
                    "update": f"Found {len(web_search_results)} results : {top_titles}",
                },
                "web_search",
            )

        return web_search_results, {
            "name": "Web Search",
            "tool": "Web Search",
            "data": web_search_results,
        }
    except Exception as e:
        logger.exception("Error performing web search: %s", e)
        return [WebSearchResult(url="", title="", content="", score=0)], {
            "name": "Web Search",
            "tool": "Web Search",
            "data": [WebSearchResult(url="", title="", content="", score=0)],
        }


def get_web_tools() -> List[Tool]:
    """Get all web tools."""
    return [web_search]
