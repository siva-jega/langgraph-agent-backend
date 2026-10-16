from typing import List, Optional
from langchain_core.tools import tool, Tool
from pydantic import BaseModel
from .utils import get_zendesk_credentials, make_zendesk_request
from langchain_core.runnables import RunnableConfig
import logging
import asyncio
from .models import CreateTicketResult
from ..aide.utils import create_widget_ticket

logger = logging.getLogger(__name__)


@tool(response_format="content_and_artifact")
async def create_ticket(
    config: RunnableConfig,
    user_email: str,
    user_name: str,
    summary: str,
    description: str,
) -> CreateTicketResult:
    """Create a ticket in Zendesk by providing a ticket summary, description, and user information.

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
        apps = configurable.get("apps") or []
        connectorId = None
        for app in apps:
            try:
                slug = (app.get("slug") or "").lower() if isinstance(app, dict) else ""
            except Exception:
                slug = ""
            if "zendesk" in slug:
                connectorId = app.get("connectorId") or app.get("id")
                break

        credentials = await get_zendesk_credentials(user_id, connectorId)
        if not credentials:
            return CreateTicketResult(
                success=False,
                message=f"Failed to get Zendesk credentials",
            )
        # Extract credentials from token response
        zendesk_domain = credentials.metadata.get("subdomain")
        email = credentials.metadata.get("email")
        api_token = credentials.token

        if not all([zendesk_domain, email, api_token]):
            logger.error(
                f"Zendesk credentials incomplete. Missing zendesk_domain, email, or api_token."
            )
            return (
                CreateTicketResult(
                    success=False,
                    message="Zendesk credentials incomplete. Missing zendesk_domain, email, or api_token.",
                ),
                {
                    "name": "Zendesk",
                    "tool": "Create Ticket",
                    "data": CreateTicketResult(
                        success=False,
                        message="Zendesk credentials incomplete. Missing zendesk_domain, email, or api_token.",
                    ),
                },
            )

        # Create ticket data
        ticket_data = {
            "ticket": {
                "subject": summary,
                "comment": {"body": description},
                "requester": {"name": user_name, "email": user_email},
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
                platform="zendesk",
                comment=aide_comment,
                metadata=None,
            )

        # Make both requests in parallel
        if aide_task:
            ticket_info, aide_result = await asyncio.gather(
                make_zendesk_request(
                    zendesk_domain=zendesk_domain,
                    email=email,
                    api_token=api_token,
                    data=ticket_data,
                ),
                aide_task,
                return_exceptions=True,
            )
            # Log warning if Aide API call failed
            if isinstance(aide_result, Exception):
                logger.warning(
                    f"Failed to create ticket in Aide widget API: {str(aide_result)}"
                )
            # If Zendesk request failed, raise the exception
            if isinstance(ticket_info, Exception):
                raise ticket_info
        else:
            ticket_info = await make_zendesk_request(
                zendesk_domain=zendesk_domain,
                email=email,
                api_token=api_token,
                data=ticket_data,
            )

        ticket_id = str(ticket_info["ticket"]["id"])

        result = CreateTicketResult(
            success=True,
            message="Ticket created successfully",
            ticket_id=ticket_id,
        )
        return result, {
            "name": "Zendesk",
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
                "name": "Zendesk",
                "tool": "Create Ticket",
                "data": CreateTicketResult(
                    success=False,
                    message=f"Error creating ticket: {str(e)}",
                ),
            },
        )


def get_zendesk_tools() -> List[Tool]:
    """Get all Zendesk tools."""
    return [create_ticket]
