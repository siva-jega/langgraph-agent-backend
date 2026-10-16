"""Pydantic models for channel queue envelopes and normalized channel messages."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Inbound queue envelope (producer -> AI backend)
# ---------------------------------------------------------------------------


class MediaRef(BaseModel):
    key: str
    url: str
    name: str
    size: int
    mimeType: str


class InboundMessage(BaseModel):
    id: str
    channel: Literal["gmail", "whatsapp", "instagram", "messenger"]
    source: str
    payload: Dict[str, Any]
    receivedAt: str
    extractedContent: Optional[str] = None
    mediaRefs: Optional[List[MediaRef]] = None


# ---------------------------------------------------------------------------
# Normalized inbound message (provider -> runtime)
# ---------------------------------------------------------------------------
class InboundSender(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    displayName: Optional[str] = None
    id: Optional[str] = None


class InboundPostback(BaseModel):
    action: str          # VIEW_PRODUCT
    source: str          # SHOPIFY
    productId: str       # external product GID
    variantId: Optional[str] = None  # external variant GID


class InboundMessagePayload(BaseModel):
    text: str
    subject: Optional[str] = None
    messageId: str
    threadId: Optional[str] = None
    inReplyTo: Optional[str] = None
    originalMessageId: Optional[str] = None
    postback: Optional[InboundPostback] = None
    # Persisted message content type (TEXT/BUTTON/INTERACTIVE); button/list taps
    # carry their title + payload here for storage in extraInfo.
    messageType: str = "TEXT"
    buttonPayload: Optional[Dict[str, Any]] = None


class InboundMeta(BaseModel):
    whatsappPhoneNumberId: Optional[str] = None
    pageId: Optional[str] = None


class NormalizedInboundMessage(BaseModel):
    id: str
    timestamp: str
    channel: Literal["gmail", "whatsapp", "instagram", "messenger"]
    agentId: str
    integrationId: str
    agentIntegrationId: Optional[str] = None
    customerId: Optional[str] = None
    workspaceId: str
    chatId: str
    externalUserId: Optional[str] = None
    sender: InboundSender
    message: InboundMessagePayload
    meta: Optional[InboundMeta] = None
    mediaRefs: Optional[List[MediaRef]] = None


# ---------------------------------------------------------------------------
# Outbound (AI backend → NestJS)
# ---------------------------------------------------------------------------

class OutboundReply(BaseModel):
    text: str
    html: Optional[str] = None
    messageId: Optional[str] = None


class OutboundReceipt(BaseModel):
    id: str
    inboundId: str
    channel: str
    agentId: str
    chatId: str
    status: Literal["sent", "failed"]
    reply: Optional[OutboundReply] = None
    error: Optional[str] = None
    tokenUsage: Optional[Dict[str, Any]] = None
    responseTimeMs: Optional[float] = None


# ---------------------------------------------------------------------------
# Provider result
# ---------------------------------------------------------------------------

class SendResult(BaseModel):
    """Returned by ChannelProvider.send()."""

    success: bool
    messageId: Optional[str] = None
    error: Optional[str] = None
