"""Gmail data models."""

from pydantic import BaseModel
from typing import Dict, Any, Optional, List


class GmailMessage(BaseModel):
    id: str
    thread_id: str
    subject: str
    sender: str
    recipients: List[str]
    body: str
    date: str
    labels: List[str]
    attachments: Optional[List[Dict[str, Any]]] = None


class GmailThread(BaseModel):
    id: str
    snippet: str
    history_id: str
    messages: List[GmailMessage]
    subject: str
    participants: List[str]
    message_count: int
    last_message_date: str
    labels: List[str]
    has_attachments: bool
