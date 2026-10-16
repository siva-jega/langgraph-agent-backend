"""Utility functions for Notion integration."""

import asyncio
from typing import Optional, Dict, Any, List
import requests
from common.integration import get_token
from common.modules.token import TokenResponse
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from .models import NotionPage, NotionBlock, NotionDatabase, NotionUser, NotionComment

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(5)


async def get_notion_credentials(user_id: str, connectorId: str | None = None) -> Optional[TokenResponse]:
    """Get Notion credentials from token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id, connector_name="notion", connectorId=connectorId, needs_refresh=False
        )
        return user_data
    except Exception as e:
        logger.error(f"Error getting Notion credentials: {str(e)}")
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
)
async def execute_notion_request(
    method: str, url: str, access_token: str, **kwargs
) -> Any:
    """Execute Notion API request with retry logic."""
    async with _semaphore:
        for attempt in range(3):
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {access_token}"
            headers["Notion-Version"] = "2022-06-28"
            headers["Content-Type"] = "application/json"
            try:
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(
                    None,
                    lambda: requests.request(method, url, headers=headers, **kwargs),
                )
                if resp.status_code == 401 and attempt < 2:
                    # Token refresh would be handled by caller
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == 2:
                    raise Exception(f"Notion API error: {str(e)}")
                await asyncio.sleep(2**attempt)


def parse_page(page: Dict[str, Any]) -> NotionPage:
    """Parse raw Notion page into NotionPage object."""
    title = ""
    properties = page.get("properties", {})
    for prop in properties.values():
        if prop.get("type") == "title":
            title_arr = prop.get("title", [])
            if title_arr:
                title = title_arr[0].get("plain_text", "")
    return NotionPage(
        id=page["id"],
        title=title,
        created_time=page.get("created_time", ""),
        last_edited_time=page.get("last_edited_time", ""),
        url=page.get("url", ""),
        properties=properties,
        is_archived=page.get("archived", False),
    )


def parse_block(block: Dict[str, Any]) -> NotionBlock:
    """Parse raw Notion block into NotionBlock object."""
    return NotionBlock(
        id=block["id"],
        type=block.get("type", ""),
        created_time=block.get("created_time", ""),
        last_edited_time=block.get("last_edited_time", ""),
        has_children=block.get("has_children", False),
        archived=block.get("archived", False),
        parent=block.get("parent", {}),
    )


def parse_database(database: Dict[str, Any]) -> NotionDatabase:
    """Parse raw Notion database into NotionDatabase object."""
    title = ""
    title_arr = database.get("title", [])
    if title_arr:
        title = title_arr[0].get("plain_text", "")

    description = ""
    desc_arr = database.get("description", [])
    if desc_arr:
        description = desc_arr[0].get("plain_text", "")

    return NotionDatabase(
        id=database["id"],
        title=title,
        description=description,
        properties=database.get("properties", {}),
        created_time=database.get("created_time", ""),
        last_edited_time=database.get("last_edited_time", ""),
        url=database.get("url", ""),
        is_inline=database.get("is_inline", False),
        archived=database.get("archived", False),
    )


def parse_user(user: Dict[str, Any]) -> NotionUser:
    """Parse raw Notion user into NotionUser object."""
    return NotionUser(
        id=user["id"],
        name=user.get("name", ""),
        avatar_url=user.get("avatar_url"),
        type=user.get("type", ""),
        person=user.get("person"),
    )


def parse_comment(comment: Dict[str, Any]) -> NotionComment:
    """Parse raw Notion comment into NotionComment object."""
    return NotionComment(
        id=comment["id"],
        parent=comment.get("parent", {}),
        discussion_id=comment.get("discussion_id", ""),
        created_time=comment.get("created_time", ""),
        last_edited_time=comment.get("last_edited_time", ""),
        created_by=comment.get("created_by", {}),
        rich_text=comment.get("rich_text", []),
    )
