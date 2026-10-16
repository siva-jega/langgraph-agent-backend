"""Structural typing for settings objects passed into shared services."""

from __future__ import annotations

from typing import Protocol


class SupportsRetrievalSettings(Protocol):
    OPENAI_API_KEY: str | None
    OPENAI_API_BASE: str | None
    OPENAI_EMBEDDING_MODEL: str
    RETRIEVAL_DEFAULT_TOP_K: int
    COHERE_AZURE_API_KEY: str | None
    COHERE_AZURE_BASE_URL: str | None
    COHERE_RERANK_MODEL: str


class SupportsEmailSettings(Protocol):
    RESEND_API_KEY: str | None
    SENDER_EMAIL: str | None


class SupportsTokenSettings(Protocol):
    ENCRYPTION_KEY: str | None
    NESTJS_URL: str | None
