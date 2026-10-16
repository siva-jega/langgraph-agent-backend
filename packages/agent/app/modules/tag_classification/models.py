from typing import List, Optional

from pydantic import BaseModel


class AgentTag(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    rules: Optional[str] = None
    category: Optional[str] = None


class TagClassificationResult(BaseModel):
    selected: List[int] = []
