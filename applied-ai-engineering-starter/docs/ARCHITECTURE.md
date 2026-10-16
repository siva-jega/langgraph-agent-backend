# Architecture

The project is a small RAG API.

```text
Client
  -> FastAPI route
  -> RAG service
  -> Retriever
  -> LLM interface
  -> Answer with sources
```

## Components

## API Layer

`src/applied_ai_starter/main.py`

Defines HTTP endpoints. It should stay thin: validate input, call the RAG service, return structured output.

## RAG Layer

`src/applied_ai_starter/rag.py`

Loads documents, retrieves relevant chunks, builds the prompt context, and asks the model interface for an answer.

## LLM Boundary

`src/applied_ai_starter/llm.py`

Keeps model logic isolated. Tests use `FakeLLM`, which makes the project predictable. A real provider can be added later without rewriting the API.

## Data

`data/knowledge_base.md`

Simple markdown knowledge base. Each `##` heading becomes one searchable document chunk.

## Evaluation

`src/applied_ai_starter/evaluation.py`

Runs sample questions against the system and checks whether expected source titles are retrieved.
