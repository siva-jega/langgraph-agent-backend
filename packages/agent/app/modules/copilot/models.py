from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CopilotChatRequest(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    message: str
    apps: List[dict]
    chatId: str
    userId: str
    fileIds: List[str]
    isNewChat: bool
    kids: List[str]
    webSearch: bool

    def model_post_init(self, __context) -> None:
        """Post-initialization validation."""
        if not self.message.strip():
            raise ValueError("Message cannot be empty")

        if not self.userId:
            raise ValueError("User ID is required")

        if not self.chatId:
            raise ValueError("Chat ID is required for non-preview requests")
