"""Utility functions for HubSpot integration."""

import asyncio
import httpx
from typing import Optional, Dict, Any
from common.integration import get_token
from common.modules.token import TokenResponse
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from .models import HubspotContact, HubspotCompany, HubspotDeal, HubspotTicket
logger = logging.getLogger(__name__)


def get_connector_id(configurable: dict) -> str | None:
    """Extract the HubSpot connector ID from agent configurable apps."""
    for app in (configurable.get("apps") or []):
        try:
            slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
        except Exception:
            slug = ""
        if "hubspot" in slug:
            return app.get("connectorId") or app.get("id")
    return None


async def get_hubspot_credentials(user_id: str, connectorId: str | None = None) -> Optional[TokenResponse]:
    """Get HubSpot credentials from token service."""
    try:
        user_data = await get_token().get_user_data(
            user_id=user_id, connector_name="hubspot", connectorId=connectorId, needs_refresh=True
        )
        return user_data
    except Exception as e:
        logger.error(f"Error getting HubSpot credentials: {str(e)}")
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
)
async def make_hubspot_request(
    access_token: str, method: str, url: str, **kwargs
) -> Any:
    """Make a HubSpot API request with retry logic.

    Args:
        access_token: HubSpot access token
        method: HTTP method (GET, POST, etc.)
        url: API endpoint URL
        **kwargs: Additional parameters for the request

    Returns:
        JSON response data
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json()




# Helper functions for parsing
def parse_contact(contact: Dict[str, Any]) -> HubspotContact:
    """Parse a contact dict into HubspotContact model."""
    props = contact.get("properties", {})
    return HubspotContact(
        id=contact.get("id", ""),
        email=props.get("email", ""),
        firstname=props.get("firstname", ""),
        lastname=props.get("lastname"),
        company=props.get("company"),
        phone=props.get("phone"),
        createdate=props.get("createdate", contact.get("createdAt")),
        lastmodifieddate=props.get("lastmodifieddate", contact.get("updatedAt")),
        properties=props,
        archived=contact.get("archived"),
        createdAt=contact.get("createdAt"),
        updatedAt=contact.get("updatedAt"),
    )


def parse_company(company: Dict[str, Any]) -> HubspotCompany:
    """Parse a company dict into HubspotCompany model."""
    props = company.get("properties", {})
    return HubspotCompany(
        id=company.get("id", ""),
        name=props.get("name", ""),
        domain=props.get("domain"),
        city=props.get("city"),
        state=props.get("state"),
        country=props.get("country"),
        industry=props.get("industry"),
        phone=props.get("phone"),
        createdate=props.get("createdate", company.get("createdAt")),
        lastmodifieddate=props.get("lastmodifieddate", company.get("updatedAt")),
        properties=props,
        archived=company.get("archived"),
        createdAt=company.get("createdAt"),
        updatedAt=company.get("updatedAt"),
    )


def parse_deal(deal: Dict[str, Any]) -> HubspotDeal:
    """Parse a deal dict into HubspotDeal model."""
    props = deal.get("properties", {})
    return HubspotDeal(
        id=deal.get("id", ""),
        dealname=props.get("dealname", ""),
        amount=props.get("amount"),
        dealstage=props.get("dealstage"),
        pipeline=props.get("pipeline"),
        closedate=props.get("closedate"),
        createdate=props.get("createdate", deal.get("createdAt")),
        lastmodifieddate=props.get("lastmodifieddate", deal.get("updatedAt")),
        properties=props,
        archived=deal.get("archived"),
        createdAt=deal.get("createdAt"),
        updatedAt=deal.get("updatedAt"),
    )


def parse_ticket(ticket: Dict[str, Any]) -> HubspotTicket:
    """Parse a ticket dict into HubspotTicket model."""
    props = ticket.get("properties", {})
    return HubspotTicket(
        id=ticket.get("id", ""),
        subject=props.get("subject", ""),
        content=props.get("content"),
        hs_ticket_priority=props.get("hs_ticket_priority"),
        hs_ticket_category=props.get("hs_ticket_category"),
        hs_ticket_pipeline=props.get("hs_ticket_pipeline"),
        hs_ticket_stage=props.get("hs_ticket_stage"),
        createdate=props.get("createdate", ticket.get("createdAt")),
        lastmodifieddate=props.get("lastmodifieddate", ticket.get("updatedAt")),
        properties=props,
        archived=ticket.get("archived"),
        createdAt=ticket.get("createdAt"),
        updatedAt=ticket.get("updatedAt"),
    )
