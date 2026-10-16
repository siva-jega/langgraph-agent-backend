"""Google Calendar data models."""

from pydantic import BaseModel
from typing import Optional, List


class CalendarEvent(BaseModel):
    id: str
    summary: str
    organizer_email: str
    start_time: str
    end_time: str
    location: str
    description: str
    attendees: List[str]
    html_link: str
    meet_link: Optional[str] = None
