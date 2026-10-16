from typing import Protocol

from applied_ai_starter.schemas import Source


class LLMClient(Protocol):
    """Interface for answer generation."""

    def answer(self, question: str, sources: list[Source]) -> str:
        """Return an answer grounded in retrieved sources."""


class FakeLLM:
    """Deterministic local model used for learning and tests."""

    def answer(self, question: str, sources: list[Source]) -> str:
        if not sources:
            return "I do not know based on the available knowledge base."

        best = sources[0]
        return (
            f"Based on {best.title}: {best.content} "
            "Check the source before taking action."
        )
