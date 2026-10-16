"""Response models for retrieval."""

from dataclasses import dataclass


@dataclass
class RetrievalResponse:
    """Single retrieval hit: text, metadata JSON string, score, source (dense_search | sparse_search)."""

    text: str
    metadata: str
    score: float
    source: str
