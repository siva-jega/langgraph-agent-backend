from typing import List

from pydantic import BaseModel, Field


class KnowledgeBaseResponse(BaseModel):
    text: str
    metadata: str
    score: float
    source: str


class KBQueryTranslation(BaseModel):
    """Translation of knowledge-base search-query arguments into English plus
    detected source language. Mirrors the structure of `query`,
    `query_text_for_sparse_search`, and `text_list` so the server can swap the
    fields in place."""

    detected_language: str = Field(
        description=(
            "ISO 639-1 code (e.g. 'en', 'es', 'ta', 'hi', 'ar', 'zh') of the user's "
            "original message language. Use 'en' only when the text is already English."
        )
    )
    query_en: str
    query_text_for_sparse_search_en: str
    text_list_en: List[str]

