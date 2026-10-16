"""Utility functions for Google Drive integration."""

import asyncio
from typing import Optional, Dict, Any, List
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from common.integration import get_token
from common.modules.token import TokenResponse
from common.modules.retrieval import RetrievalResponse
import logging
import json
from tenacity import retry, stop_after_attempt, wait_exponential, RetryError
from .models import DriveItem

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(5)


async def get_gdrive_credentials(user_id: str, connectorId: str | None = None) -> Optional[TokenResponse]:
    """Get Google Drive credentials from token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id, connector_name="googledrive", connectorId=connectorId, needs_refresh=True
        )
        return user_data
    except Exception as e:
        logger.error(f"Error getting Google Drive credentials: {str(e)}")
        return None


def build_gdrive_service(access_token: str):
    """Build Google Drive service instance from access token."""
    credentials = Credentials(token=access_token)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    reraise=True,
)
async def execute_gdrive_request(request: Any) -> Any:
    """Execute Google Drive API request with retry logic."""
    async with _semaphore:
        try:
            response = request.execute()
            logger.debug(f"Google Drive API response received. Type: {type(response)}")
            return response
        except HttpError as e:
            status = e.resp.status if e.resp else None
            logger.error(f"Google Drive API HttpError: status={status}, error={e}")
            if status in [429, 500, 502, 503, 504]:
                raise Exception(
                    f"Google Drive API server/quota error: {status} - {e}"
                )
            if status in [401, 403]:
                raise ValueError("Google Drive credentials expired")
            raise Exception(f"Google Drive API error: {status} - {e}")
        except RetryError as e:
            # Extract underlying exception from RetryError
            underlying_error = e.last_attempt.exception() if e.last_attempt else e
            error_msg = f"RetryError after {e.last_attempt.attempt_number if e.last_attempt else 'unknown'} attempts: {underlying_error}"
            logger.error(f"Google Drive API RetryError: {error_msg}")
            raise underlying_error if underlying_error else e
        except Exception as e:
            error_msg = f"An unexpected error occurred with Google Drive API: {type(e).__name__}: {e}"
            logger.error(error_msg, exc_info=True)
            raise Exception(error_msg) from e


def parse_drive_item(item_data: Dict[str, Any]) -> DriveItem:
    """Parse raw Google Drive item data into DriveItem Pydantic model."""
    return DriveItem.model_validate(item_data)


def process_retrieved_drive_items(
    retrieved_nodes: List[RetrievalResponse],
) -> tuple[List[DriveItem], List[Dict[str, Any]]]:
    """
    Process retrieved nodes from vector database into DriveItem objects and artifacts.

    Args:
        retrieved_nodes: List of RetrievalResponse objects containing drive item data

    Returns:
        Tuple of (items list, artifacts list)
    """
    items = []
    artifacts = []

    for node in retrieved_nodes:
        try:
            # Try to parse metadata first (contains file information)
            metadata = {}
            if node.metadata:
                try:
                    if isinstance(node.metadata, str):
                        metadata = json.loads(node.metadata)
                    elif isinstance(node.metadata, dict):
                        metadata = node.metadata
                except (json.JSONDecodeError, TypeError):
                    # If metadata parsing fails, continue with empty dict
                    logger.warning(f"Failed to parse metadata: {node.metadata}")
                    metadata = {}
            
            # Extract file information from metadata
            file_id = metadata.get("item_id") or metadata.get("file_id", "")
            file_name = metadata.get("item_name", "")
            mime_type = metadata.get("mime_type", "application/octet-stream")
            page_number = metadata.get("page_number")
            
            # Try to parse text as JSON first (for structured data)
            # If that fails, treat it as document content
            item_data = None
            document_content = None
            
            try:
                item_data = json.loads(node.text)
                # If successful, this is structured JSON data
            except json.JSONDecodeError:
                # Text is not JSON, it's document content
                document_content = node.text
            
            # If we have structured JSON data, use it to create DriveItem
            if item_data:
                drive_item = parse_drive_item(item_data)
                items.append(drive_item)
                
                # Create artifact entry from structured data
                artifact_entry = {
                    "id": drive_item.id,
                    "name": drive_item.name,
                    "mime_type": drive_item.mime_type,
                    "created_time": drive_item.created_time,
                    "modified_time": drive_item.modified_time,
                    "owners": drive_item.owners,
                    "parents": drive_item.parents,
                    "web_view_link": drive_item.web_view_link,
                    "icon_link": drive_item.icon_link,
                    "size": drive_item.size,
                    "capabilities": drive_item.capabilities,
                    "shared": drive_item.shared,
                    "description": drive_item.description,
                    "starred": drive_item.starred,
                    "trashed": drive_item.trashed,
                    "content": drive_item.content,  # Include content if present
                }
                artifacts.append(artifact_entry)
            else:
                # Skip if we don't have required fields (id and name)
                if not file_id or not file_name:
                    logger.warning(
                        f"Skipping node with missing required fields: file_id={file_id}, file_name={file_name}"
                    )
                    continue
                
                # Create DriveItem from metadata with document content
                drive_item = DriveItem(
                    id=file_id,
                    name=file_name,
                    mimeType=mime_type,
                    content=document_content,  # Include document content in DriveItem
                )
                items.append(drive_item)
                
                # Create artifact entry with document content
                artifact_entry = {
                    "id": file_id,
                    "name": file_name,
                    "mime_type": mime_type,
                    "file_id": metadata.get("file_id"),
                    "page_number": page_number,
                    "content": document_content,  # Include the actual document content
                    "connector_type": metadata.get("connector_type"),
                    "export_mime_type": metadata.get("export_mime_type"),
                }
                artifacts.append(artifact_entry)

        except json.JSONDecodeError as e:
            logger.error(f"Error parsing drive item JSON: {str(e)}")
            continue
        except Exception as e:
            logger.error(f"Error processing drive item: {str(e)}")
            continue

    return items, artifacts