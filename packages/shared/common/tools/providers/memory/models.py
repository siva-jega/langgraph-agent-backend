from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class AddMemoryResult(BaseModel):
    success: bool
    message: str


class MemoryItem(BaseModel):
    id: str
    memory: str
    metadata: Optional[Dict[str, Any]] = None


class GetMemoriesResult(BaseModel):
    success: bool
    message: str
    memories: List[MemoryItem] = []