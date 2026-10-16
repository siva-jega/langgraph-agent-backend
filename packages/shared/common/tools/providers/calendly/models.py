"""Pydantic models for Calendly provider."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CalendlyUser(BaseModel):
    uri: str
    name: str
    slug: Optional[str] = None
    email: Optional[str] = None
    scheduling_url: Optional[str] = None
    timezone: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    current_organization: Optional[str] = None


class CalendlyInvitee(BaseModel):
    uri: str
    name: Optional[str] = None
    email: Optional[str] = None
    status: Optional[str] = None
    timezone: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    cancel_url: Optional[str] = None
    reschedule_url: Optional[str] = None


class CalendlyEvent(BaseModel):
    uri: str
    name: Optional[str] = None
    status: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    event_type: Optional[str] = None
    location: Optional[Dict[str, Any]] = None
    invitees_counter: Optional[Dict[str, int]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    event_memberships: Optional[List[Dict[str, Any]]] = None
    event_guests: Optional[List[Dict[str, Any]]] = None
    calendar_event: Optional[Dict[str, Any]] = None
    cancellation: Optional[Dict[str, Any]] = None
    meeting_notes_plain: Optional[str] = None
    meeting_notes_html: Optional[str] = None


class CalendlyEventType(BaseModel):
    uri: str
    name: Optional[str] = None
    active: Optional[bool] = None
    slug: Optional[str] = None
    scheduling_url: Optional[str] = None
    duration: Optional[int] = None
    kind: Optional[str] = None
    type: Optional[str] = None
    color: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    description_plain: Optional[str] = None


class CalendlySchedulingLink(BaseModel):
    booking_url: str
    owner: Optional[str] = None
    owner_type: Optional[str] = None


class CalendlyNoShow(BaseModel):
    uri: str
    invitee: Optional[str] = None
    created_at: Optional[str] = None


class CalendlyMeetingRecap(BaseModel):
    uri: Optional[str] = None
    event: Optional[str] = None
    summary: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


class CalendlyMeetingTranscript(BaseModel):
    uri: Optional[str] = None
    event: Optional[str] = None
    transcript_text: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


class CalendlyResult(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
