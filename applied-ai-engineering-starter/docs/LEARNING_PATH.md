# Learning Path: Applied AI Engineering From Basic

## Stage 1: Run the App

Goal: understand the shape of an AI API.

- Start the FastAPI server.
- Open `/docs`.
- Send one `/ask` request.
- Read the response fields: `answer`, `sources`, `confidence`.

## Stage 2: Understand Retrieval

Goal: learn why AI systems need context.

- Open `rag.py`.
- Read `load_markdown_documents`.
- Read `KeywordRetriever.search`.
- Change `data/knowledge_base.md` and ask a new question.

## Stage 3: Understand the LLM Boundary

Goal: avoid hard-coding yourself to one model provider.

- Open `llm.py`.
- Notice the `LLMClient` protocol.
- See how `FakeLLM` makes tests stable.

## Stage 4: Add Evaluation

Goal: learn how AI quality is measured.

- Open `data/eval_questions.jsonl`.
- Add one new question.
- Run `python -m applied_ai_starter.evaluation`.

## Stage 5: Make It More Real

Choose one:

- Add embeddings.
- Add streaming.
- Add LangGraph.
- Add a real OpenAI model adapter.
- Add GitHub Actions CI.

## Portfolio Story

When applying for Applied AI engineering roles, describe this project like this:

> I built a small RAG API that retrieves relevant knowledge-base chunks, generates grounded answers, returns citations, and includes tests plus a simple evaluation loop. I designed it with a clean model boundary so the fake test model can be replaced by a production LLM provider.
