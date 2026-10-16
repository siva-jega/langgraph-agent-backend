"""Notion data models."""

from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class NotionPage(BaseModel):
    id: str
    title: str
    created_time: str
    last_edited_time: str
    url: str
    properties: Dict[str, Any]
    is_archived: bool


class NotionBlock(BaseModel):
    id: str
    type: str
    created_time: str
    last_edited_time: str
    has_children: bool
    archived: bool
    parent: Dict[str, Any]


class NotionDatabase(BaseModel):
    id: str
    title: str
    description: str
    properties: Dict[str, Any]
    created_time: str
    last_edited_time: str
    url: str
    is_inline: bool
    archived: bool


class NotionUser(BaseModel):
    id: str
    name: str
    avatar_url: Optional[str]
    type: str
    person: Optional[Dict[str, Any]]


class NotionComment(BaseModel):
    id: str
    parent: Dict[str, Any]
    discussion_id: str
    created_time: str
    last_edited_time: str
    created_by: Dict[str, Any]
    rich_text: List[Dict[str, Any]]
