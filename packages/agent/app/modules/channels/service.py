"""Channels service — inbound message orchestrator.

Flow per message:
  1. Parse envelope → get provider
  2. resolve_routing  → integration / customer / session (Meta channels only)
  3. provider.normalize_inbound → NormalizedInboundMessage
  4. Save inbound message, check handoff
  5. Run AI runtime
  6. Select template assets (lazy — only if sources contain relevant data)
  7. Send reply (order-tracking template OR plain text + optional catalog cards)
  8. Persist assistant message(s)
"""

import asyncio
import logging
import time

from app.core.types import MessageContentType, MessageDeliveryStatus, MessageRole
from app.modules.channels.ecommerce import get_ecommerce_provider_for_agent
from app.modules.channels.manifest import (
    render_catalog_manifest,
    render_order_tracking_manifest,
)
from app.modules.channels.providers import get_provider
from app.modules.channels.repository import (
    create_chat_message,
    fetch_asset_by_logical_key,
    fetch_best_carousel_asset,
    fetch_integration,
    get_agent_config,
    get_cart_and_recent_carousel_products,
    get_quoted_message_text,
)
from app.modules.channels.routing import resolve_routing
from app.modules.channels.ai import get_channel_runtime
from app.modules.channels.ai.prompt import (
    build_additional_params,
    build_channel_base_prompt,
    build_channel_prompt_params,
    build_customer_context,
)
from app.modules.channels.schemas import (
    InboundMessage,
    NormalizedInboundMessage,
    SendResult,
)
from app.modules.common import get_usage
from app.modules.common.handoff_client import check_active_handoff_api
from app.modules.tag_classification import schedule_tag_classification

logger = logging.getLogger(__name__)

# Channels that require routing resolution before normalization
_ROUTED_CHANNELS = {"whatsapp", "instagram", "messenger"}


# ------------------------------------------------------------------ #
# Main entry point                                                    #
# ------------------------------------------------------------------ #


async def process_inbound_message(raw: dict) -> None:
    """Process a single inbound channel message end-to-end."""

    try:
        envelope = InboundMessage(**raw)
    except Exception:
        logger.exception("Failed to parse inbound queue envelope")
        return

    provider = get_provider(envelope.channel)
    if not provider:
        logger.warning(
            "No channel provider registered for channel=%s", envelope.channel
        )
        return

    # --- Phase 1: routing (Meta channels only) ---
    integration_row_id = ""
    resolved_integration: dict | None = None

    if envelope.channel in _ROUTED_CHANNELS:
        routing_result = await resolve_routing(envelope, provider)
        if routing_result is None:
            return
        envelope, routing_ctx = routing_result
        resolved_integration = routing_ctx.integration
        integration_row_id = routing_ctx.integration_row_id

    # --- Phase 2: normalize ---
    inbound: NormalizedInboundMessage | None = None
    try:
        inbound = provider.normalize_inbound(envelope)
    except Exception:
        logger.exception(
            "Failed to normalize inbound envelope id=%s channel=%s",
            envelope.id,
            envelope.channel,
        )
        return

    t0 = time.time()

    try:
        agent_config = await get_agent_config(inbound.agentId)
        if not agent_config:
            logger.error("Agent config not found for agentId=%s", inbound.agentId)
            return

        integration = resolved_integration or await fetch_integration(
            inbound.integrationId
        )
        if not integration:
            logger.error("Integration not found id=%s", inbound.integrationId)
            return

        # --- Phase 3: save inbound message, check handoff ---
        # Attachment-only messages (e.g. an image with no caption) have empty
        # message.text — fall back to a placeholder so they aren't dropped by
        # create_chat_message's empty-text guard before any reply is sent.
        inbound_text = inbound.message.text or ("[attachment]" if inbound.mediaRefs else inbound.message.text)
        inbound_saved = await create_chat_message(
            agent_chat_id=inbound.chatId,
            role=MessageRole.USER.value,
            message=inbound_text,
            client_message_id=inbound.message.messageId,
            reply_to_provider_message_id=inbound.message.inReplyTo,
            message_type=inbound.message.messageType,
            extra_info=(
                {"button": inbound.message.buttonPayload}
                if inbound.message.buttonPayload
                else None
            ),
        )
        if not inbound_saved:
            logger.info(
                "Skipping duplicate/empty inbound chatId=%s messageId=%s",
                inbound.chatId,
                inbound.message.messageId,
            )
            return

        if await check_active_handoff_api(inbound.chatId):
            logger.info(
                "Skipping AI reply — active handoff on chat_id=%s", inbound.chatId
            )
            return

        # Post-add-to-cart "Recommend similar products" tap: a dedicated routine
        # re-offers the carousel's other products (excluding what's in the cart)
        # instead of a normal AI turn.
        if _is_postcart_similar(inbound):
            await _handle_postcart_similar(
                inbound=inbound,
                integration=integration,
                provider=provider,
                agent_config=agent_config,
                integration_row_id=integration_row_id,
            )
            return

        # --- Phase 4: typing indicator ---
        try:
            await provider.send_typing_indicator(inbound, integration)
        except Exception:
            logger.exception(
                "Failed to send typing indicator inbound=%s channel=%s",
                inbound.id,
                inbound.channel,
            )

        # --- Phase 4b: static acknowledgment for media attachments ---
        # Send an immediate static reply so the user knows their image was
        # received while the AI processes the context.
        if inbound.mediaRefs:
            static_ack = "Got your image! Give me a moment to look at it."
            try:
                ack_result = await provider.send(static_ack, static_ack, inbound, integration)
                if ack_result.success:
                    await create_chat_message(
                        agent_chat_id=inbound.chatId,
                        role=MessageRole.ASSISTANT.value,
                        message=static_ack,
                        client_message_id=ack_result.messageId,
                    )
                    logger.info(
                        "Sent static media ack inbound=%s refs=%d",
                        inbound.id,
                        len(inbound.mediaRefs),
                    )
            except Exception:
                logger.exception(
                    "Failed to send static media ack inbound=%s", inbound.id
                )

        # --- Phase 5: run AI (3 attempts with exponential backoff) ---
        additional_params = build_additional_params(agent_config, inbound)
        prompt_params = build_channel_prompt_params(agent_config, inbound)
        customer_ctx = build_customer_context(inbound)
        persona_for_role = (
            prompt_params.get("persona") if isinstance(prompt_params.get("persona"), dict) else {}
        ) or {}
        system_prompt = build_channel_base_prompt(
            prompt_params,
            customer_ctx=customer_ctx,
            format_rules=provider.get_format_rules(inbound),
            few_shots=provider.get_few_shots(inbound),
            role_block=provider.get_role_block(inbound, persona_for_role),
            channel_instructions=provider.get_channel_instructions(resolved_integration),
        )

        # Phase 4: if this message quotes an earlier one, prepend that content so
        # the AI understands what the user is replying to.
        ai_input_text = inbound_text
        if inbound.message.inReplyTo:
            quoted_text = await get_quoted_message_text(
                agent_chat_id=inbound.chatId,
                provider_message_id=inbound.message.inReplyTo,
            )
            if quoted_text:
                snippet = quoted_text.strip()
                if len(snippet) > 500:
                    snippet = snippet[:500] + "…"
                ai_input_text = (
                    f'[The user is replying to this earlier message: "{snippet}"]\n\n'
                    f"{inbound_text}"
                )
                logger.info(
                    "Injected reply context inbound=%s replyTo=%s",
                    inbound.id,
                    inbound.message.inReplyTo,
                )

        # Inject media context into LangGraph so the checkpointer stores image
        # info as part of the conversation history.
        if inbound.mediaRefs:
            image_lines = "\n".join(
                f"- {ref.url or ref.name} ({ref.mimeType})"
                for ref in inbound.mediaRefs
            )
            media_prefix = (
                f"[The user shared {len(inbound.mediaRefs)} image(s):\n{image_lines}]"
            )
            ai_input_text = (
                f"{media_prefix}\n\n{ai_input_text}"
                if ai_input_text and ai_input_text != "[attachment]"
                else media_prefix
            )
            logger.info(
                "Injected media context inbound=%s refs=%d",
                inbound.id,
                len(inbound.mediaRefs),
            )

        ai_result = None
        last_exc: BaseException | None = None
        for attempt in range(1, 4):
            try:
                ai_result = await get_channel_runtime().run_non_streaming(
                    input_text=ai_input_text,
                    additional_params={
                        **additional_params,
                        "channel_system_prompt": system_prompt,
                    },
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "AI runtime attempt %d/3 failed inbound=%s agent=%s: %s",
                    attempt,
                    inbound.id,
                    inbound.agentId,
                    exc,
                )
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)

        if last_exc is not None:
            logger.exception(
                "AI runtime failed after 3 attempts inbound=%s agent=%s — sending error reply",
                inbound.id,
                inbound.agentId,
                exc_info=last_exc,
            )
            try:
                await provider.send(
                    "I'm having some technical difficulties right now. Please try sending your message again in a moment.",
                    "",
                    inbound,
                    integration,
                )
            except Exception:
                logger.warning(
                    "Failed to send error reply to user inbound=%s", inbound.id
                )
            return
        # --- Phase 6: select template assets (only when AI returned relevant data) ---
        raw_sources = ai_result.get("sources") if isinstance(ai_result, dict) else None
        ecommerce_provider = get_ecommerce_provider_for_agent(agent_config)
        products = (
            ecommerce_provider.extract_products_from_sources(raw_sources or [])
            if ecommerce_provider
            else []
        )
        order = (
            ecommerce_provider.extract_order_from_sources(raw_sources or [])
            if ecommerce_provider
            else None
        )

        order_tracking_asset = None
        if integration_row_id and order:
            order_tracking_asset = await fetch_asset_by_logical_key(
                integration_row_id, "order_tracking"
            )

        catalog_asset: dict | None = None
        if integration_row_id and products:
            count = len(products)
            if count == 1 and inbound.channel == "whatsapp":
                catalog_asset = await fetch_asset_by_logical_key(
                    integration_row_id, "single_product_card"
                )
            else:
                catalog_asset = await fetch_best_carousel_asset(integration_row_id, count)

        ai_text = ai_result.get("content") or ""
        if not ai_text:
            logger.warning(
                "Empty AI response inbound=%s agent=%s", inbound.id, inbound.agentId
            )
            # Still proceed when we have a template to send (order tracking or a
            # catalog carousel) — those don't depend on the AI text.
            has_catalog = bool(catalog_asset and products and ecommerce_provider)
            if not (order_tracking_asset and order) and not has_catalog:
                return

        # --- Phase 7: send reply ---
        # When a catalog carousel is available it carries its own body text
        # ("Check out our featured products below.") plus the product cards, so we
        # send it as the single reply and skip the separate AI text message — the
        # prose list would only duplicate what the cards already show.
        will_send_catalog = bool(catalog_asset and products and ecommerce_provider)
        formatted: str | None = None

        if order_tracking_asset and order:
            logger.info(
                "Sending order tracking template asset=%s order=%s channel=%s",
                order_tracking_asset.get("slug"),
                order.get("name"),
                inbound.channel,
            )
            send_result = await provider.send_order_tracking_template(
                inbound=inbound,
                integration=integration,
                asset=order_tracking_asset,
                order_data=order,
            ) or SendResult(success=False, error="order tracking template not sent")
            if send_result.success:
                await _persist_order_tracking_message(
                    inbound=inbound,
                    send_result=send_result,
                    asset=order_tracking_asset,
                    order=order,
                    ai_result=ai_result,
                    t0=t0,
                )
        else:
            catalog_sent = False
            if will_send_catalog:
                catalog_result = await _send_and_persist_catalog_cards(
                    inbound=inbound,
                    integration=integration,
                    provider=provider,
                    agent_config=agent_config,
                    asset=catalog_asset,
                    products=products,
                    ecommerce=ecommerce_provider,
                    intro_text=ai_text,
                )
                catalog_sent = bool(catalog_result and catalog_result.success)

            if catalog_sent:
                # Cards already sent and persisted inside the helper above.
                send_result = SendResult(success=True)
            else:
                # No catalog (or the carousel failed to send) — fall back to the
                # AI text reply so the user always receives a response.
                formatted = provider.format_response(ai_text, inbound, agent_config)
                quick_replies = provider.extract_quick_replies(ai_text) or None
                send_result = await provider.send(formatted, ai_text, inbound, integration, quick_replies=quick_replies)
                if send_result.success:
                    await _persist_text_message(
                        inbound=inbound,
                        send_result=send_result,
                        message=formatted or ai_text,
                        ai_result=ai_result,
                        t0=t0,
                    )

        # --- Phase 8: usage accounting ---
        if send_result.success:
            user_id = agent_config.get("user_id", "")
            if user_id and ai_result.get("extra_info"):
                try:
                    await get_usage().update_usage_consumed(
                        user_id=user_id,
                        token_info=ai_result["extra_info"],
                    )
                except Exception:
                    logger.exception(
                        "Failed to update usage for user_id=%s chatId=%s",
                        user_id,
                        inbound.chatId,
                    )
            schedule_tag_classification(inbound.chatId, inbound.agentId)

        logger.info(
            "Channel message processed: id=%s channel=%s agent=%s success=%s elapsed=%.2fs",
            inbound.id,
            inbound.channel,
            inbound.agentId,
            send_result.success,
            time.time() - t0,
        )

    except Exception:
        logger.exception(
            "Failed to handle channel message id=%s channel=%s",
            inbound.id if inbound else envelope.id,
            envelope.channel,
        )
        raise


# ------------------------------------------------------------------ #
# Persist helpers                                                     #
# ------------------------------------------------------------------ #


async def _persist_order_tracking_message(
    *,
    inbound: NormalizedInboundMessage,
    send_result: SendResult,
    asset: dict,
    order: dict,
    ai_result: dict,
    t0: float,
) -> None:
    order_name = order.get("name") or order.get("id") or ""
    asset_slug = asset.get("slug") or "order_tracking"
    try:
        await create_chat_message(
            agent_chat_id=inbound.chatId,
            role=MessageRole.ASSISTANT.value,
            message=ai_result.get("content")
            or f"[Template: {asset_slug}] Order {order_name}".strip(),
            client_message_id=send_result.messageId,
            response_time=time.time() - t0,
            token_consume=int(ai_result.get("token_usage") or 0),
            sources=ai_result.get("sources"),
            message_type=MessageContentType.TEMPLATE.value,
            status=MessageDeliveryStatus.SENT.value,
            extra_info={
                "template": {
                    "type": "order_tracking",
                    "surface": inbound.channel,
                    "slug": asset_slug,
                    "logicalKey": asset.get("logicalKey"),
                    "order": order,
                    "previewManifest": render_order_tracking_manifest(
                        asset=asset, order=order, inbound=inbound
                    ),
                },
                **(ai_result.get("extra_info") or {}),
            },
        )
    except Exception:
        logger.exception(
            "Failed to persist assistant message chatId=%s — reply was already sent",
            inbound.chatId,
        )


async def _persist_text_message(
    *,
    inbound: NormalizedInboundMessage,
    send_result: SendResult,
    message: str,
    ai_result: dict,
    t0: float,
) -> None:
    try:
        await create_chat_message(
            agent_chat_id=inbound.chatId,
            role=MessageRole.ASSISTANT.value,
            message=message,
            client_message_id=send_result.messageId,
            response_time=time.time() - t0,
            token_consume=int(ai_result.get("token_usage") or 0),
            sources=ai_result.get("sources"),
            status=MessageDeliveryStatus.SENT.value,
            extra_info=ai_result.get("extra_info"),
        )
    except Exception:
        logger.exception(
            "Failed to persist assistant message chatId=%s — reply was already sent",
            inbound.chatId,
        )


async def _send_and_persist_catalog_cards(
    *,
    inbound: NormalizedInboundMessage,
    integration: dict,
    provider,
    agent_config: dict,
    asset: dict,
    products: list[dict],
    ecommerce,
    intro_text: str = "",
) -> SendResult | None:
    try:
        # Enrich products with handle
        product_ids = [str(p.get("id") or "") for p in products if p.get("id")]
        if product_ids:
            handle_map = await ecommerce.fetch_product_handles(product_ids)
            if handle_map:
                products = [
                    {
                        **p,
                        "handle": handle_map.get(
                            str(p.get("id") or ""), str(p.get("handle") or "")
                        ),
                    }
                    for p in products
                ]

        shop_domain = ""
        try:
            creds = await ecommerce.get_credentials(agent_config)
            if creds:
                shop_domain = (
                    creds.metadata.get("shop")
                    or creds.metadata.get("shop_domain")
                    or ""
                )
        except Exception:
            logger.warning(
                "Could not resolve shop_domain for catalog cards inbound=%s", inbound.id
            )

        card_result = await provider.send_catalog_cards(
            inbound=inbound,
            integration=integration,
            asset=asset,
            products=products,
            shop_domain=shop_domain,
            intro_text=intro_text,
        )
        if not card_result:
            return None

        logger.info(
            "Catalog cards sent channel=%s success=%s logicalKey=%s count=%s",
            inbound.channel,
            card_result.success,
            asset.get("logicalKey"),
            len(products),
        )
        if card_result.success:
            catalog_slug = asset.get("slug") or "catalog"
            try:
                await create_chat_message(
                    agent_chat_id=inbound.chatId,
                    role=MessageRole.ASSISTANT.value,
                    message=f"[Template: {catalog_slug}] {len(products)} product(s)",
                    client_message_id=card_result.messageId,
                    response_time=0,
                    token_consume=0,
                    message_type=MessageContentType.TEMPLATE.value,
                    status=MessageDeliveryStatus.SENT.value,
                    extra_info={
                        "template": {
                            "type": "catalog_cards",
                            "surface": inbound.channel,
                            "slug": catalog_slug,
                            "logicalKey": asset.get("logicalKey"),
                            "products": products,
                            "previewManifest": render_catalog_manifest(
                                asset=asset, products=products
                            ),
                        },
                    },
                )
            except Exception:
                logger.exception(
                    "Failed to persist catalog cards message chatId=%s — cards were already sent",
                    inbound.chatId,
                )
        return card_result
    except Exception:
        logger.exception(
            "Failed to send catalog cards inbound=%s channel=%s asset=%s",
            inbound.id,
            inbound.channel,
            asset.get("slug"),
        )


# ------------------------------------------------------------------ #
# Post-add-to-cart "recommend similar" flow                           #
# ------------------------------------------------------------------ #

# Quick-reply payload action codes (must match user-backend's POST_CART).
_POSTCART_SIMILAR = "POSTCART_SIMILAR"
_POSTCART_ELSE = "POSTCART_ELSE"
_POSTCART_DONE = "POSTCART_DONE"
_POSTCART_INTRO_TEXT = "Here are a few more you might like 👇"
_POSTCART_EXHAUSTED_TEXT = (
    "That's everything similar I have for you right now. "
    "Tell me what else you're looking for and I'll help you find it."
)
# Max cards per carousel (Meta supports 2–5 for the seeded variants).
_POSTCART_RECOMMEND_MAX = 5


def _is_postcart_similar(inbound: NormalizedInboundMessage) -> bool:
    if inbound.message.messageType != "BUTTON":
        return False
    payload = inbound.message.buttonPayload
    code = payload.get("payload") if isinstance(payload, dict) else None
    return isinstance(code, str) and code.startswith(_POSTCART_SIMILAR)


def _postcart_seed_product_id(inbound: NormalizedInboundMessage) -> str:
    """The just-added product id from a POSTCART_SIMILAR::SHOPIFY::<id> payload."""
    payload = inbound.message.buttonPayload
    code = payload.get("payload") if isinstance(payload, dict) else ""
    parts = (code or "").split("::")
    return parts[2].strip() if len(parts) >= 3 else ""


async def _handle_postcart_similar(
    *,
    inbound: NormalizedInboundMessage,
    integration: dict,
    provider,
    agent_config: dict,
    integration_row_id: str,
) -> None:
    """Re-offer the most recent carousel's other products (excluding what's in
    the cart), then re-send the actions buttons so the loop can continue. When
    nothing fresh remains, send a graceful note and stop."""
    cart_ids, carousel_products = await get_cart_and_recent_carousel_products(
        inbound.chatId
    )
    candidates = [
        p
        for p in carousel_products
        if str(p.get("id") or "") not in cart_ids
        and str(p.get("externalId") or "") not in cart_ids
    ][:_POSTCART_RECOMMEND_MAX]

    ecommerce = get_ecommerce_provider_for_agent(agent_config)
    if not candidates or not ecommerce or not integration_row_id:
        await _send_postcart_text(provider, inbound, integration, _POSTCART_EXHAUSTED_TEXT)
        return

    count = len(candidates)
    if count == 1 and inbound.channel == "whatsapp":
        asset = await fetch_asset_by_logical_key(
            integration_row_id, "single_product_card"
        )
    else:
        asset = await fetch_best_carousel_asset(integration_row_id, count)
    if not asset:
        await _send_postcart_text(provider, inbound, integration, _POSTCART_EXHAUSTED_TEXT)
        return

    result = await _send_and_persist_catalog_cards(
        inbound=inbound,
        integration=integration,
        provider=provider,
        agent_config=agent_config,
        asset=asset,
        products=candidates,
        ecommerce=ecommerce,
        intro_text=_POSTCART_INTRO_TEXT,
    )
    if not (result and result.success):
        return

    seed = _postcart_seed_product_id(inbound) or str(candidates[0].get("id") or "")
    await _resend_postcart_actions(
        inbound=inbound,
        integration=integration,
        provider=provider,
        integration_row_id=integration_row_id,
        seed_product_id=seed,
    )


async def _resend_postcart_actions(
    *,
    inbound: NormalizedInboundMessage,
    integration: dict,
    provider,
    integration_row_id: str,
    seed_product_id: str,
) -> None:
    """Re-send the 3-button actions template after a recommendation round."""
    if not hasattr(provider, "send_quick_reply_template"):
        return
    try:
        asset = await fetch_asset_by_logical_key(integration_row_id, "postcart_actions")
        if not asset:
            return
        similar_payload = f"{_POSTCART_SIMILAR}::SHOPIFY::{seed_product_id}"[:128]
        result = await provider.send_quick_reply_template(
            inbound=inbound,
            integration=integration,
            asset=asset,
            button_payloads=[_POSTCART_ELSE, similar_payload, _POSTCART_DONE],
        )
        if result and result.success:
            await create_chat_message(
                agent_chat_id=inbound.chatId,
                role=MessageRole.ASSISTANT.value,
                message="What would you like to do next?",
                client_message_id=result.messageId,
                message_type=MessageContentType.TEMPLATE.value,
                status=MessageDeliveryStatus.SENT.value,
            )
    except Exception:
        logger.exception(
            "Failed to re-send post-cart actions inbound=%s", inbound.id
        )


async def _send_postcart_text(provider, inbound, integration, text: str) -> None:
    try:
        result = await provider.send(text, "", inbound, integration)
        if result and result.success:
            await create_chat_message(
                agent_chat_id=inbound.chatId,
                role=MessageRole.ASSISTANT.value,
                message=text,
                client_message_id=result.messageId,
                message_type=MessageContentType.TEXT.value,
                status=MessageDeliveryStatus.SENT.value,
            )
    except Exception:
        logger.exception(
            "Failed to send post-cart fallback text inbound=%s", inbound.id
        )


async def handle_inbound_message(raw: dict) -> None:
    """Backward-compatible alias."""
    await process_inbound_message(raw)
