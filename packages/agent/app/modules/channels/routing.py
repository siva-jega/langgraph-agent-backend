"""Resolve integration, customer, and chat session for an inbound envelope.

Called once per message before normalization. Returns an enriched envelope
(routing context injected into payload.context) + a RoutingContext dataclass,
or None if routing fails (message should be dropped).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.modules.channels.providers.base import ChannelProvider
from app.modules.channels.repository import (
    build_client_session_key,
    fetch_integration_by_account_id,
    find_or_create_customer,
    find_or_create_session,
)
from app.modules.channels.schemas import InboundMessage

logger = logging.getLogger(__name__)


@dataclass
class RoutingContext:
    integration: dict
    integration_row_id: str
    external_user_id: str
    customer_id: str
    chat_id: str


async def resolve_routing(
    envelope: InboundMessage,
    provider: ChannelProvider,
) -> tuple[InboundMessage, RoutingContext] | None:
    """Resolve and inject routing context for Meta-style channels (WA/Messenger/IG).

    Returns (enriched_envelope, RoutingContext) on success, None on failure.
    """
    account_id = provider.extract_account_id(envelope.payload)
    if not account_id:
        logger.error(
            "%s envelope id=%s missing account identifier",
            envelope.channel.capitalize(), envelope.id,
        )
        return None

    integration = await fetch_integration_by_account_id(account_id)
    if not integration:
        logger.error(
            "No AgentIntegration found for %s accountId=%s",
            envelope.channel, account_id,
        )
        return None

    integration_row_id = str(integration.get("id") or "").strip()
    agent_id = str(integration.get("agentId") or "").strip()
    workspace_id = str(integration.get("workspaceId") or "").strip()

    external_user_id = provider.extract_sender_id(envelope.payload)
    if not external_user_id:
        logger.error(
            "%s envelope id=%s missing sender identifier",
            envelope.channel.capitalize(), envelope.id,
        )
        return None

    customer = await find_or_create_customer(
        workspace_id=workspace_id,
        agent_id=agent_id,
        channel=envelope.channel,
        agent_integration_id=integration_row_id,
        external_user_id=external_user_id,
        data=provider.build_customer_data(envelope.payload, external_user_id),
    )
    if not customer:
        logger.error(
            "Failed to resolve customer for %s integration=%s externalUserId=%s",
            envelope.channel, integration_row_id, external_user_id,
        )
        return None

    customer_id = str(customer.get("id") or "").strip()
    client_session_key = build_client_session_key(
        envelope.channel, account_id, external_user_id
    )

    session = await find_or_create_session(
        workspace_id=workspace_id,
        agent_id=agent_id,
        channel=envelope.channel,
        client_session_key=client_session_key,
        customer_id=customer_id or None,
        title=provider.build_chat_title(customer, external_user_id),
        integration_id=integration_row_id,
    )
    if not session:
        logger.error(
            "Failed to resolve session for %s key=%s",
            envelope.channel, client_session_key,
        )
        return None

    enriched = _inject_routing_context(
        envelope=envelope,
        integration=integration,
        external_user_id=external_user_id,
        customer_id=customer_id or None,
        chat_id=session["chat_id"],
    )
    ctx = RoutingContext(
        integration=integration,
        integration_row_id=integration_row_id,
        external_user_id=external_user_id,
        customer_id=customer_id,
        chat_id=session["chat_id"],
    )
    return enriched, ctx


def _inject_routing_context(
    envelope: InboundMessage,
    integration: dict,
    external_user_id: str,
    customer_id: str | None = None,
    chat_id: str | None = None,
) -> InboundMessage:
    """Inject resolved routing fields into payload.context so normalize_inbound can read them."""
    payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    enriched_payload = dict(payload)
    context = dict(enriched_payload.get("context") or {})
    integration_row_id = str(integration.get("id") or "").strip()
    context.update({
        "agentId": str(integration.get("agentId") or "").strip(),
        "integrationId": integration_row_id,
        "agentIntegrationId": integration_row_id,
        "workspaceId": str(integration.get("workspaceId") or "").strip(),
        "externalUserId": external_user_id,
        "customerId": customer_id or "",
        "chatId": chat_id or "",
    })
    enriched_payload["context"] = context
    return envelope.model_copy(update={"payload": enriched_payload})
