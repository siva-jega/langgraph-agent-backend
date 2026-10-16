"""Database operations for the channels service.

Handles conversation persistence, chat metadata upsert, and integration lookup.
Kept separate from the service orchestrator so providers or tests can reuse them.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any
from typing import Optional

from app.core.types import MessageRole
from common.infrastructure.postgres import get_pg
from app.modules.agents.repository import AgentsRepository
from app.modules.channels.schemas import NormalizedInboundMessage
from app.modules.common.conversation_live_client import (
    schedule_conversation_live_activity,
)

logger = logging.getLogger(__name__)

_repo = AgentsRepository()


async def find_or_create_customer(
    *,
    workspace_id: str,
    agent_id: str,
    channel: str,
    agent_integration_id: str,
    external_user_id: str,
    data: Optional[dict] = None,
) -> Optional[dict]:
    """Resolve a channel customer by stable external identity, creating it if needed."""
    pg = get_pg()
    channel_enum = channel.upper()
    raw_data = data or {}
    payload = json.dumps(raw_data)

    # Extract real-column values from provider data dict
    raw_name = str(raw_data.get("name") or "").strip()
    name_parts = raw_name.split(None, 1) if raw_name else []
    first_name: Optional[str] = name_parts[0] if name_parts else None
    last_name: Optional[str] = name_parts[1] if len(name_parts) > 1 else None
    email: Optional[str] = str(raw_data.get("email") or "").strip() or None
    phone: Optional[str] = str(raw_data.get("phone") or "").strip() or None

    now_iso = datetime.now(timezone.utc).isoformat()
    field_sources: dict = {}
    if email:
        field_sources["email"] = {"source": channel, "updatedAt": now_iso, "locked": False}
    if phone:
        field_sources["phone"] = {"source": channel, "updatedAt": now_iso, "locked": False}
    if first_name:
        field_sources["firstName"] = {"source": channel, "updatedAt": now_iso, "locked": False}
    if last_name:
        field_sources["lastName"] = {"source": channel, "updatedAt": now_iso, "locked": False}

    _select_sql = """
        SELECT id, "workspaceId", "agentId", "agentChatId", "channel",
               "agentIntegrationId", "externalUserId", data
        FROM "Customer"
        WHERE "agentId" = $1
          AND "channel" = $2::"AgentChatChannel"
          AND "agentIntegrationId" = $3
          AND "externalUserId" = $4
        LIMIT 1
    """

    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            _select_sql,
            agent_id,
            channel_enum,
            agent_integration_id,
            external_user_id,
        )
        if row:
            return dict(row)

        async with conn.transaction():
            created = await conn.fetchrow(
                """
                INSERT INTO "Customer" (
                    id, "workspaceId", "agentId", "channel",
                    "agentIntegrationId", "externalUserId", data,
                    email, phone, "firstName", "lastName",
                    "fieldSources", "createdFromSource",
                    "createdAt", "updatedAt"
                )
                VALUES (
                    gen_random_uuid(), $1, $2, $3::"AgentChatChannel",
                    $4, $5, $6::jsonb,
                    $7, $8, $9, $10,
                    $11::jsonb, $12,
                    NOW(), NOW()
                )
                ON CONFLICT DO NOTHING
                RETURNING id, "workspaceId", "agentId", "agentChatId", "channel",
                          "agentIntegrationId", "externalUserId", data
                """,
                workspace_id,
                agent_id,
                channel_enum,
                agent_integration_id,
                external_user_id,
                payload,
                email,
                phone,
                first_name,
                last_name,
                json.dumps(field_sources),
                channel,
            )
            if not created:
                # Lost a race with a concurrent insert — re-fetch the winner.
                row = await conn.fetchrow(
                    _select_sql,
                    agent_id,
                    channel_enum,
                    agent_integration_id,
                    external_user_id,
                )
                return dict(row) if row else None

            await conn.execute(
                """
                INSERT INTO "CustomerIdentity" (
                    id, "customerId", "workspaceId",
                    provider, "identityType", value,
                    "createdAt"
                )
                VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, NOW())
                ON CONFLICT ("workspaceId", provider, "identityType", value) DO NOTHING
                """,
                str(created["id"]),
                workspace_id,
                channel,
                "externalUserId",
                external_user_id,
            )
            return dict(created)


def build_client_session_key(channel: str, account_id: str, external_user_id: str) -> str:
    """Build a stable clientSessionKey for a channel user.

    Format: ``{prefix}_{account_id}_{external_user_id}``
    """
    prefixes = {"whatsapp": "wa", "instagram": "ig", "messenger": "msg", "gmail": "gmail"}
    prefix = prefixes.get(channel, channel)
    return f"{prefix}_{account_id}_{external_user_id}"


async def find_or_create_session(
    *,
    workspace_id: str,
    agent_id: str,
    channel: str,
    client_session_key: str,
    customer_id: Optional[str] = None,
    title: str,
    integration_id: str,
) -> Optional[dict]:
    """Upsert an AgentChatSessions row and return its linked chat.

    Upserts the session, joins to AgentChats in one shot to check for an
    existing chat.  If no chat exists yet, creates one and links it back
    via ``AgentChats.sessionId``.

    Returns a dict with ``session_id`` and ``chat_id``.
    """
    pg = get_pg()
    channel_enum = channel.upper()

    async with pg.acquire() as conn:
        # upsert session and fetch its linked chat in one query
        row = await conn.fetchrow(
            """
            WITH session AS (
                INSERT INTO "AgentChatSessions" (
                    id, "workspaceId", "agentId", channel, "clientSessionKey",
                    "customerId", "createdAt", "updatedAt"
                )
                VALUES (gen_random_uuid(), $1, $2, $3::"AgentChatChannel", $4, $5, NOW(), NOW())
                ON CONFLICT ("agentId", "clientSessionKey") DO UPDATE
                    SET "updatedAt" = NOW()
                RETURNING id
            )
            SELECT s.id AS session_id, ac.id AS chat_id
            FROM session s
            LEFT JOIN "AgentChats" ac ON ac."sessionId" = s.id
            """,
            workspace_id,
            agent_id,
            channel_enum,
            client_session_key,
            customer_id or None,
        )

        session_id = str(row["session_id"])
        existing_chat_id = row["chat_id"]

        if existing_chat_id:
            return {"session_id": session_id, "chat_id": str(existing_chat_id)}

        # no chat yet — create one linked to this session
        chat_row = await conn.fetchrow(
            """
            INSERT INTO "AgentChats" (
                id, "sessionId", title, channel, "integrationId", "agentId",
                "createdAt", "updatedAt"
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3::"AgentChatChannel", $4, $5,
                NOW(), NOW()
            )
            RETURNING id
            """,
            session_id,
            title,
            channel_enum,
            integration_id,
            agent_id,
        )
        if not chat_row:
            return None

        chat_id = str(chat_row["id"])

        # link customer to this chat
        if customer_id:
            await conn.execute(
                """
                UPDATE "Customer"
                SET "agentChatId" = $1, "updatedAt" = NOW()
                WHERE id = $2
                """,
                chat_id,
                customer_id,
            )

        return {"session_id": session_id, "chat_id": chat_id}


async def create_chat_message(
    *,
    agent_chat_id: str,
    role: str,
    message: str,
    client_message_id: str | None = None,
    response_time: float | None = None,
    token_consume: int | None = None,
    sources: Any = None,
    extra_info: Any = None,
    message_type: str = "TEXT",
    status: str | None = None,
    status_timestamps: Any = None,
    reply_to_provider_message_id: str | None = None,
) -> bool:
    """Insert a single AgentChatMessages row. Dedupe on client_message_id when present.

    Returns False only when the message is empty or already exists (duplicate).
    Raises on DB errors so the caller can handle retries.

    Outbound messages pass ``status="SENT"``; when no explicit ``status_timestamps``
    is given for a SENT message, ``sentAt`` is stamped here. Inbound messages leave
    ``status`` NULL. ``message_type`` defaults to TEXT for new writes.

    On successful insert, bumps Redis ``conv:live:{workspace}`` (live inbox index).
    """
    text = (message or "").strip()
    if not text:
        return False

    if status == "SENT" and status_timestamps is None:
        status_timestamps = {"sentAt": datetime.now(timezone.utc).isoformat()}

    status_timestamps_json = (
        json.dumps(status_timestamps) if status_timestamps is not None else None
    )

    pg = get_pg()
    async with pg.acquire() as conn:
        if client_message_id:
            row = await conn.fetchrow(
                """
                INSERT INTO "AgentChatMessages" (
                    id, "agentChatId", message, "client_message_id",
                    "responseTime", role, sources, "extraInfo",
                    "tokenConsume", "messageType", status, "statusTimestamps",
                    "replyToProviderMessageId",
                    "createdAt", "updatedAt"
                )
                VALUES (
                    gen_random_uuid(), $1, $2, $3,
                    $4, $5, $6::jsonb, $7::jsonb,
                    $8, $9::"MessageContentType", $10::"MessageDeliveryStatus", $11::jsonb,
                    $12,
                    NOW(), NOW()
                )
                ON CONFLICT ("agentChatId", "client_message_id") DO NOTHING
                RETURNING id
                """,
                agent_chat_id,
                text,
                client_message_id,
                response_time or 0,
                role,
                json.dumps(sources) if sources is not None else None,
                json.dumps(extra_info) if extra_info is not None else None,
                token_consume or 0,
                message_type,
                status,
                status_timestamps_json,
                reply_to_provider_message_id,
            )
            if row is not None:
                schedule_conversation_live_activity(agent_chat_id)
            return row is not None

        await conn.execute(
            """
            INSERT INTO "AgentChatMessages" (
                id, "agentChatId", message,
                "responseTime", role, sources, "extraInfo",
                "tokenConsume", "messageType", status, "statusTimestamps",
                "replyToProviderMessageId",
                "createdAt", "updatedAt"
            )
            VALUES (
                gen_random_uuid(), $1, $2,
                $3, $4, $5::jsonb, $6::jsonb,
                $7, $8::"MessageContentType", $9::"MessageDeliveryStatus", $10::jsonb,
                $11,
                NOW(), NOW()
            )
            """,
            agent_chat_id,
            text,
            response_time or 0,
            role,
            json.dumps(sources) if sources is not None else None,
            json.dumps(extra_info) if extra_info is not None else None,
            token_consume or 0,
            message_type,
            status,
            status_timestamps_json,
            reply_to_provider_message_id,
        )
    schedule_conversation_live_activity(agent_chat_id)
    return True


def _template_products(extra_info_raw: Any) -> list[dict]:
    """Product objects stored in a template message's ``extraInfo.template.products``.

    Returns the same normalized shape the catalog-card pipeline emits/consumes,
    so they can be re-fed to it directly. Empty list when none.
    """
    if not extra_info_raw:
        return []
    try:
        extra = (
            json.loads(extra_info_raw)
            if isinstance(extra_info_raw, (str, bytes, bytearray))
            else extra_info_raw
        )
    except (ValueError, TypeError):
        return []

    template = extra.get("template") if isinstance(extra, dict) else None
    products = template.get("products") if isinstance(template, dict) else None
    if not isinstance(products, list):
        return []
    return [p for p in products if isinstance(p, dict)]


def _summarize_template_products(extra_info_raw: Any) -> str | None:
    """Build a readable product list from a stored template message's extraInfo.

    Carousel/catalog template rows store only a placeholder in ``message``
    ("[Template: ...] N product(s)"); the actual product names live in
    ``extraInfo.template.products``. Returns None when there is nothing useful.
    """
    products = _template_products(extra_info_raw)
    if not products:
        return None

    lines: list[str] = []
    for idx, product in enumerate(products, start=1):
        name = str(product.get("name") or "").strip()
        if not name:
            continue
        desc = str(product.get("description") or "").strip()
        line = f"{idx}. {name}"
        if desc:
            line += f" — {desc[:80]}"
        lines.append(line)

    if not lines:
        return None
    return "product carousel — " + "; ".join(lines)


async def get_quoted_message_text(
    *, agent_chat_id: str, provider_message_id: str
) -> str | None:
    """Resolve the content of an earlier message in this chat by its provider id
    (wamid), for giving the AI the context a user is replying to.

    For template carousels the ``message`` column is just a placeholder, so the
    product names are reconstructed from ``extraInfo.template.products`` instead.

    Scoped to the chat so it uses the ``(agentChatId, client_message_id)`` index.
    """
    pid = (provider_message_id or "").strip()
    if not pid:
        return None

    pg = get_pg()
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT message, "messageType", "extraInfo"
            FROM "AgentChatMessages"
            WHERE "agentChatId" = $1 AND "client_message_id" = $2
            LIMIT 1
            """,
            agent_chat_id,
            pid,
        )
    if not row:
        return None

    # Prefer reconstructed product details for template messages; fall back to
    # the stored text for plain messages.
    if row["messageType"] == "TEMPLATE":
        summary = _summarize_template_products(row["extraInfo"])
        if summary:
            return summary
    return row["message"]


def _button_product_id(extra_info_raw: Any) -> str | None:
    """The Shopify product id stashed on an add-to-cart BUTTON message's
    ``extraInfo.button.productId`` (written by user-backend), if present."""
    if not extra_info_raw:
        return None
    try:
        extra = (
            json.loads(extra_info_raw)
            if isinstance(extra_info_raw, (str, bytes, bytearray))
            else extra_info_raw
        )
    except (ValueError, TypeError):
        return None
    button = extra.get("button") if isinstance(extra, dict) else None
    product_id = button.get("productId") if isinstance(button, dict) else None
    pid = str(product_id).strip() if product_id else ""
    return pid or None


async def get_cart_and_recent_carousel_products(
    agent_chat_id: str,
) -> tuple[set[str], list[dict]]:
    """For the post-cart "recommend similar" flow.

    Returns ``(cart_product_ids, carousel_products)`` where ``cart_product_ids``
    are the products the customer added to cart in this chat (from BUTTON taps)
    and ``carousel_products`` are the products from the most recent catalog
    carousel shown. Lets the caller re-offer the carousel's other products while
    excluding what is already in the cart.
    """
    pg = get_pg()
    async with pg.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT "messageType", "extraInfo"
            FROM "AgentChatMessages"
            WHERE "agentChatId" = $1
              AND "messageType" IN ('BUTTON', 'TEMPLATE')
            ORDER BY "createdAt" DESC
            """,
            agent_chat_id,
        )

    cart_ids: set[str] = set()
    carousel_products: list[dict] = []
    for row in rows:
        if row["messageType"] == "BUTTON":
            pid = _button_product_id(row["extraInfo"])
            if pid:
                cart_ids.add(pid)
        elif row["messageType"] == "TEMPLATE" and not carousel_products:
            # Most recent template carrying products (the actions template has
            # none, so it is naturally skipped).
            carousel_products = _template_products(row["extraInfo"])
    return cart_ids, carousel_products


async def persist_conversation(
    inbound: NormalizedInboundMessage,
    ai_text: str,
    result: dict,
    agent_config: dict,
    response_time: float,
) -> None:
    """Save the inbound user message + AI response as a conversation in the DB."""
    try:
        user_id = agent_config.get("user_id", "")
        if not user_id:
            logger.warning(
                "Cannot persist channel conversation — no user_id in agent config (agent=%s)",
                inbound.agentId,
            )
            return

        chat_title = build_chat_title(inbound)

        ok = await _repo.save_conversation(
            chat_id=inbound.chatId,
            user_id=user_id,
            agent_id=inbound.agentId,
            session_id="",
            user_message=inbound.message.text,
            assistant_content=ai_text,
            sources=result.get("sources", []),
            token_data=result.get("token_usage", 0),
            extra_info=result.get("extra_info"),
            response_time=response_time,
            channel=inbound.channel,
            chat_title=chat_title,
            integration_id=inbound.integrationId,
        )
        if ok:
            logger.info(
                "Persisted channel conversation chatId=%s channel=%s",
                inbound.chatId,
                inbound.channel,
            )
        else:
            logger.warning("Failed to persist channel conversation chatId=%s", inbound.chatId)
    except Exception:
        logger.exception("Error persisting channel conversation chatId=%s", inbound.chatId)


async def fetch_integration(integration_id: str) -> Optional[dict]:
    """Fetch an AgentIntegration row by ID from Postgres."""
    pg = get_pg()
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT id, "agentId", "workspaceId", creds, metadata, status '
            'FROM "AgentIntegration" WHERE id = $1 LIMIT 1',
            integration_id,
        )
    if not row:
        return None
    return dict(row)


async def fetch_integration_by_account_id(account_id: str) -> Optional[dict]:
    """Fetch an AgentIntegration row by accountId from Postgres."""
    pg = get_pg()
    async with pg.acquire() as conn:
        row = await conn.fetchrow(
            'SELECT id, "agentId", "workspaceId", "integrationId", creds, metadata, status, "accountId" '
            'FROM "AgentIntegration" WHERE "accountId" = $1 LIMIT 1',
            account_id,
        )
    if not row:
        return None
    return dict(row)


async def fetch_asset_by_logical_key(
    agent_integration_id: str, logical_key: str
) -> Optional[dict]:
    """Fetch a single CatalogAsset by logicalKeyId on AgentIntegrationAsset."""
    try:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    ca.id,
                    ca.slug,
                    ca."logicalKey",
                    ca.kind,
                    ca.title,
                    ca.description,
                    ca."metaPayload",
                    ca."previewManifest",
                    ca.parameters,
                    ca.category,
                    ca."sortOrder",
                    ca."createdAt",
                    ca."updatedAt"
                FROM "AgentIntegrationAsset" aia
                JOIN "CatalogAsset" ca ON ca.id = aia."catalogAssetId"
                WHERE aia."agentIntegrationId" = $1
                  AND ca."logicalKey" = $2
                  AND aia.enabled = TRUE
                ORDER BY ca."sortOrder" ASC
                LIMIT 1
                """,
                agent_integration_id,
                logical_key,
            )
        return dict(row) if row else None
    except Exception:
        logger.exception(
            "Failed to fetch asset for agentIntegrationId=%s logicalKey=%s",
            agent_integration_id,
            logical_key,
        )
        return None


async def fetch_best_carousel_asset(
    agent_integration_id: str, product_count: int
) -> Optional[dict]:
    """Fetch the consolidated carousel CatalogAsset and extract the sub-payload
    for the requested product_count.

    The single CatalogAsset row stores a JSON-of-JSON ``metaPayload`` keyed by
    card count (e.g. ``{"2": {...}, "3": {...}, ...}``).  This function resolves
    the best-fit variant and returns the row with a flat ``metaPayload`` so
    callers see the same shape as before.
    """
    row = await fetch_asset_by_logical_key(
        agent_integration_id, "product_cards_carousel"
    )
    if not row:
        return None

    meta_payload = row.get("metaPayload")
    if isinstance(meta_payload, str):
        meta_payload = json.loads(meta_payload)
    if not isinstance(meta_payload, dict):
        return None

    # If no digit keys exist the payload is already a flat template (Instagram /
    # Messenger generic template format) — return as-is.
    available = sorted(int(k) for k in meta_payload if k.isdigit())
    if not available:
        return row

    # WhatsApp: pick the closest count variant.
    count_key = str(product_count)
    variant = meta_payload.get(count_key)
    if not variant:
        closest = min(available, key=lambda k: abs(k - product_count))
        count_key = str(closest)
        variant = meta_payload[count_key]

    resolved = dict(row)
    resolved["metaPayload"] = variant

    for field in ("previewManifest", "parameters"):
        raw = row.get(field)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = None
        if isinstance(raw, dict) and count_key in raw:
            resolved[field] = raw[count_key]

    return resolved


async def fetch_shopify_product_handles(external_ids: list[str]) -> dict[str, str]:
    """Batch-fetch handle for each product by externalId from ShopifyProducts.

    Returns a mapping of {externalId: handle}.
    """
    if not external_ids:
        return {}
    try:
        pg = get_pg()
        async with pg.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT "externalId", handle
                FROM "ShopifyProducts"
                WHERE "externalId" = ANY($1::text[])
                """,
                external_ids,
            )
        return {row["externalId"]: str(row["handle"] or "") for row in rows}
    except Exception:
        logger.exception("Failed to batch-fetch shopify product handles")
        return {}


async def fetch_shopify_product(external_id: str) -> Optional[dict]:
    """Fetch a ShopifyProducts row by externalId, including first variant."""
    try:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    p.id, p."externalId", p.title, p.description,
                    p.handle, p."onlineStoreUrl", p."onlineStorePreviewUrl",
                    p."minVariantPrice", p."maxVariantPrice", p."currencyCode",
                    p.images, p.status,
                    v.id AS "variantId", v."externalId" AS "variantExternalId",
                    v.title AS "variantTitle", v.price AS "variantPrice",
                    v."availableForSale", v.image AS "variantImage"
                FROM "ShopifyProducts" p
                LEFT JOIN "ShopifyVariants" v ON v."productId" = p.id
                WHERE p."externalId" = $1
                ORDER BY v.position ASC
                LIMIT 1
                """,
                external_id,
            )
        return dict(row) if row else None
    except Exception:
        logger.exception(
            "Failed to fetch shopify product externalId=%s",
            external_id,
        )
        return None


async def fetch_shopify_product_by_variant(
    workspace_id: str, variant_external_id: str
) -> Optional[dict]:
    """Fetch a ShopifyProducts row via a variant's externalId."""
    try:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    p.id, p."externalId", p.title, p.description,
                    p.handle, p."onlineStoreUrl", p."onlineStorePreviewUrl",
                    p."minVariantPrice", p."maxVariantPrice", p."currencyCode",
                    p.images, p.status,
                    v.id AS "variantId", v."externalId" AS "variantExternalId",
                    v.title AS "variantTitle", v.price AS "variantPrice",
                    v."availableForSale", v.image AS "variantImage"
                FROM "ShopifyVariants" v
                JOIN "ShopifyProducts" p ON p.id = v."productId"
                WHERE v."externalId" = $1
                  AND p."workspaceId" = $2
                LIMIT 1
                """,
                variant_external_id,
                workspace_id,
            )
        return dict(row) if row else None
    except Exception:
        logger.exception(
            "Failed to fetch shopify product by variant externalId=%s workspace=%s",
            variant_external_id,
            workspace_id,
        )
        return None


async def get_session_id_by_chat_id(chat_id: str) -> Optional[str]:
    """Return the AgentChatSessions.id for a given AgentChats.id."""
    try:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT "sessionId" FROM "AgentChats" WHERE id = $1 LIMIT 1',
                chat_id,
            )
        return str(row["sessionId"]) if row and row["sessionId"] else None
    except Exception:
        logger.exception("Failed to get sessionId for chatId=%s", chat_id)
        return None


async def get_shopify_cart_by_session(session_id: str) -> Optional[dict]:
    """Fetch a ShopifyCart row by agentChatSessionId."""
    try:
        pg = get_pg()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, "agentChatSessionId", "workspaceId",
                       "shopifyDraftOrderId", "checkoutUrl", "createdAt", "updatedAt"
                FROM "ShopifyCart"
                WHERE "agentChatSessionId" = $1
                LIMIT 1
                """,
                session_id,
            )
        return dict(row) if row else None
    except Exception:
        logger.exception("Failed to fetch ShopifyCart for sessionId=%s", session_id)
        return None


async def upsert_shopify_cart(
    session_id: str,
    workspace_id: str,
    draft_order_id: str,
    checkout_url: str,
) -> None:
    """Insert or update a ShopifyCart row keyed on agentChatSessionId."""
    try:
        pg = get_pg()
        async with pg.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO "ShopifyCart" (
                    id, "agentChatSessionId", "workspaceId",
                    "shopifyDraftOrderId", "checkoutUrl",
                    "createdAt", "updatedAt"
                )
                VALUES (gen_random_uuid(), $1, $2, $3, $4, NOW(), NOW())
                ON CONFLICT ("agentChatSessionId") DO UPDATE SET
                    "shopifyDraftOrderId" = EXCLUDED."shopifyDraftOrderId",
                    "checkoutUrl"         = EXCLUDED."checkoutUrl",
                    "updatedAt"           = NOW()
                """,
                session_id,
                workspace_id,
                draft_order_id,
                checkout_url,
            )
    except Exception:
        logger.exception(
            "Failed to upsert ShopifyCart sessionId=%s draftOrderId=%s",
            session_id,
            draft_order_id,
        )


async def get_agent_config(agent_id: str) -> Optional[dict]:
    """Load agent configuration via the shared repository."""
    return await _repo.get_config(agent_id)


def build_chat_title(inbound: NormalizedInboundMessage) -> str:
    """Build a human-readable chat title from the inbound message."""
    parts: list[str] = []
    if inbound.sender.displayName:
        parts.append(inbound.sender.displayName)
    elif inbound.sender.email:
        parts.append(inbound.sender.email)

    if inbound.message.subject:
        parts.append(f"— {inbound.message.subject}")

    return " ".join(parts) if parts else f"{inbound.channel} conversation"
