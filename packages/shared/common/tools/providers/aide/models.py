from typing import Optional
from pydantic import BaseModel


class CreateCustomerResult(BaseModel):
    success: bool
    message: str
    customer_id: Optional[str] = None


class CreateTicketResult(BaseModel):
    success: bool
    message: str
    ticket_id: Optional[str] = None
