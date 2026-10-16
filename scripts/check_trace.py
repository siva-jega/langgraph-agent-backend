#!/usr/bin/env python3
"""Summarize OTLP trace JSON: TTFT, validation, and priority tier per LLM call."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def attrs(span: dict) -> dict[str, object]:
    out: dict[str, object] = {}
    for a in span.get("attributes", []):
        key = a.get("key")
        value = a.get("value", {})
        if "stringValue" in value:
            out[key] = value["stringValue"]
        elif "intValue" in value:
            out[key] = value["intValue"]
        elif "boolValue" in value:
            out[key] = value["boolValue"]
    return out


def ms(start: int | str, end: int | str) -> float:
    return (int(end) - int(start)) / 1e6


def load_spans(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    spans: list[dict] = []
    for batch in data.get("batches", []):
        for lib in batch.get("instrumentationLibrarySpans", []):
            spans.extend(lib.get("spans", []))
    return spans


def user_query(chat_spans: list[dict]) -> str | None:
    if not chat_spans:
        return None
    first = attrs(chat_spans[0])
    for key in sorted(first.keys()):
        if key.endswith(".role") and first[key] == "user":
            idx = key.split(".")[2]
            content = first.get(f"gen_ai.prompt.{idx}.content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return None


def validation_summary(content: str) -> str | None:
    if "evaluated_products" not in content:
        return None
    result = json.loads(content)
    products = result.get("evaluated_products", [])
    passed = sum(
        1
        for p in products
        if p.get("is_relevant") and float(p.get("relevance_score", 0)) >= 0.7
    )
    return f"VALIDATION {passed}/{len(products)} passed"


def analyze_trace(path: Path) -> None:
    spans = load_spans(path)
    root = next((s for s in spans if s["name"] == "POST /api/agents/stream"), None)
    chat_spans = [s for s in spans if s["name"] == "openai.chat"]

    print(f"Trace: {path.name}")
    if root is None:
        print("  No POST /api/agents/stream span found.")
        return

    root_attrs = attrs(root)
    total = ms(root["startTimeUnixNano"], root["endTimeUnixNano"])
    print(f"  Total:  {total:.0f}ms")
    print(f"  Host:   {root_attrs.get('http.server_name', '?')}")
    print(f"  Status: {root_attrs.get('http.status_code', '?')}")
    print(f"  Trace:  {root.get('traceId', '?')}")

    query = user_query(chat_spans)
    if query:
        preview = query if len(query) <= 120 else query[:117] + "..."
        print(f"  Query:  {preview!r}")

    if not chat_spans:
        print("  No openai.chat spans found.")
        return

    print()
    for i, span in enumerate(chat_spans, 1):
        span_attrs = attrs(span)
        total_ms = ms(span["startTimeUnixNano"], span["endTimeUnixNano"])
        parts = [
            f"  Call #{i}: {total_ms:.0f}ms",
            f"model={span_attrs.get('gen_ai.request.model', '?')}",
            f"req={span_attrs.get('openai.request.service_tier', '-')}",
            f"resp={span_attrs.get('openai.response.service_tier', '-')}",
            f"in={span_attrs.get('gen_ai.usage.input_tokens', '?')}",
            f"out={span_attrs.get('gen_ai.usage.output_tokens', '?')}",
        ]

        if span_attrs.get("llm.is_streaming") and span.get("events"):
            ttft = ms(span["startTimeUnixNano"], span["events"][0]["timeUnixNano"])
            stream_window = ms(span["events"][0]["timeUnixNano"], span["events"][-1]["timeUnixNano"])
            parts.append(f"TTFT={ttft:.0f}ms")
            parts.append(f"stream={stream_window:.0f}ms")
            parts.append(f"chunks={len(span['events'])}")
        elif not span_attrs.get("llm.is_streaming"):
            parts.append("non-streaming")

        content = span_attrs.get("gen_ai.completion.0.content") or ""
        if isinstance(content, str):
            summary = validation_summary(content)
            if summary:
                parts.append(summary)
            else:
                tool = span_attrs.get("gen_ai.completion.0.tool_calls.0.name")
                if tool:
                    parts.append(f"tool={tool}")

        print(" | ".join(parts))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/check_trace.py <trace.json> [trace2.json ...]")
        sys.exit(1)

    paths = [Path(p) for p in sys.argv[1:]]
    for i, path in enumerate(paths):
        if i:
            print()
        if not path.exists():
            print(f"Trace: {path}")
            print(f"  File not found: {path}")
            continue
        analyze_trace(path)


if __name__ == "__main__":
    main()
