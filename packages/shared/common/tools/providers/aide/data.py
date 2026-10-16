from __future__ import annotations

TOOLS_DATA: dict[str, dict[str, str]] = {
    "create_ticket_aide": {
        "id": "create_ticket_aide",
        "name": "Create Ticket",
        "description": "Create a support ticket in Aide using summary and description. Requester email/name come from the chat client session, not from this tool.",
    },
    "create_lead_aide": {
        "id": "create_lead_aide",
        "name": "Create Customer",
        "description": "Create a new customer in Aide using customer intent. Contact email/name come from the chat client session, not from this tool.",
    },
    "create_ticket_aide_channel": {
        "id": "create_ticket_aide_channel",
        "name": "Create Ticket",
        "description": "Create a support ticket on messaging channels (WhatsApp, Instagram, Messenger). No form — contact info is pulled from context. Ask the user for their email if not already known.",
    },
    "create_lead_aide_channel": {
        "id": "create_lead_aide_channel",
        "name": "Create Customer",
        "description": "Create a customer/lead on messaging channels (WhatsApp, Instagram, Messenger). No form — name and phone are pulled from context automatically.",
    },
}

__all__ = ["TOOLS_DATA"]

