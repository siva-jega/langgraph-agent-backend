from pydantic import BaseModel
from typing import Dict, Any, Optional

class HubspotContact(BaseModel):
    id: str
    email: str
    firstname: str
    lastname: Optional[str]
    company: Optional[str]
    phone: Optional[str]
    createdate: Optional[str]
    lastmodifieddate: Optional[str]
    properties: Dict[str, Any]
    archived: Optional[bool] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class HubspotCompany(BaseModel):
    id: str
    name: str
    domain: Optional[str]
    city: Optional[str]
    state: Optional[str]
    country: Optional[str]
    industry: Optional[str]
    phone: Optional[str]
    createdate: Optional[str]
    lastmodifieddate: Optional[str]
    properties: Dict[str, Any]
    archived: Optional[bool] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class HubspotDeal(BaseModel):
    id: str
    dealname: str
    amount: Optional[str]
    dealstage: Optional[str]
    pipeline: Optional[str]
    closedate: Optional[str]
    createdate: Optional[str]
    lastmodifieddate: Optional[str]
    properties: Dict[str, Any]
    archived: Optional[bool] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class HubspotTicket(BaseModel):
    id: str
    subject: str
    content: Optional[str]
    hs_ticket_priority: Optional[str]
    hs_ticket_category: Optional[str]
    hs_ticket_pipeline: Optional[str]
    hs_ticket_stage: Optional[str]
    createdate: Optional[str]
    lastmodifieddate: Optional[str]
    properties: Dict[str, Any]
    archived: Optional[bool] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class HubspotAnalytics(BaseModel):
    total_contacts: int
    total_companies: int
    total_deals: int
    total_tickets: int
    recent_contacts: int
    recent_companies: int
    recent_deals: int
    recent_tickets: int
    deal_pipeline_summary: Dict[str, int]
    ticket_priority_summary: Dict[str, int]
