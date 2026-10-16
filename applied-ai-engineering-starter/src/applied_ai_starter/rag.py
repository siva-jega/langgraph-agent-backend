import re
from dataclasses import dataclass
from pathlib import Path

from applied_ai_starter.llm import LLMClient
from applied_ai_starter.schemas import AskResponse, Source


@dataclass(frozen=True)
class Document:
    title: str
    content: str


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2
    }


def load_markdown_documents(path: Path) -> list[Document]:
    """Load a markdown file where each level-2 heading is one document."""
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"^##\s+", text, flags=re.MULTILINE)
    documents: list[Document] = []

    for section in sections[1:]:
        lines = section.strip().splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        content = " ".join(line.strip() for line in lines[1:] if line.strip())
        if title and content:
            documents.append(Document(title=title, content=content))

    return documents


class KeywordRetriever:
    """Tiny keyword-overlap retriever for transparent beginner learning."""

    def __init__(self, documents: list[Document]):
        self.documents = documents

    def search(self, query: str, limit: int = 3) -> list[Source]:
        query_tokens = _tokens(query)
        scored: list[Source] = []

        for doc in self.documents:
            doc_tokens = _tokens(f"{doc.title} {doc.content}")
            if not query_tokens or not doc_tokens:
                continue
            overlap = query_tokens & doc_tokens
            score = len(overlap) / len(query_tokens)
            if score > 0:
                scored.append(
                    Source(title=doc.title, content=doc.content, score=round(score, 3))
                )

        return sorted(scored, key=lambda source: source.score, reverse=True)[:limit]


class RAGService:
    """Coordinates retrieval and generation."""

    def __init__(self, retriever: KeywordRetriever, llm: LLMClient):
        self.retriever = retriever
        self.llm = llm

    def ask(self, question: str) -> AskResponse:
        sources = self.retriever.search(question)
        answer = self.llm.answer(question, sources)
        confidence = sources[0].score if sources else 0.0
        return AskResponse(answer=answer, sources=sources, confidence=confidence)
