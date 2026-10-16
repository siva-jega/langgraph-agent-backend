"""Google Drive data models."""

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List, Dict, Any, TypedDict


class DriveItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    mime_type: str = Field(alias="mimeType")
    created_time: Optional[str] = Field(None, alias="createdTime")
    modified_time: Optional[str] = Field(None, alias="modifiedTime")
    owners: Optional[List[Dict[str, Any]]] = None
    parents: Optional[List[str]] = None
    web_view_link: Optional[str] = Field(None, alias="webViewLink")
    icon_link: Optional[str] = Field(None, alias="iconLink")
    size: Optional[str] = None
    capabilities: Optional[Dict[str, bool]] = None
    shared: Optional[bool] = None
    description: Optional[str] = None
    starred: Optional[bool] = None
    trashed: Optional[bool] = None
    content: Optional[str] = None  # Document content from retrieval


class DriveFileContent(TypedDict, total=False):
    name: str
    mime_type: str
    size: str
    web_view_link: str
    content: str
    parsed_content: Optional[str]
    error: str


class ListItemsResult(TypedDict):
    items: List[DriveItem]
    nextPageToken: Optional[str]
    error: Optional[str]


class SearchFilesResult(TypedDict):
    items: List[DriveItem]
    nextPageToken: Optional[str]
    error: Optional[str]


class GetFileMetadataResult(TypedDict, total=False):
    error: str


class DriveAnalyticsResult(TypedDict):
    file_type_counts: Dict[str, int]
    total_size_bytes: int
    largest_files: List[Dict[str, Any]]
    error: Optional[str]


class ActivityAnalyticsResult(TypedDict):
    items: List[DriveItem]
    nextPageToken: Optional[str]
    error: Optional[str]


class SharedFilesResult(TypedDict):
    items: List[DriveItem]
    nextPageToken: Optional[str]
    error: Optional[str]


class FindDuplicatesResult(TypedDict):
    duplicates: Dict[str, List[Dict[str, Any]]]
    error: Optional[str]


class FolderTreeResult(TypedDict):
    folder_tree: Dict[str, Any]
    error: Optional[str]


class RevisionItem(TypedDict, total=False):
    id: str
    mime_type: Optional[str]
    modified_time: Optional[str]
    last_modifying_user: Optional[Dict[str, Any]]
    keep_forever: Optional[bool]
    original_filename: Optional[str]
    md5_checksum: Optional[str]
    size: Optional[str]
    export_links: Optional[Dict[str, str]]
    download_url: Optional[str]
    error: Optional[str]


class ListRevisionsResult(TypedDict):
    revisions: List[RevisionItem]
    error: Optional[str]
