import json
from pathlib import Path

from applied_ai_starter.llm import FakeLLM
from applied_ai_starter.rag import KeywordRetriever, RAGService, load_markdown_documents


def run_evaluation() -> int:
    root = Path(__file__).resolve().parents[2]
    docs = load_markdown_documents(root / "data" / "knowledge_base.md")
    service = RAGService(KeywordRetriever(docs), FakeLLM())

    total = 0
    passed = 0
    eval_path = root / "data" / "eval_questions.jsonl"

    for line in eval_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += 1
        item = json.loads(line)
        response = service.ask(item["question"])
        top_source = response.sources[0].title if response.sources else ""
        ok = top_source == item["expected_source"]
        passed += int(ok)
        status = "PASS" if ok else "FAIL"
        print(f"{status}: {item['question']} -> {top_source}")

    print(f"\nScore: {passed}/{total}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(run_evaluation())
