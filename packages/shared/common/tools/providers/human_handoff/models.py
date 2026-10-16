from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class EscalationCategory(str, Enum):
    FRUSTRATION = "frustration"
    EXPLICIT_REQUEST = "explicit_request"
    SENSITIVE_TOPIC = "sensitive_topic"
    REPEATED_FAILURE = "repeated_failure"
    OUT_OF_SCOPE = "out_of_scope"
    NONE = "none"


class EscalationResult(BaseModel):
    should_escalate: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    category: EscalationCategory


class EscalationConfig(BaseModel):
    enabled: bool = True
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    #: When empty, the escalation classifier uses the app ``DEFAULT_AGENT_MODEL`` setting.
    model: str = ""
    categories_to_skip: List[EscalationCategory] = []


class HumanHandoffResponse(BaseModel):
    text: str
    success: bool
