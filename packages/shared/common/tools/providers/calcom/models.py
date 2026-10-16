"""Pydantic models for Cal.com provider (optional, for structured tool results)."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class CalComEventType(BaseModel):
    id: int
    slug: Optional[str] = None
    title: Optional[str] = None
    length: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class CalComBooking(BaseModel):
    id: Optional[int] = None
    uid: Optional[str] = None
    title: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
