from pathlib import Path

from applied_ai_starter.llm import FakeLLM
from applied_ai_starter.rag import KeywordRetriever, RAGService, load_markdown_documents


def test_load_markdown_documents() -> None:
    docs = load_markdown_documents(Path("data/knowledge_base.md"))

    assert len(docs) == 4
    assert docs[0].title == "Password Reset"


def test_retriever_returns_relevant_source() -> None:
    docs = load_markdown_documents(Path("data/knowledge_base.md"))
    retriever = KeywordRetriever(docs)

    results = retriever.search("How long does express shipping take?")

    assert results
    assert results[0].title == "Shipping Times"


def test_rag_service_returns_answer_with_sources() -> None:
    docs = load_markdown_documents(Path("data/knowledge_base.md"))
    service = RAGService(KeywordRetriever(docs), FakeLLM())

    response = service.ask("Can I delete my account?")

    assert response.sources[0].title == "Account Deletion"
    assert "Based on Account Deletion" in response.answer
