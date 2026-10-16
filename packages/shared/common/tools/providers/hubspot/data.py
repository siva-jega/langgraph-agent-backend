from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "list_contacts": {
        "id": "list_contacts",
        "name": "List Contacts",
        "description": "List recent HubSpot contacts.",
    },
    "search_contacts": {
        "id": "search_contacts",
        "name": "Search Contacts",
        "description": "Search HubSpot contacts using a text query and optional filters.",
    },
    "get_contact": {
        "id": "get_contact",
        "name": "Get Contact",
        "description": "Retrieve a single HubSpot contact by contact id.",
    },
    "search_contacts_by_company": {
        "id": "search_contacts_by_company",
        "name": "Search Contacts by Company",
        "description": "Search for HubSpot contacts associated with a specified company.",
    },
    "list_companies": {
        "id": "list_companies",
        "name": "List Companies",
        "description": "List recent HubSpot companies.",
    },
    "search_companies": {
        "id": "search_companies",
        "name": "Search Companies",
        "description": "Search HubSpot companies using a text query and optional filters.",
    },
    "get_company": {
        "id": "get_company",
        "name": "Get Company",
        "description": "Retrieve a single HubSpot company by company id.",
    },
    "list_deals": {
        "id": "list_deals",
        "name": "List Deals",
        "description": "List recent HubSpot deals.",
    },
    "search_deals": {
        "id": "search_deals",
        "name": "Search Deals",
        "description": "Search HubSpot deals using a text query and optional filters.",
    },
    "get_deal": {
        "id": "get_deal",
        "name": "Get Deal",
        "description": "Retrieve a single HubSpot deal by deal id.",
    },
    "search_deals_by_company": {
        "id": "search_deals_by_company",
        "name": "Search Deals by Company",
        "description": "Search for HubSpot deals associated with a specified company.",
    },
    "list_tickets": {
        "id": "list_tickets",
        "name": "List Tickets",
        "description": "List recent HubSpot tickets.",
    },
    "search_tickets": {
        "id": "search_tickets",
        "name": "Search Tickets",
        "description": "Search HubSpot tickets using a text query and optional filters.",
    },
    "get_ticket": {
        "id": "get_ticket",
        "name": "Get Ticket",
        "description": "Retrieve a single HubSpot ticket by ticket id.",
    },
    "create_ticket": {
        "id": "create_ticket",
        "name": "Create Ticket",
        "description": "Create a HubSpot ticket using a summary, description, and user information.",
    },
    "search_tickets_by_company": {
        "id": "search_tickets_by_company",
        "name": "Search Tickets by Company",
        "description": "Search HubSpot tickets associated with a specified company.",
    },
    "get_analytics_summary": {
        "id": "get_analytics_summary",
        "name": "Get Analytics Summary",
        "description": "Get an analytics summary including key counts and recent activity snapshots.",
    },
}

__all__ = ["TOOLS_DATA"]

