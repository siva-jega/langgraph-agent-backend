"""HubSpot tools for managing contacts, companies, deals, and tickets."""

from typing import List, Optional, Dict, Any
from langchain_core.tools import tool, Tool
from langchain_core.runnables import RunnableConfig
import logging
from .models import (
    HubspotContact,
    HubspotCompany,
    HubspotDeal,
    HubspotTicket,
    HubspotAnalytics,
)
from .utils import (
    get_connector_id,
    get_hubspot_credentials,
    make_hubspot_request,
    parse_contact,
    parse_company,
    parse_deal,
    parse_ticket,
)
from ..aide.utils import create_widget_ticket
from ..zendesk.models import CreateTicketResult
from datetime import datetime, timedelta
import asyncio
from common.tools.utils.writer import emit_update

logger = logging.getLogger(__name__)


# Contact tools
@tool(response_format="content_and_artifact")
async def list_contacts(
    max_results: int = 50,
    sort: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotContact]:
    """
    List recent Hubspot contacts.

    Args:
        max_results: Maximum number of contacts to return.
        sort: Sort order for the contacts.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot contacts.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")
        emit_update(
            {
                "tool": "Hubspot",
                "update": f"Listing contacts with max_results: {max_results} and sort: {sort}",
            },
            "hubspot",
        )
        url = f"https://api.hubapi.com/crm/v3/objects/contacts?limit={max_results}"
        if sort:
            url += f"&sort={sort}"

        response = await make_hubspot_request(credentials.token, "GET", url)
        contacts = response.get("results", [])
        emit_update(
            {"tool": "HubSpot", "update": f"Found {len(contacts)} contacts"},
            "hubspot",
        )
        parsed_contacts = [parse_contact(contact) for contact in contacts]

        return parsed_contacts, {
            "name": "Hubspot",
            "tool": "List Contacts",
            "data": [
                {
                    "id": c.id,
                    "email": c.email,
                    "name": f"{c.firstname} {c.lastname}",
                    "company": c.company,
                }
                for c in parsed_contacts
            ],
        }
    except Exception as e:
        logger.error(f"Error listing contacts: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def search_contacts(
    query: Optional[str] = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    filter_groups: Optional[List[List[Dict[str, Any]]]] = None,
    properties: Optional[List[str]] = None,
    sorts: Optional[List[str]] = None,
    limit: int = 50,
    after: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotContact]:
    """
    Search Hubspot contacts using text query, filters, and filter groups.

    Args:
        query: Text search query to search across contact properties.
        filters: List of filters (propertyName, operator, value/values/highValue).
        filter_groups: List of filter groups (each group is a list of filters).
        properties: List of properties to return for each contact.
        sorts: List of sort strings (e.g., ["createdate"]).
        limit: Maximum number of contacts to return.
        after: Pagination cursor.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot contacts.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        emit_update(
            {"tool": "HubSpot", "update": f"Searching contacts with query: {query}"},
            "hubspot",
        )

        url = "https://api.hubapi.com/crm/v3/objects/contacts/search"
        data = {"limit": min(limit, 100)}

        if query:
            data["query"] = query

        if filter_groups:
            valid_filter_groups = []
            for group in filter_groups:
                if isinstance(group, list) and len(group) > 0:
                    valid_filters = []
                    for f in group:
                        if (
                            isinstance(f, dict)
                            and "propertyName" in f
                            and "operator" in f
                            and ("value" in f or "values" in f or "highValue" in f)
                        ):
                            valid_filters.append(f)
                    if valid_filters:
                        valid_filter_groups.append({"filters": valid_filters})
            if valid_filter_groups:
                data["filterGroups"] = valid_filter_groups
        elif filters:
            valid_filters = []
            for f in filters:
                if (
                    isinstance(f, dict)
                    and "propertyName" in f
                    and "operator" in f
                    and ("value" in f or "values" in f or "highValue" in f)
                ):
                    valid_filters.append(f)
            if valid_filters:
                data["filterGroups"] = [{"filters": valid_filters}]

        if properties:
            data["properties"] = properties
        else:
            data["properties"] = [
                "firstname",
                "lastname",
                "email",
                "company",
                "phone",
                "createdate",
                "lastmodifieddate",
            ]

        if sorts:
            valid_sorts = [s for s in sorts if isinstance(s, str)]
            if valid_sorts:
                data["sorts"] = valid_sorts

        if after:
            data["after"] = after

        response = await make_hubspot_request(credentials.token, "POST", url, json=data)
        contacts = response.get("results", [])
        parsed_contacts = [parse_contact(contact) for contact in contacts]
        emit_update(
            {"tool": "HubSpot", "update": f"Found {len(parsed_contacts)} contacts"},
            "hubspot",
        )
        return parsed_contacts, {
            "name": "HubSpot",
            "tool": "Search Contacts",
            "data": [
                {
                    "id": c.id,
                    "email": c.email,
                    "name": f"{c.firstname} {c.lastname}",
                    "company": c.company,
                }
                for c in parsed_contacts
            ],
        }
    except Exception as e:
        logger.error(f"Error searching contacts: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def get_contact(
    contact_id: str,
    config: RunnableConfig = None,
) -> Optional[HubspotContact]:
    """
    Get a single Hubspot contact by ID.

    Args:
        contact_id: ID of the contact to get.
        config: Configuration containing user_id.

    Returns:
        Hubspot contact.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        emit_update(
            {"tool": "HubSpot", "update": f"Getting contact with id: {contact_id}"},
            "hubspot",
        )
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        url = f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}"
        response = await make_hubspot_request(credentials.token, "GET", url)
        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Found contact: {response.get('properties', {}).get('email', '')}",
            },
            "hubspot",
        )
        parsed_contact = parse_contact(response)
        emit_update(
            {"tool": "HubSpot", "update": f"Found contact: {parsed_contact.email}"},
            "hubspot",
        )
        return parsed_contact, {
            "name": "Hubspot",
            "tool": "Get Contact",
            "data": parsed_contact,
        }
    except Exception as e:
        logger.error(f"Error getting contact {contact_id}: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def search_contacts_by_company(
    company_name: str,
    limit: int = 50,
    after: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotContact]:
    """
    Search Hubspot contacts associated with a specific company by name.

    Args:
        company_name: Name of the company to find contacts for.
        limit: Maximum number of contacts to return.
        after: Pagination cursor.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot contacts associated with the company.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Searching company with name: {company_name}",
            },
            "hubspot",
        )
        # First, search for the company by name
        company_search_data = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "name",
                            "operator": "CONTAINS_TOKEN",
                            "value": company_name,
                        }
                    ]
                }
            ],
            "properties": ["name"],
            "limit": 10,
        }

        company_response = await make_hubspot_request(
            credentials.token,
            "POST",
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            json=company_search_data,
        )
        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Found {len(company_response.get('results', []))} companies",
            },
            "hubspot",
        )
        companies = company_response.get("results", [])
        if not companies:
            return [], {
                "name": "Hubspot",
                "tool": "Search Contacts by Company",
                "data": [],
            }

        company_id = companies[0].get("id")

        # Try to get contacts via associations
        try:
            associations_url = f"https://api.hubapi.com/crm/v4/objects/companies/{company_id}/associations/contacts"
            associations_response = await make_hubspot_request(
                credentials.token, "GET", associations_url
            )

            associated_contacts = associations_response.get("results", [])
            if not associated_contacts:
                return []

            contact_ids = [
                contact.get("toObjectId") or contact.get("id")
                for contact in associated_contacts
            ]

            # Get full contact details
            contacts = []
            for contact_id in contact_ids[:limit]:
                try:
                    contact_url = (
                        f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}"
                    )
                    contact_response = await make_hubspot_request(
                        credentials.token, "GET", contact_url
                    )
                    contacts.append(parse_contact(contact_response))
                except Exception:
                    continue

            return contacts

        except Exception:
            # Fallback to property search
            contact_search_data = {
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "company",
                                "operator": "CONTAINS_TOKEN",
                                "value": company_name,
                            }
                        ]
                    }
                ],
                "properties": [
                    "firstname",
                    "lastname",
                    "email",
                    "company",
                    "phone",
                    "createdate",
                    "lastmodifieddate",
                ],
                "limit": limit,
            }

            if after:
                contact_search_data["after"] = after

            contact_response = await make_hubspot_request(
                credentials.token,
                "POST",
                "https://api.hubapi.com/crm/v3/objects/contacts/search",
                json=contact_search_data,
            )

            contacts = contact_response.get("results", [])
            parsed_contacts = [parse_contact(contact) for contact in contacts]
            emit_update(
                {"tool": "HubSpot", "update": f"Found {len(parsed_contacts)} contacts"},
                "hubspot",
            )
            return parsed_contacts, {
                "name": "HubSpot",
                "tool": "Search Contacts by Company",
                "data": parsed_contacts,
            }

    except Exception as e:
        logger.error(f"Error searching contacts by company: {str(e)}")
        raise


# Company tools
@tool(response_format="content_and_artifact")
async def list_companies(
    max_results: int = 50,
    sort: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotCompany]:
    """
    List recent Hubspot companies.

    Args:
        max_results: Maximum number of companies to return.
        sort: Sort order for the companies.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot companies.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        url = f"https://api.hubapi.com/crm/v3/objects/companies?limit={max_results}"
        if sort:
            url += f"&sort={sort}"

        response = await make_hubspot_request(credentials.token, "GET", url)
        companies = response.get("results", [])
        parsed_companies = [parse_company(company) for company in companies]
        return parsed_companies, {
            "name": "HubSpot",
            "tool": "List Companies",
            "data": parsed_companies,
        }
    except Exception as e:
        logger.error(f"Error listing companies: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def search_companies(
    query: Optional[str] = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    filter_groups: Optional[List[List[Dict[str, Any]]]] = None,
    properties: Optional[List[str]] = None,
    sorts: Optional[List[str]] = None,
    limit: int = 50,
    after: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotCompany]:
    """
    Search Hubspot companies using text query, filters, and filter groups.

    Args:
        query: Text search query to search across company properties.
        filters: List of filters (propertyName, operator, value/values/highValue).
        filter_groups: List of filter groups (each group is a list of filters).
        properties: List of properties to return for each company.
        sorts: List of sort strings (e.g., ["createdate"]).
        limit: Maximum number of companies to return.
        after: Pagination cursor.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot companies.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        emit_update(
            {"tool": "HubSpot", "update": f"Searching companies with query: {query}"},
            "hubspot",
        )

        url = "https://api.hubapi.com/crm/v3/objects/companies/search"
        data = {"limit": min(limit, 100)}

        if query:
            data["query"] = query

        if filter_groups:
            valid_filter_groups = []
            for group in filter_groups:
                if isinstance(group, list) and len(group) > 0:
                    valid_filters = []
                    for f in group:
                        if (
                            isinstance(f, dict)
                            and "propertyName" in f
                            and "operator" in f
                            and ("value" in f or "values" in f or "highValue" in f)
                        ):
                            valid_filters.append(f)
                    if valid_filters:
                        valid_filter_groups.append({"filters": valid_filters})
            if valid_filter_groups:
                data["filterGroups"] = valid_filter_groups
        elif filters:
            valid_filters = []
            for f in filters:
                if (
                    isinstance(f, dict)
                    and "propertyName" in f
                    and "operator" in f
                    and ("value" in f or "values" in f or "highValue" in f)
                ):
                    valid_filters.append(f)
            if valid_filters:
                data["filterGroups"] = [{"filters": valid_filters}]

        if properties:
            data["properties"] = properties
        else:
            data["properties"] = [
                "name",
                "domain",
                "city",
                "state",
                "country",
                "industry",
                "phone",
                "createdate",
                "lastmodifieddate",
            ]

        if sorts:
            valid_sorts = [s for s in sorts if isinstance(s, str)]
            if valid_sorts:
                data["sorts"] = valid_sorts

        if after:
            data["after"] = after

        response = await make_hubspot_request(credentials.token, "POST", url, json=data)
        companies = response.get("results", [])
        parsed_companies = [parse_company(company) for company in companies]
        emit_update(
            {"tool": "HubSpot", "update": f"Found {len(parsed_companies)} companies"},
            "hubspot",
        )
        return parsed_companies, {
            "name": "HubSpot",
            "tool": "Search Companies",
            "data": parsed_companies,
        }
    except Exception as e:
        logger.error(f"Error searching companies: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def get_company(
    company_id: str,
    config: RunnableConfig = None,
) -> Optional[HubspotCompany]:
    """
    Get a single Hubspot company by ID.

    Args:
        company_id: ID of the company to get.
        config: Configuration containing user_id.

    Returns:
        Hubspot company.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")
        emit_update(
            {"tool": "HubSpot", "update": f"Getting company with id: {company_id}"},
            "hubspot",
        )

        url = f"https://api.hubapi.com/crm/v3/objects/companies/{company_id}"
        response = await make_hubspot_request(credentials.token, "GET", url)

        parsed_company = parse_company(response)
        emit_update(
            {"tool": "HubSpot", "update": f"Found company: {parsed_company.name}"},
            "hubspot",
        )
        return parsed_company, {
            "name": "HubSpot",
            "tool": "Get Company",
            "data": parsed_company,
        }
    except Exception as e:
        logger.error(f"Error getting company {company_id}: {str(e)}")
        raise


# Deal tools
@tool(response_format="content_and_artifact")
async def list_deals(
    max_results: int = 50,
    sort: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotDeal]:
    """
    List recent Hubspot deals.

    Args:
        max_results: Maximum number of deals to return.
        sort: Sort order for the deals.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot deals.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Listing deals with max_results: {max_results} and sort: {sort}",
            },
            "hubspot",
        )
        url = f"https://api.hubapi.com/crm/v3/objects/deals?limit={max_results}"
        if sort:
            url += f"&sort={sort}"

        response = await make_hubspot_request(credentials.token, "GET", url)
        deals = response.get("results", [])
        parsed_deals = [parse_deal(deal) for deal in deals]
        emit_update(
            {"tool": "HubSpot", "update": f"Found {len(parsed_deals)} deals"},
            "hubspot",
        )
        return parsed_deals, {
            "name": "HubSpot",
            "tool": "List Deals",
            "data": parsed_deals,
        }
    except Exception as e:
        logger.error(f"Error listing deals: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def search_deals(
    query: Optional[str] = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    filter_groups: Optional[List[List[Dict[str, Any]]]] = None,
    properties: Optional[List[str]] = None,
    sorts: Optional[List[str]] = None,
    limit: int = 50,
    after: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotDeal]:
    """
    Search Hubspot deals using text query, filters, and filter groups.

    Args:
        query: Text search query to search across deal properties.
        filters: List of filters (propertyName, operator, value/values/highValue).
        filter_groups: List of filter groups (each group is a list of filters).
        properties: List of properties to return for each deal.
        sorts: List of sort strings (e.g., ["createdate"]).
        limit: Maximum number of deals to return.
        after: Pagination cursor.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot deals.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        emit_update(
            {"tool": "HubSpot", "update": f"Searching deals with query: {query}"},
            "hubspot",
        )

        url = "https://api.hubapi.com/crm/v3/objects/deals/search"
        data = {"limit": min(limit, 100)}

        if query:
            data["query"] = query

        if filter_groups:
            valid_filter_groups = []
            for group in filter_groups:
                if isinstance(group, list) and len(group) > 0:
                    valid_filters = []
                    for f in group:
                        if (
                            isinstance(f, dict)
                            and "propertyName" in f
                            and "operator" in f
                            and ("value" in f or "values" in f or "highValue" in f)
                        ):
                            valid_filters.append(f)
                    if valid_filters:
                        valid_filter_groups.append({"filters": valid_filters})
            if valid_filter_groups:
                data["filterGroups"] = valid_filter_groups
        elif filters:
            valid_filters = []
            for f in filters:
                if (
                    isinstance(f, dict)
                    and "propertyName" in f
                    and "operator" in f
                    and ("value" in f or "values" in f or "highValue" in f)
                ):
                    valid_filters.append(f)
            if valid_filters:
                data["filterGroups"] = [{"filters": valid_filters}]

        if properties:
            data["properties"] = properties
        else:
            data["properties"] = [
                "dealname",
                "amount",
                "dealstage",
                "pipeline",
                "closedate",
                "createdate",
                "lastmodifieddate",
            ]

        if sorts:
            valid_sorts = [s for s in sorts if isinstance(s, str)]
            if valid_sorts:
                data["sorts"] = valid_sorts

        if after:
            data["after"] = after

        response = await make_hubspot_request(credentials.token, "POST", url, json=data)
        deals = response.get("results", [])
        parsed_deals = [parse_deal(deal) for deal in deals]
        emit_update(
            {"tool": "HubSpot", "update": f"Found {len(parsed_deals)} deals"},
            "hubspot",
        )
        return parsed_deals, {
            "name": "HubSpot",
            "tool": "Search Deals",
            "data": parsed_deals,
        }
    except Exception as e:
        logger.error(f"Error searching deals: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def get_deal(
    deal_id: str,
    config: RunnableConfig = None,
) -> Optional[HubspotDeal]:
    """
    Get a single Hubspot deal by ID.

    Args:
        deal_id: ID of the deal to get.
        config: Configuration containing user_id.

    Returns:
        Hubspot deal.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        emit_update(
            {"tool": "HubSpot", "update": f"Getting deal with id: {deal_id}"},
            "hubspot",
        )
        url = f"https://api.hubapi.com/crm/v3/objects/deals/{deal_id}"
        response = await make_hubspot_request(credentials.token, "GET", url)
        parsed_deal = parse_deal(response)
        emit_update(
            {"tool": "HubSpot", "update": f"Found deal: {parsed_deal.dealname}"},
            "hubspot",
        )
        return parsed_deal, {
            "name": "HubSpot",
            "tool": "Get Deal",
            "data": parsed_deal,
        }
    except Exception as e:
        logger.error(f"Error getting deal {deal_id}: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def search_deals_by_company(
    company_name: str,
    limit: int = 50,
    after: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotDeal]:
    """
    Search Hubspot deals associated with a specific company by name.

    Args:
        company_name: Name of the company to find deals for.
        limit: Maximum number of deals to return.
        after: Pagination cursor.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot deals associated with the company.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        # First, search for the company
        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Searching deals by company with name: {company_name} and limit: {limit} and after: {after}",
            },
            "hubspot",
        )
        company_search_data = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "name",
                            "operator": "CONTAINS_TOKEN",
                            "value": company_name,
                        }
                    ]
                }
            ],
            "properties": ["name"],
            "limit": 10,
        }

        company_response = await make_hubspot_request(
            credentials.token,
            "POST",
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            json=company_search_data,
        )
        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Found {len(company_response.get('results', []))} companies",
            },
            "hubspot",
        )
        companies = company_response.get("results", [])
        if not companies:
            return [], {
                "name": "HubSpot",
                "tool": "Search Deals by Company",
                "data": [],
            }

        company_id = companies[0].get("id")

        # Try associations
        try:
            associations_url = f"https://api.hubapi.com/crm/v4/objects/companies/{company_id}/associations/deals"
            associations_response = await make_hubspot_request(
                credentials.token, "GET", associations_url
            )

            associated_deals = associations_response.get("results", [])
            if not associated_deals:
                return []

            deal_ids = [
                deal.get("toObjectId") or deal.get("id") for deal in associated_deals
            ]

            deals = []
            for deal_id in deal_ids[:limit]:
                try:
                    deal_url = f"https://api.hubapi.com/crm/v3/objects/deals/{deal_id}"
                    deal_response = await make_hubspot_request(
                        credentials.token, "GET", deal_url
                    )
                    deals.append(parse_deal(deal_response))
                except Exception:
                    continue

            parsed_deals = [parse_deal(deal) for deal in deals]
            return parsed_deals, {
                "name": "HubSpot",
                "tool": "Search Deals by Company",
                "data": parsed_deals,
            }

        except Exception:
            # Fallback to text search
            deal_search_data = {
                "query": company_name,
                "properties": [
                    "dealname",
                    "amount",
                    "dealstage",
                    "pipeline",
                    "closedate",
                    "createdate",
                    "lastmodifieddate",
                ],
                "limit": limit,
            }

            if after:
                deal_search_data["after"] = after

            deal_response = await make_hubspot_request(
                credentials.token,
                "POST",
                "https://api.hubapi.com/crm/v3/objects/deals/search",
                json=deal_search_data,
            )

            deals = deal_response.get("results", [])
            parsed_deals = [parse_deal(deal) for deal in deals]
            return parsed_deals, {
                "name": "HubSpot",
                "tool": "Search Deals by Company",
                "data": parsed_deals,
            }

    except Exception as e:
        logger.error(f"Error searching deals by company: {str(e)}")
        raise


# Ticket tools
@tool(response_format="content_and_artifact")
async def list_tickets(
    max_results: int = 50,
    sort: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotTicket]:
    """
    List recent Hubspot tickets.

    Args:
        max_results: Maximum number of tickets to return.
        sort: Sort order for the tickets.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot tickets.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Listing tickets with max_results: {max_results} and sort: {sort}",
            },
            "hubspot",
        )

        url = f"https://api.hubapi.com/crm/v3/objects/tickets?limit={max_results}"
        if sort:
            url += f"&sort={sort}"

        response = await make_hubspot_request(credentials.token, "GET", url)
        tickets = response.get("results", [])
        parsed_tickets = [parse_ticket(ticket) for ticket in tickets]
        emit_update(
            {"tool": "HubSpot", "update": f"Found {len(parsed_tickets)} tickets"},
            "hubspot",
        )
        return parsed_tickets, {
            "name": "HubSpot",
            "tool": "List Tickets",
            "data": parsed_tickets,
        }
    except Exception as e:
        logger.error(f"Error listing tickets: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def search_tickets(
    query: Optional[str] = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    filter_groups: Optional[List[List[Dict[str, Any]]]] = None,
    properties: Optional[List[str]] = None,
    sorts: Optional[List[str]] = None,
    limit: int = 50,
    after: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotTicket]:
    """
    Search Hubspot tickets using text query, filters, and filter groups.

    Args:
        query: Text search query to search across ticket properties.
        filters: List of filters (propertyName, operator, value/values/highValue).
        filter_groups: List of filter groups (each group is a list of filters).
        properties: List of properties to return for each ticket.
        sorts: List of sort strings (e.g., ["createdate"]).
        limit: Maximum number of tickets to return.
        after: Pagination cursor.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot tickets.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        emit_update(
            {"tool": "HubSpot", "update": f"Searching tickets with query: {query}"},
            "hubspot",
        )

        url = "https://api.hubapi.com/crm/v3/objects/tickets/search"
        data = {"limit": min(limit, 100)}

        if query:
            data["query"] = query

        if filter_groups:
            valid_filter_groups = []
            for group in filter_groups:
                if isinstance(group, list) and len(group) > 0:
                    valid_filters = []
                    for f in group:
                        if (
                            isinstance(f, dict)
                            and "propertyName" in f
                            and "operator" in f
                            and ("value" in f or "values" in f or "highValue" in f)
                        ):
                            valid_filters.append(f)
                    if valid_filters:
                        valid_filter_groups.append({"filters": valid_filters})
            if valid_filter_groups:
                data["filterGroups"] = valid_filter_groups
        elif filters:
            valid_filters = []
            for f in filters:
                if (
                    isinstance(f, dict)
                    and "propertyName" in f
                    and "operator" in f
                    and ("value" in f or "values" in f or "highValue" in f)
                ):
                    valid_filters.append(f)
            if valid_filters:
                data["filterGroups"] = [{"filters": valid_filters}]

        if properties:
            data["properties"] = properties
        else:
            data["properties"] = [
                "subject",
                "content",
                "hs_ticket_priority",
                "hs_ticket_category",
                "hs_ticket_pipeline",
                "hs_ticket_stage",
                "createdate",
                "lastmodifieddate",
            ]

        if sorts:
            valid_sorts = [s for s in sorts if isinstance(s, str)]
            if valid_sorts:
                data["sorts"] = valid_sorts

        if after:
            data["after"] = after

        response = await make_hubspot_request(credentials.token, "POST", url, json=data)
        tickets = response.get("results", [])
        parsed_tickets = [parse_ticket(ticket) for ticket in tickets]
        emit_update(
            {"tool": "HubSpot", "update": f"Found {len(parsed_tickets)} tickets"},
            "hubspot",
        )
        return parsed_tickets, {
            "name": "HubSpot",
            "tool": "Search Tickets",
            "data": parsed_tickets,
        }
    except Exception as e:
        logger.error(f"Error searching tickets: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def get_ticket(
    ticket_id: str,
    config: RunnableConfig = None,
) -> Optional[HubspotTicket]:
    """
    Get a single Hubspot ticket by ID.

    Args:
        ticket_id: ID of the ticket to get.
        config: Configuration containing user_id.

    Returns:
        Hubspot ticket.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        emit_update(
            {"tool": "HubSpot", "update": f"Getting ticket with id: {ticket_id}"},
            "hubspot",
        )
        url = f"https://api.hubapi.com/crm/v3/objects/tickets/{ticket_id}"
        response = await make_hubspot_request(credentials.token, "GET", url)
        parsed_ticket = parse_ticket(response)
        emit_update(
            {"tool": "HubSpot", "update": f"Found ticket: {parsed_ticket.subject}"},
            "hubspot",
        )
        return parsed_ticket, {
            "name": "HubSpot",
            "tool": "Get Ticket",
            "data": parsed_ticket,
        }
    except Exception as e:
        logger.error(f"Error getting ticket {ticket_id}: {str(e)}")
        raise


@tool(response_format="content_and_artifact")
async def create_ticket(
    config: RunnableConfig,
    user_email: str,
    user_name: str,
    summary: str,
    description: str,
) -> CreateTicketResult:
    """Create a ticket in HubSpot by providing a ticket summary, description, and user information.

    Args:
        user_email: The email of the user requesting the ticket
        user_name: The name of the user requesting the ticket
        summary: The subject/summary of the ticket
        description: The detailed description of the ticket

    Returns:
        A CreateTicketResult object containing the success status, message, and ticket ID.
    """
    try:
        # Get credentials from token service
        configurable = config.get("configurable", {})
        user_id = configurable.get("user_id")
        if not user_id:
            return CreateTicketResult(
                success=False,
                message="No user_id provided in configuration",
            )
        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            return CreateTicketResult(
                success=False,
                message="Failed to get HubSpot credentials",
            )

        # Create ticket data
        ticket_data = {
            "properties": {
                "subject": summary,
                "content": description,
            }
        }

        # Get widget_id and chat_id for Aide API
        widget_id = configurable.get("agent_id")
        chat_id = configurable.get("thread_id")

        # Prepare Aide API call if widget_id and chat_id are available
        aide_task = None
        if widget_id and chat_id:
            aide_comment = {"body": description} if description else None
            aide_task = create_widget_ticket(
                widget_id=widget_id,
                chat_id=chat_id,
                subject=summary,
                requester_name=user_name,
                requester_email=user_email,
                platform="hubspot",
                comment=aide_comment,
                metadata=None,
            )

        # Make both requests in parallel
        url = "https://api.hubapi.com/crm/v3/objects/tickets"
        if aide_task:
            ticket_info, aide_result = await asyncio.gather(
                make_hubspot_request(
                    access_token=credentials.token,
                    method="POST",
                    url=url,
                    json=ticket_data,
                ),
                aide_task,
                return_exceptions=True,
            )
            # Log warning if Aide API call failed
            if isinstance(aide_result, Exception):
                logger.warning(
                    f"Failed to create ticket in Aide widget API: {str(aide_result)}"
                )
            # If HubSpot request failed, raise the exception
            if isinstance(ticket_info, Exception):
                raise ticket_info
        else:
            ticket_info = await make_hubspot_request(
                access_token=credentials.token,
                method="POST",
                url=url,
                json=ticket_data,
            )

        ticket_id = str(ticket_info.get("id", ""))

        result = CreateTicketResult(
            success=True,
            message="Ticket created successfully",
            ticket_id=ticket_id,
        )
        return result, {
            "name": "HubSpot",
            "tool": "Create Ticket",
            "data": result,
        }

    except Exception as e:
        logger.error(f"Error creating ticket: {str(e)}")
        return (
            CreateTicketResult(
                success=False,
                message=f"Error creating ticket: {str(e)}",
            ),
            {
                "name": "HubSpot",
                "tool": "Create Ticket",
                "data": CreateTicketResult(
                    success=False,
                    message=f"Error creating ticket: {str(e)}",
                ),
            },
        )


@tool(response_format="content_and_artifact")
async def search_tickets_by_company(
    company_name: str,
    limit: int = 50,
    after: Optional[str] = None,
    config: RunnableConfig = None,
) -> List[HubspotTicket]:
    """
    Search Hubspot tickets associated with a specific company by name.

    Args:
        company_name: Name of the company to find tickets for.
        limit: Maximum number of tickets to return.
        after: Pagination cursor.
        config: Configuration containing user_id.

    Returns:
        List of Hubspot tickets associated with the company.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        # First, search for the company
        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Searching tickets by company with name: {company_name} and limit: {limit} and after: {after}",
            },
            "hubspot",
        )
        company_search_data = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "name",
                            "operator": "CONTAINS_TOKEN",
                            "value": company_name,
                        }
                    ]
                }
            ],
            "properties": ["name"],
            "limit": 10,
        }

        company_response = await make_hubspot_request(
            credentials.token,
            "POST",
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            json=company_search_data,
        )

        companies = company_response.get("results", [])
        if not companies:
            return [], {
                "name": "HubSpot",
                "tool": "Search Tickets by Company",
                "data": [],
            }

        company_id = companies[0].get("id")

        # Try associations
        try:
            associations_url = f"https://api.hubapi.com/crm/v4/objects/companies/{company_id}/associations/tickets"
            associations_response = await make_hubspot_request(
                credentials.token, "GET", associations_url
            )

            associated_tickets = associations_response.get("results", [])
            if not associated_tickets:
                return [], {
                    "name": "HubSpot",
                    "tool": "Search Tickets by Company",
                    "data": [],
                }

            ticket_ids = [
                ticket.get("toObjectId") or ticket.get("id")
                for ticket in associated_tickets
            ]

            tickets = []
            for ticket_id in ticket_ids[:limit]:
                try:
                    ticket_url = (
                        f"https://api.hubapi.com/crm/v3/objects/tickets/{ticket_id}"
                    )
                    ticket_response = await make_hubspot_request(
                        credentials.token, "GET", ticket_url
                    )
                    tickets.append(parse_ticket(ticket_response))
                except Exception:
                    continue

            parsed_tickets = [parse_ticket(ticket) for ticket in tickets]
            emit_update(
                {"tool": "HubSpot", "update": f"Found {len(parsed_tickets)} tickets"},
                "hubspot",
            )
            return parsed_tickets, {
                "name": "HubSpot",
                "tool": "Search Tickets by Company",
                "data": parsed_tickets,
            }

        except Exception:
            # Fallback to text search
            ticket_search_data = {
                "query": company_name,
                "properties": [
                    "subject",
                    "content",
                    "hs_ticket_priority",
                    "hs_ticket_category",
                    "hs_ticket_pipeline",
                    "hs_ticket_stage",
                    "createdate",
                    "lastmodifieddate",
                ],
                "limit": limit,
            }

            if after:
                ticket_search_data["after"] = after

            ticket_response = await make_hubspot_request(
                credentials.token,
                "POST",
                "https://api.hubapi.com/crm/v3/objects/tickets/search",
                json=ticket_search_data,
            )

            tickets = ticket_response.get("results", [])
            parsed_tickets = [parse_ticket(ticket) for ticket in tickets]
            return parsed_tickets, {
                "name": "HubSpot",
                "tool": "Search Tickets by Company",
                "data": parsed_tickets,
            }

    except Exception as e:
        logger.error(f"Error searching tickets by company: {str(e)}")
        raise


# Analytics tools
@tool(response_format="content_and_artifact")
async def get_analytics_summary(
    days: int = 30,
    config: RunnableConfig = None,
) -> HubspotAnalytics:
    """
    Get analytics summary including counts and recent activity.

    Args:
        days: Number of days to look back for recent activity analysis.
        config: Configuration containing user_id.

    Returns:
        HubspotAnalytics object with summary data.
    """
    try:
        configurable = config.get("configurable", {}) if config else {}
        user_id = configurable.get("user_id")
        if not user_id:
            raise ValueError("No user_id provided in configuration")

        credentials = await get_hubspot_credentials(user_id, get_connector_id(configurable))
        if not credentials or not credentials.token:
            raise ValueError("Failed to get HubSpot credentials")

        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Getting analytics summary with days: {days}",
            },
            "hubspot",
        )
        # Get total counts
        contacts_response = await make_hubspot_request(
            credentials.token,
            "GET",
            "https://api.hubapi.com/crm/v3/objects/contacts?limit=1",
        )
        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Found {contacts_response.get('total', 0)} contacts",
            },
            "hubspot",
        )
        companies_response = await make_hubspot_request(
            credentials.token,
            "GET",
            "https://api.hubapi.com/crm/v3/objects/companies?limit=1",
        )
        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Found {companies_response.get('total', 0)} companies",
            },
            "hubspot",
        )
        deals_response = await make_hubspot_request(
            credentials.token,
            "GET",
            "https://api.hubapi.com/crm/v3/objects/deals?limit=1",
        )
        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Found {deals_response.get('total', 0)} deals",
            },
            "hubspot",
        )
        tickets_response = await make_hubspot_request(
            credentials.token,
            "GET",
            "https://api.hubapi.com/crm/v3/objects/tickets?limit=1",
        )
        emit_update(
            {
                "tool": "HubSpot",
                "update": f"Found {tickets_response.get('total', 0)} tickets",
            },
            "hubspot",
        )

        # Get recent activity
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        recent_contacts_data = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "createdate",
                            "operator": "GTE",
                            "value": cutoff_date,
                        }
                    ]
                }
            ],
            "limit": 100,
        }
        recent_contacts_response = await make_hubspot_request(
            credentials.token,
            "POST",
            "https://api.hubapi.com/crm/v3/objects/contacts/search",
            json=recent_contacts_data,
        )

        recent_companies_data = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "createdate",
                            "operator": "GTE",
                            "value": cutoff_date,
                        }
                    ]
                }
            ],
            "limit": 100,
        }
        recent_companies_response = await make_hubspot_request(
            credentials.token,
            "POST",
            "https://api.hubapi.com/crm/v3/objects/companies/search",
            json=recent_companies_data,
        )

        recent_deals_data = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "createdate",
                            "operator": "GTE",
                            "value": cutoff_date,
                        }
                    ]
                }
            ],
            "limit": 100,
        }
        recent_deals_response = await make_hubspot_request(
            credentials.token,
            "POST",
            "https://api.hubapi.com/crm/v3/objects/deals/search",
            json=recent_deals_data,
        )

        recent_tickets_data = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "createdate",
                            "operator": "GTE",
                            "value": cutoff_date,
                        }
                    ]
                }
            ],
            "limit": 100,
        }
        recent_tickets_response = await make_hubspot_request(
            credentials.token,
            "POST",
            "https://api.hubapi.com/crm/v3/objects/tickets/search",
            json=recent_tickets_data,
        )

        # Analyze deal pipeline
        all_deals_response = await make_hubspot_request(
            credentials.token,
            "GET",
            "https://api.hubapi.com/crm/v3/objects/deals?limit=50&properties=dealstage",
        )
        deal_pipeline_summary = {}
        for deal in all_deals_response.get("results", []):
            stage = deal.get("properties", {}).get("dealstage", "Unknown")
            deal_pipeline_summary[stage] = deal_pipeline_summary.get(stage, 0) + 1

        # Analyze ticket priority
        all_tickets_response = await make_hubspot_request(
            credentials.token,
            "GET",
            "https://api.hubapi.com/crm/v3/objects/tickets?limit=50&properties=hs_ticket_priority",
        )
        ticket_priority_summary = {}
        for ticket in all_tickets_response.get("results", []):
            priority = ticket.get("properties", {}).get("hs_ticket_priority", "Unknown")
            ticket_priority_summary[priority] = (
                ticket_priority_summary.get(priority, 0) + 1
            )

        result = HubspotAnalytics(
            total_contacts=contacts_response.get("total", 0),
            total_companies=companies_response.get("total", 0),
            total_deals=deals_response.get("total", 0),
            total_tickets=tickets_response.get("total", 0),
            recent_contacts=len(recent_contacts_response.get("results", [])),
            recent_companies=len(recent_companies_response.get("results", [])),
            recent_deals=len(recent_deals_response.get("results", [])),
            recent_tickets=len(recent_tickets_response.get("results", [])),
            deal_pipeline_summary=deal_pipeline_summary,
            ticket_priority_summary=ticket_priority_summary,
        )

        return result, {
            "name": "HubSpot",
            "tool": "Get Analytics Summary",
            "data": result,
        }
    except Exception as e:
        logger.error(f"Error getting analytics summary: {str(e)}")
        raise


def get_hubspot_tools() -> List[Tool]:
    """Get all HubSpot tools."""
    return [
        list_contacts,
        search_contacts,
        get_contact,
        search_contacts_by_company,
        list_companies,
        search_companies,
        get_company,
        list_deals,
        search_deals,
        get_deal,
        search_deals_by_company,
        list_tickets,
        search_tickets,
        get_ticket,
        create_ticket,
        search_tickets_by_company,
        get_analytics_summary,
    ]
