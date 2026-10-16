from typing import Optional
from pydantic import BaseModel


class CreateTicketResult(BaseModel):
    success: bool
    message: str
    ticket_id: Optional[str] = None

