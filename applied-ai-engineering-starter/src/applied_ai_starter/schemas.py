from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=3, examples=["How do I reset my password?"])


class Source(BaseModel):
    title: str
    content: str
    score: float


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    confidence: float
