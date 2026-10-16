from fastapi import FastAPI

from applied_ai_starter.config import get_settings
from applied_ai_starter.llm import FakeLLM
from applied_ai_starter.rag import KeywordRetriever, RAGService, load_markdown_documents
from applied_ai_starter.schemas import AskRequest, AskResponse


def build_service() -> RAGService:
    settings = get_settings()
    documents = load_markdown_documents(settings.knowledge_base_path)
    return RAGService(retriever=KeywordRetriever(documents), llm=FakeLLM())


app = FastAPI(
    title="Support Copilot RAG API",
    version="0.1.0",
    description="A beginner-friendly Applied AI engineering project.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    service = build_service()
    return service.ask(payload.question)
