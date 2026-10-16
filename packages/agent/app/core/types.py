from dataclasses import dataclass
from typing import Optional, Any
from enum import Enum

NO_TRACE = True  # exclude this module from auto-tracing


class ParticipantRole(Enum):
    ASSISTANT = "assistant"
    USER = "user"


class MessageRole(Enum):
    """Role for stored message dicts (e.g. in messages repo)."""

    USER = "USER"
    ASSISTANT = "ASSISTANT"


class MessageDeliveryStatus(Enum):
    """Delivery status for an outbound channel message (mirrors Prisma enum)."""

    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"


class MessageContentType(Enum):
    """Content type of a stored message (mirrors Prisma enum)."""

    TEXT = "TEXT"
    TEMPLATE = "TEMPLATE"
    BUTTON = "BUTTON"
    INTERACTIVE = "INTERACTIVE"


class ConversationMessage:
    def __init__(self, role: str, content: Any):
        self.role = role
        self.content = content


@dataclass
class AgentStreamResponse:
    """
    Represents a streaming response from an agent.

    Attributes:
        text: The current text in the stream
        final_message: The complete message when streaming is complete
        token_usage: Token usage information when available
    """

    event_type: Optional[str] = None
    data: Optional[Any] = None
    text: str = ""
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    final_message: Optional[ConversationMessage] = None
    interrupt_data: Optional[dict] = None
    extra_info: Optional[dict] = None
    token_usage: Optional[int] = None
