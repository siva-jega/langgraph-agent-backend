"""Outlook data models."""

from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class OutlookMessage(BaseModel):
    id: str
    subject: str
    sender: str
    recipients: List[str]
    body: str
    date: str
    is_read: bool
    importance: str
    attachments: Optional[List[Dict[str, Any]]] = None
