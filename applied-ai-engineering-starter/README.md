# Support Copilot RAG API

Beginner-friendly open-source project for learning Applied AI engineering.

This project is a small Retrieval-Augmented Generation API. It searches a local knowledge base, builds a grounded answer, returns citations, and includes tests plus a tiny evaluation workflow. It is intentionally simple so a new Applied AI engineer can understand every layer.

## Why This Project Helps Your Portfolio

Applied AI engineering is not only prompting a model. This project shows that you can:

- Build an API around an AI workflow.
- Retrieve relevant context before generating an answer.
- Keep model calls behind a clean interface.
- Return sources so answers are auditable.
- Add tests and evaluation examples.
- Write documentation for contributors.

## Features

- `POST /ask`: ask a question and get an answer with sources.
- `GET /health`: basic service health check.
- Local keyword retrieval with no vector database required.
- Deterministic fake LLM for local development and tests.
- Optional OpenAI adapter boundary for future real model integration.
- Tiny evaluation dataset and evaluation script.

## Quick Start

```bash
cd applied-ai-engineering-starter
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
uvicorn applied_ai_starter.main:app --reload
```

Then open:

```text
http://127.0.0.1:8000/docs
```

Example request:

```bash
curl -X POST http://127.0.0.1:8000/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"question\":\"How do I reset my password?\"}"
```

## Run Tests

```bash
pytest
```

## Run Evaluation

```bash
python -m applied_ai_starter.evaluation
```

## Project Structure

```text
src/applied_ai_starter/
  main.py        FastAPI routes
  rag.py         Retrieval and answer orchestration
  llm.py         Model interface and fake model
  schemas.py     Request/response models
  config.py      Settings
data/
  knowledge_base.md
  eval_questions.jsonl
docs/
  ARCHITECTURE.md
  LEARNING_PATH.md
tests/
  test_api.py
  test_rag.py
```

## Roadmap

- Add embeddings and a vector database.
- Add OpenAI or local model generation.
- Add streaming responses.
- Add LangGraph orchestration.
- Add CI with GitHub Actions.
- Add production logging and tracing.

## License

MIT
