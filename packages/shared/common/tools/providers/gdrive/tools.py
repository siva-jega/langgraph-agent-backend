"""Google Drive tools for managing files and folders."""

from typing import List, Optional, Union
from langchain_core.tools import tool, Tool
from langchain_core.runnables import RunnableConfig
import logging
from .models import (
    DriveItem,
    ListItemsResult,
    SearchFilesResult,
    GetFileMetadataResult,
    SharedFilesResult,
)
from .utils import (
    build_gdrive_service,
    execute_gdrive_request,
    get_gdrive_credentials,
    parse_drive_item,
)
from common.tools.utils.writer import emit_update
from tenacity import RetryError

logger = logging.getLogger(__name__)


def _extract_retry_error(retry_error: RetryError) -> tuple[Exception, str]:
    """Extract underlying exception and error message from RetryError."""
    try:
        if retry_error.last_attempt:
            attempt = retry_error.last_attempt
            attempt_num = getattr(attempt, "attempt_number", "unknown")
            try:
                underlying_error = attempt.exception()
                if underlying_error:
                    error_msg = (
                        f"RetryError after {attempt_num} attempts: {underlying_error}"
                    )
                    return underlying_error, error_msg
            except Exception:
                pass
        # Fallback if we can't extract the underlying exception
        error_msg = f"RetryError: {retry_error}"
        return retry_error, error_msg
    except Exception:
        # Final fallback
        return retry_error, f"RetryError: {retry_error}"


@tool(response_format="content_and_artifact")
async def list_drive_items(
    folder_id: Optional[str] = None,
    query_filter: Optional[str] = None,
    page_size: int = 20,
    order_by: Optional[str] = "folder,modifiedTime desc",
    page_token: Optional[str] = None,
    config: RunnableConfig = None,
) -> ListItemsResult:
    """
    List files and folders in Google Drive with concurrent fetching.

    Args:
        folder_id: Folder ID to list items from (defaults to root)
        query_filter: Filter query for specific items (e.g., "mimeType='image/jpeg'")
        page_size: Maximum number of items to return
        order_by: Ordering criteria
        page_token: Pagination token
        config: Configuration containing user_id.

    Returns:
        ListItemsResult: Dictionary with items, nextPageToken, and optional error
    """
    try:
        if page_size > 1000:
            raise ValueError("page_size must be less than or equal to 1000")

        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "drive" in slug or "google" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_gdrive_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Drive credentials")

        service = build_gdrive_service(credentials.token)

        emit_update(
            {
                "tool": "Google Drive",
                "update": f"Listing items with folder_id: {folder_id}, page_size: {page_size}",
            },
            "gdrive",
        )

        q_parts = []
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        else:
            q_parts.append("'root' in parents")

        if query_filter:
            q_parts.append(f"({query_filter})")
        q_parts.append("trashed = false")
        final_query = " and ".join(q_parts)

        fields = "nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, owners, parents, webViewLink, iconLink, size, capabilities, shared, description, starred, trashed)"
        try:
            request = service.files().list(
                q=final_query,
                pageSize=min(page_size, 1000),
                fields=fields,
                orderBy=order_by,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            response = await execute_gdrive_request(request)
            items = [
                parse_drive_item(item_data) for item_data in response.get("files", [])
            ]
            # Create artifacts matching retrieval format
            artifacts = [
                {
                    "id": item.id,
                    "name": item.name,
                    "mime_type": item.mime_type,
                    "created_time": item.created_time,
                    "modified_time": item.modified_time,
                    "owners": item.owners,
                    "parents": item.parents,
                    "web_view_link": item.web_view_link,
                    "icon_link": item.icon_link,
                    "size": item.size,
                    "capabilities": item.capabilities,
                    "shared": item.shared,
                    "description": item.description,
                    "starred": item.starred,
                    "trashed": item.trashed,
                }
                for item in items
            ]
            return {
                "items": items,
                "nextPageToken": response.get("nextPageToken"),
            }, {
                "name": "Google Drive",
                "tool": "List Drive Items",
                "data": artifacts,
            }
        except RetryError as e:
            # Extract underlying exception from RetryError
            underlying_error, error_msg = _extract_retry_error(e)
            logger.error(f"Error listing Drive items: {error_msg}")
            return {
                "items": [],
                "nextPageToken": None,
                "error": str(underlying_error),
            }, {
                "name": "Google Drive",
                "tool": "List Drive Items",
                "error": str(underlying_error),
            }
        except Exception as e:
            logger.error(f"Error listing Drive items: {str(e)}")
            return {"items": [], "nextPageToken": None, "error": str(e)}, {
                "name": "Google Drive",
                "tool": "List Drive Items",
                "error": str(e),
            }
    except RetryError as e:
        # Extract underlying exception from RetryError
        underlying_error, error_msg = _extract_retry_error(e)
        logger.error(f"Error listing Drive items: {error_msg}")
        raise underlying_error
    except Exception as e:
        logger.error(f"Error listing Drive items: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def search_drive_files(
    config: RunnableConfig,
    search_llm_query: str,
    query: str,
    order_by: Optional[str] = None,
    page_size: int = 20,
    page_token: Optional[str] = None,
) -> SearchFilesResult:
    """
    Search for files and folders in Google Drive using Google Drive query language.

    Args:
        search_llm_query: The LLM query to search for files in vector database.
        query: Search query to be used to search for files and folders, all queries should be a valid Google query
        order_by: Ordering criteria
        page_size: Maximum number of items to return
        page_token: Pagination token
        config: Configuration containing user_id.

    Returns:
        SearchFilesResult: Dictionary with items, nextPageToken, and optional error
    """
    try:
        if page_size > 1000:
            raise ValueError("page_size must be less than or equal to 1000")

        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")
        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "drive" in slug or "google" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_gdrive_credentials(user_id, connectorId)
        # gdriveData = configurable.get("apps", {}).get("Google Drive")
        # if gdriveData:
        #     connectorId = gdriveData.get("config_id")
        #     package = get_package()
        #     retrieval_service = package.get_retrieval()

        #     retrieved_nodes: List[RetrievalResponse] = await retrieval_service.retrieve(
        #         text=search_llm_query,
        #         kids=[connectorId],
        #         collection_name="b2c",
        #     )

        #     # Check if highest score is less than 0.5, if so call Google Drive API directly
        #     highest_score = (
        #         max([node.score for node in retrieved_nodes])
        #         if retrieved_nodes
        #         else 0.0
        #     )
        #     if highest_score < 0.5:
        #         logger.info(
        #             f"Highest retrieval score ({highest_score}) is below 0.5, falling back to Google Drive API"
        #         )
        #         # Fall through to Google Drive API call below
        #     else:
        #         # Process retrieved items using utils function
        #         items, artifacts = process_retrieved_drive_items(retrieved_nodes)

        #         return {
        #             "items": items,
        #             "nextPageToken": None,  # Retrieval doesn't support pagination
        #         }, {
        #             "name": "Google Drive",
        #             "tool": "Search Drive Files",
        #             "data": artifacts,
        #         }

        credentials = await get_gdrive_credentials(user_id)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Drive credentials")

        service = build_gdrive_service(credentials.token)

        emit_update(
            {
                "tool": "Google Drive",
                "update": f"Searching files with query: {query}, page_size: {page_size}",
            },
            "gdrive",
        )

        if not any(
            operator in query for operator in ["=", "!=", ">", "<", "contains", "in"]
        ):
            safe_query = query.replace("'", "\\'")
            formatted_query = f"fullText contains '{safe_query}'"
        else:
            formatted_query = query

        fields = "nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, owners, parents, webViewLink, iconLink, size, capabilities, shared, description, starred, trashed)"
        try:
            request = service.files().list(
                q=formatted_query,
                fields=fields,
                orderBy=order_by,
                supportsAllDrives=True,
                pageSize=min(page_size, 1000),
                pageToken=page_token,
                includeItemsFromAllDrives=True,
            )
            response = await execute_gdrive_request(request)
            items = [
                parse_drive_item(item_data) for item_data in response.get("files", [])
            ]
            return {
                "items": items,
                "nextPageToken": response.get("nextPageToken"),
            }, {
                "name": "Google Drive",
                "tool": "Search Drive Files",
                "data": [
                    {"id": item.id, "name": item.name, "mime_type": item.mime_type}
                    for item in items
                ],
            }
        except RetryError as e:
            # Extract underlying exception from RetryError
            underlying_error, error_msg = _extract_retry_error(e)
            logger.error(f"Error searching Drive files: {error_msg}")
            return {
                "items": [],
                "nextPageToken": None,
                "error": str(underlying_error),
            }, {
                "name": "Google Drive",
                "tool": "Search Drive Files",
                "error": str(underlying_error),
            }
        except Exception as e:
            logger.error(f"Error searching Drive files: {str(e)}")
            return {"items": [], "nextPageToken": None, "error": str(e)}, {
                "name": "Google Drive",
                "tool": "Search Drive Files",
                "error": str(e),
            }
    except RetryError as e:
        # Extract underlying exception from RetryError
        underlying_error, error_msg = _extract_retry_error(e)
        logger.error(f"Error searching Drive files: {error_msg}")
        raise underlying_error
    except Exception as e:
        logger.error(f"Error searching Drive files: {str(e)}")
        raise


@tool
async def get_drive_file_metadata(
    file_id: str,
    config: RunnableConfig = None,
) -> Union[DriveItem, GetFileMetadataResult]:
    """
    Get metadata for a specific file or folder in Google Drive.

    Args:
        file_id: File ID to get metadata for
        config: Configuration containing user_id.

    Returns:
        DriveItem: Parsed Drive item metadata or error dictionary
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")
        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "drive" in slug or "google" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_gdrive_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Drive credentials")

        service = build_gdrive_service(credentials.token)
        emit_update(
            {
                "tool": "Google Drive",
                "update": f"Getting file metadata for ID: {file_id}",
            },
            "gdrive",
        )

        fields = "id, name, mimeType, createdTime, modifiedTime, owners, parents, webViewLink, iconLink, size, capabilities, shared, description, starred, trashed"
        try:
            request = service.files().get(
                fileId=file_id, fields=fields, supportsAllDrives=True
            )
            response = await execute_gdrive_request(request)
            item = parse_drive_item(response)
            emit_update(
                {"tool": "Google Drive", "update": f"Found file: {item.name}"},
                "gdrive",
            )
            return item
        except RetryError as e:
            # Extract underlying exception from RetryError
            underlying_error, error_msg = _extract_retry_error(e)
            logger.error(
                f"Error getting Drive file metadata for ID {file_id}: {error_msg}"
            )
            return {"error": str(underlying_error)}
        except Exception as e:
            logger.error(
                f"Error getting Drive file metadata for ID {file_id}: {str(e)}"
            )
            return {"error": str(e)}
    except RetryError as e:
        # Extract underlying exception from RetryError
        underlying_error, error_msg = _extract_retry_error(e)
        logger.error(f"Error getting Drive file metadata: {error_msg}")
        raise underlying_error
    except Exception as e:
        logger.error(f"Error getting Drive file metadata: {str(e)}")
        raise


@tool
async def shared_files(
    shared_with_email: Optional[str] = None,
    shared_with_me: bool = False,
    page_size: int = 50,
    page_token: Optional[str] = None,
    query_filter: Optional[str] = None,
    config: RunnableConfig = None,
) -> SharedFilesResult:
    """
    List all shared files, or files shared with a specific user.

    Args:
        shared_with_email: Email to filter files shared with
        shared_with_me: Filter files shared with the user
        page_size: Max results
        page_token: For pagination
        query_filter: Filter query for specific items
        config: Configuration containing user_id.

    Returns:
        SharedFilesResult: Dictionary with items and nextPageToken
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")
        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "drive" in slug or "google" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_gdrive_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Drive credentials")

        service = build_gdrive_service(credentials.token)

        q_parts = ["trashed = false"]
        if shared_with_email:
            q_parts.append(
                f"'{shared_with_email}' in readers or '{shared_with_email}' in writers or '{shared_with_email}' in owners"
            )
        if shared_with_me:
            q_parts.append("sharedWithMe")
        if query_filter:
            q_parts.append(query_filter)
        final_query = " and ".join(q_parts)

        fields = "nextPageToken, files(id, name, mimeType, createdTime, modifiedTime, owners, parents, webViewLink, iconLink, size, capabilities, shared, description, starred, trashed)"
        try:
            request = service.files().list(
                q=final_query,
                pageSize=min(page_size, 1000),
                fields=fields,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            response = await execute_gdrive_request(request)
            items = [
                parse_drive_item(item_data) for item_data in response.get("files", [])
            ]
            return {
                "items": items,
                "nextPageToken": response.get("nextPageToken"),
            }
        except RetryError as e:
            # Extract underlying exception from RetryError
            underlying_error, error_msg = _extract_retry_error(e)
            logger.error(f"Error in shared files tool: {error_msg}")
            return {"items": [], "nextPageToken": None, "error": str(underlying_error)}
        except Exception as e:
            logger.error(f"Error in shared files tool: {str(e)}")
            return {"items": [], "nextPageToken": None, "error": str(e)}
    except RetryError as e:
        # Extract underlying exception from RetryError
        underlying_error, error_msg = _extract_retry_error(e)
        logger.error(f"Error in shared files tool: {error_msg}")
        raise underlying_error
    except Exception as e:
        logger.error(f"Error in shared files tool: {str(e)}")
        raise


@tool
async def get_drive_file_content(
    file_id: str,
    config: RunnableConfig = None,
) -> str:
    """
    Get the content of a specific file in Google Drive.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")
        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "drive" in slug or "google" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_gdrive_credentials(user_id, connectorId)
        if not credentials or not credentials.token:
            raise ValueError("Failed to get Google Drive credentials")

        service = build_gdrive_service(credentials.token)
        emit_update(
            {
                "tool": "Google Drive",
                "update": f"Getting file content for ID: {file_id}",
            },
            "gdrive",
        )

        try:
            request = service.files().export(fileId=file_id, mimeType="text/plain")
            response = await execute_gdrive_request(request)
            # For export operations, the response is bytes, not a dictionary
            if isinstance(response, bytes):
                content = response.decode("utf-8")
            elif isinstance(response, dict):
                content = response.get("data", "")
            else:
                content = str(response)
            logger.info(
                f"File content for ID {file_id}: Retrieved {len(content)} characters"
            )
            return content
        except RetryError as e:
            # Extract underlying exception from RetryError
            underlying_error, error_msg = _extract_retry_error(e)
            logger.error(
                f"Error getting Drive file content for ID {file_id}: {error_msg}"
            )
            return str(underlying_error)
        except Exception as e:
            logger.error(f"Error getting Drive file content for ID {file_id}: {str(e)}")
            return str(e)
    except RetryError as e:
        # Extract underlying exception from RetryError
        underlying_error, error_msg = _extract_retry_error(e)
        logger.error(f"Error getting Drive file content: {error_msg}")
        raise underlying_error
    except Exception as e:
        logger.error(f"Error getting Drive file content: {str(e)}")
        raise


def get_gdrive_tools() -> List[Tool]:
    """Get all Google Drive tools."""
    return [
        list_drive_items,
        search_drive_files,
        get_drive_file_metadata,
        shared_files,
        get_drive_file_content,
    ]
