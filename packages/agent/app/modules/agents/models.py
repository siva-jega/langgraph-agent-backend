from typing import Any, Dict, Optional, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResumeConfig(BaseModel):
    """Model for resume configuration."""

    interrupt: bool
    choice: bool
    name: Optional[str] = None
    email: Optional[str] = None
    # media_upload fields submit a MediaRef[] list, so values aren't always strings.
    fields: Optional[Dict[str, Any]] = None


class ProductFaqContextPayload(BaseModel):
    """When the shopper taps a curated product FAQ, the widget sends canonical Q&A."""

    model_config = ConfigDict(populate_by_name=True)

    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    product_title: Optional[str] = Field(None, alias="productTitle")


class ChatRequest(BaseModel):
    """Request model for human-in-the-loop."""

    message: str
    chatId: str
    agentId: str
    sessionId: str
    channel: Literal["web", "whatsapp", "instagram", "messenger"] = "web"
    isPreview: bool = True
    resumeConfig: Optional[ResumeConfig] = None
    websiteInfo: Optional[str] = None
    customerEmail: Optional[str] = None
    customerName: Optional[str] = None
    interruptPayload: Optional[Any] = None
    senderPhone: Optional[str] = None
    whatsappPhoneNumberId: Optional[str] = None
    pageId: Optional[str] = None
    senderDisplayName: Optional[str] = None
    productFaqContext: Optional[ProductFaqContextPayload] = None
    collectionId: Optional[str] = None

    @field_validator("sessionId")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        """Validate that sessionId is a valid UUID."""
        try:
            UUID(v)
        except ValueError:
            raise ValueError("sessionId must be a valid UUID")
        return v

    class Config:
        """Pydantic configuration."""

        validate_assignment = True
        extra = "forbid"

    def model_post_init(self, __context) -> None:
        """Post-initialization validation."""
        # HITL resume uses Command(resume=...) in the graph; `message` is not used as
        # user input and must not be persisted (widget sends empty string).
        if self.resumeConfig and self.resumeConfig.interrupt:
            pass
        elif not self.message.strip():
            raise ValueError("Message cannot be empty")

        if not self.chatId:
            raise ValueError("Chat ID is required for non-preview requests")


class BenchChatRequest(BaseModel):
    """Request model for /agents/bench/stream. Extends chat with a required model override."""

    message: str
    chatId: str
    agentId: str
    sessionId: str
    channel: Literal["web", "whatsapp", "instagram", "messenger"] = "web"
    isPreview: bool = True
    model: str

    @field_validator("sessionId")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        try:
            UUID(v)
        except ValueError:
            raise ValueError("sessionId must be a valid UUID")
        return v

    class Config:
        validate_assignment = True
        extra = "forbid"

    def model_post_init(self, __context) -> None:
        if not self.message.strip():
            raise ValueError("Message cannot be empty")
        if not self.chatId:
            raise ValueError("chatId is required")
        if not self.model.strip():
            raise ValueError("model is required")
