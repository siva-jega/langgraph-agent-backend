"""Utility functions for Aide widget: create ticket/customer in app database (user-backend Prisma)."""

import json
import logging
from typing import Any, Dict, Optional

from common.db_queries import (
    get_create_chat_query,
    get_create_customer_query,
    get_create_ticket_query,
)
from common.infrastructure import get_pg

from .models import CreateCustomerResult, CreateTicketResult

logger = logging.getLogger(__name__)

def _describe_bind(value: Any, *, max_str_preview: int = 160) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        preview = value if len(value) <= max_str_preview else value[:max_str_preview] + "…"
        return f"str(len={len(value)}, preview={preview!r})"
    if isinstance(value, (bytes, bytearray)):
        return f"bytes(len={len(value)})"
    return f"{type(value).__name__}({repr(value)[:120]})"


async def _ensure_agent_chat_exists(
    *,
    pg: Any,
    chat_id: str,
    title: str,
    user_id: Optional[str],
) -> Optional[str]:
    """Ensure AgentChats row exists so Customer/Ticket can reference it."""
    if not chat_id:
        return None

    async with pg.acquire() as conn:
        exists = await conn.fetchval(
            'SELECT 1 FROM "AgentChats" WHERE id = $1',
            chat_id,
        )
        if exists:
            return chat_id

        logger.info(
            "aide._ensure_agent_chat_exists: creating AgentChats row chat_id=%s",
            chat_id,
        )
        create_chat_query = get_create_chat_query()
        row = await conn.fetchrow(
            create_chat_query,
            chat_id,
            title,
            "REGULAR",
            user_id or None,
        )
        return str(row["id"]) if row and row.get("id") else None


async def create_widget_ticket(
    widget_id: str,
    chat_id: str,
    platform: str,
    data: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> CreateTicketResult:
    """Create a widget ticket. All field values live in the `data` JSONB column."""
    try:
        pg = get_pg()
        query = get_create_ticket_query()

        agent_chat_id = await _ensure_agent_chat_exists(
            pg=pg,
            chat_id=chat_id or "",
            title=str(data.get("subject", "Ticket")),
            user_id=user_id,
        )

        data.setdefault("status", "OPEN")
        data.setdefault("priority", "MEDIUM")

        try:
            data_json = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as je:
            logger.error("aide.create_widget_ticket: json.dumps failed: %s", je, exc_info=True)
            return CreateTicketResult(success=False, message=f"Invalid data JSON: {je}")

        bind = (widget_id, agent_chat_id, data_json)
        logger.info(
            "aide.create_widget_ticket: executing INSERT platform=%s agent_id=%s data_keys=%s",
            platform, widget_id, sorted(data.keys()),
        )

        async with pg.acquire() as conn:
            row = await conn.fetchrow(query, *bind)
        ticket_id = str(row["id"]) if row else None
        if not ticket_id:
            return CreateTicketResult(
                success=False,
                message="Failed to create ticket: invalid widget/agent id or missing agent workspace",
            )
        logger.info("aide.create_widget_ticket: success ticket_id=%s", ticket_id)
        return CreateTicketResult(success=True, message="Ticket created successfully", ticket_id=ticket_id)
    except RuntimeError:
        logger.warning("DATABASE_URL not set; cannot create ticket")
        return CreateTicketResult(success=False, message="Database not configured")
    except Exception as e:
        logger.error("Error creating widget ticket: %s", e, exc_info=True)
        return CreateTicketResult(success=False, message=f"Error creating widget ticket: {str(e)}")


async def create_widget_customer(
    widget_id: str,
    chat_id: str,
    platform: str,
    data: Dict[str, Any],
    *,
    metadata: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> CreateCustomerResult:
    """Create a widget customer. All field values live in the `data` JSONB column."""
    try:
        pg = get_pg()
        query = get_create_customer_query()

        agent_chat_id = await _ensure_agent_chat_exists(
            pg=pg,
            chat_id=chat_id or "",
            title=str(data.get("name", data.get("intent", "Customer"))),
            user_id=user_id,
        )

        data.setdefault("status", "NEW")

        try:
            data_json = json.dumps(data, ensure_ascii=False)
        except (TypeError, ValueError) as je:
            logger.error("aide.create_widget_customer: json.dumps failed: %s", je, exc_info=True)
            return CreateCustomerResult(success=False, message=f"Invalid data JSON: {je}")

        bind = (widget_id, agent_chat_id, user_id, data_json)
        logger.info(
            "aide.create_widget_customer: executing INSERT platform=%s agent_id=%s data_keys=%s",
            platform, widget_id, sorted(data.keys()),
        )

        async with pg.acquire() as conn:
            row = await conn.fetchrow(query, *bind)
        customer_id = str(row["id"]) if row else None
        if not customer_id:
            return CreateCustomerResult(
                success=False,
                message="Failed to create customer: invalid widget/agent id or missing agent workspace",
            )
        logger.info("aide.create_widget_customer: success customer_id=%s", customer_id)
        return CreateCustomerResult(success=True, message="Customer created successfully", customer_id=customer_id)
    except RuntimeError:
        logger.warning("DATABASE_URL not set; cannot create customer")
        return CreateCustomerResult(success=False, message="Database not configured")
    except Exception as e:
        logger.error("Error creating widget customer: %s", e, exc_info=True)
        return CreateCustomerResult(success=False, message=f"Error creating widget customer: {str(e)}")
