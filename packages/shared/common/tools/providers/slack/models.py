"""Slack data models."""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any


class SlackMessage(BaseModel):
    ts: str
    user: Optional[str]
    text: Optional[str]
    thread_ts: Optional[str]
    reply_count: Optional[int]
    replies: Optional[List[Dict[str, Any]]]
    attachments: Optional[List[Dict[str, Any]]]
    reactions: Optional[List[Dict[str, Any]]]
    permalink: Optional[str]
    channel: Optional[str]


class SlackChannel(BaseModel):
    id: str
    name: str
    is_channel: bool
    is_group: bool
    is_im: bool
    is_mpim: bool
    is_private: bool
    is_archived: bool
    is_general: bool
    is_shared: bool
    is_org_shared: bool
    is_member: bool
    created: Optional[int]
    creator: Optional[str]
    topic: Optional[Dict[str, Any]]
    purpose: Optional[Dict[str, Any]]
    num_members: Optional[int]


class SlackUser(BaseModel):
    id: str
    name: str
    real_name: Optional[str]
    display_name: Optional[str]
    email: Optional[str]
    is_admin: Optional[bool]
    is_owner: Optional[bool]
    is_primary_owner: Optional[bool]
    is_restricted: Optional[bool]
    is_ultra_restricted: Optional[bool]
    is_bot: Optional[bool]
    is_app_user: Optional[bool]
    profile: Optional[Dict[str, Any]]
    tz: Optional[str]
    tz_label: Optional[str]
    tz_offset: Optional[int]
