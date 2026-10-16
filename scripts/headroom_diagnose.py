#!/usr/bin/env python
"""Headroom before/after diagnosis harness (Phase 1.5).

Measures what the Phase-1 compression node would do to *real* tool outputs, so we can
confirm it (a) actually shrinks context and (b) preserves the values that matter — before
and after enabling it in staging.

Two input modes:

  1. OTLP trace export(s)  -- extracts every ``role == "tool"`` message (real production
     tool outputs) from the ``gen_ai.prompt.N.*`` span attributes. Also reports the real
     ``gen_ai.usage.input_tokens`` so you can see what fraction of the prompt is tool output.

  2. Built-in synthetic corpus -- representative tool-output shapes (orders, products,
     KB search, logs) for a quick read with no data file.

For each tool message it runs the SAME compression the graph node runs
(``headroom_compress._compress_messages``), then reports tokens before/after (tiktoken),
the compression ratio, and boundary-value preservation (numbers / ids / error words that
must survive). Prints a per-message table, an aggregate summary, and a pass/fail verdict.

Usage:
    # real production traces (run from repo root, with shared on the path)
    PYTHONPATH=packages/shared:packages/agent uv run --package agent-service \
        python scripts/headroom_diagnose.py --trace "../ai-backend/Trace-4cacd6-2026-06-04 17_43_00.json"

    # quick synthetic read
    PYTHONPATH=packages/shared:packages/agent uv run --package agent-service \
        python scripts/headroom_diagnose.py --synthetic
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# Enable Headroom for this process so the node compresses (it gates on the flag).
os.environ.setdefault("HEADROOM_ENABLED", "true")
os.environ.setdefault("HEADROOM_TELEMETRY", "off")

from langchain_core.messages import HumanMessage, ToolMessage  # noqa: E402

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("o200k_base")

    def ntok(s: str) -> int:
        return len(_ENC.encode(s))

except Exception:  # tiktoken always present in this repo, but degrade gracefully

    def ntok(s: str) -> int:
        return max(1, len(s) // 4)


# ---- OTLP trace extraction -------------------------------------------------

def _iter_span_attr_maps(obj):
    """Yield {attr_key: stringValue} dicts for every span found anywhere in an OTLP export."""
    if isinstance(obj, dict):
        if "attributes" in obj and isinstance(obj["attributes"], list):
            amap = {}
            for a in obj["attributes"]:
                if isinstance(a, dict) and "key" in a and isinstance(a.get("value"), dict):
                    v = a["value"]
                    amap[a["key"]] = (
                        v.get("stringValue")
                        or v.get("intValue")
                        or v.get("doubleValue")
                        or ""
                    )
            if amap:
                yield amap
        for v in obj.values():
            yield from _iter_span_attr_maps(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _iter_span_attr_maps(x)


def extract_tool_messages_from_trace(path: str) -> list[dict]:
    """Return [{name, content, input_tokens(of whole prompt)}] for each tool message."""
    data = json.load(open(path))
    seen: set[tuple] = set()
    out: list[dict] = []
    for amap in _iter_span_attr_maps(data):
        input_tokens = amap.get("gen_ai.usage.input_tokens")
        # collect indices that are tool-role prompts
        for key, role in amap.items():
            m = re.fullmatch(r"gen_ai\.prompt\.(\d+)\.role", key)
            if not m or str(role) != "tool":
                continue
            i = m.group(1)
            content = amap.get(f"gen_ai.prompt.{i}.content", "")
            if not content or len(str(content)) < 50:
                continue
            tcid = amap.get(f"gen_ai.prompt.{i}.tool_call_id", "")
            sig = (str(content)[:200], tcid)
            if sig in seen:
                continue
            seen.add(sig)
            out.append(
                {
                    "name": amap.get(f"gen_ai.prompt.{i}.name") or "tool",
                    "content": str(content),
                    "prompt_input_tokens": int(input_tokens) if str(input_tokens).isdigit() else None,
                }
            )
    return out


# ---- synthetic corpus ------------------------------------------------------

def synthetic_tool_messages() -> list[dict]:
    orders = json.dumps(
        [
            {"id": i, "order_no": f"#10{i:03d}", "status": "shipped", "total": round(i * 12.5, 2),
             "currency": "INR", "tracking": f"TRK{i:08d}", "carrier": "Delhivery"}
            for i in range(60)
        ]
    )
    products = json.dumps(
        [
            {"id": f"gid://shopify/Product/{i}", "title": f"AOKI E-Bike Model {i}", "price": 49999 + i,
             "available": i % 3 != 0, "tags": ["ebike", "commute"], "description": "Long-range commuter e-bike. " * 8}
            for i in range(40)
        ]
    )
    kb = json.dumps(
        {"results": [{"score": 0.9 - i * 0.02, "chunk": f"Warranty clause {i}: coverage details ... " * 12} for i in range(20)]}
    )
    logs = "\n".join(
        [f"2026-06-09T10:00:{i:02d} INFO request ok id={i}" for i in range(50)]
        + ["2026-06-09T10:01:00 ERROR payment gateway timeout order #10042 code=GW-503"]
    )
    return [
        {"name": "get_orders", "content": orders, "prompt_input_tokens": None},
        {"name": "get_products", "content": products, "prompt_input_tokens": None},
        {"name": "search_knowledgebase", "content": kb, "prompt_input_tokens": None},
        {"name": "get_logs", "content": logs, "prompt_input_tokens": None},
    ]


# ---- boundary-value preservation check ------------------------------------

_NUM = re.compile(r"[A-Z]{2,}\d{3,}|#\d+|\b\d{4,}\b|GW-\d+")


def boundary_tokens(s: str) -> set[str]:
    """Identifiers/codes/long numbers that must survive compression."""
    return set(_NUM.findall(s))


# ---- main ------------------------------------------------------------------

def run(messages: list[dict]) -> int:
    from app.modules.agents.graph import headroom_compress as hc

    hc._stock_node = None  # rebuild with env flag settings

    rows = []
    tot_before = tot_after = 0
    boundary_total = boundary_kept = 0
    for msg in messages:
        content = msg["content"]
        state = {
            "messages": [
                HumanMessage(content="(diagnosis replay)"),
                ToolMessage(content=content, tool_call_id=f"call_{len(rows)}", name=msg["name"], id=f"id_{len(rows)}"),
            ]
        }
        replacements, orig_chars, comp_chars = hc._compress_messages(state["messages"])
        if replacements:
            new_content = str(replacements[0].content)
        else:
            new_content = content  # below threshold / unchanged
        b_before, b_after = ntok(content), ntok(new_content)
        tot_before += b_before
        tot_after += b_after
        want = boundary_tokens(content)
        kept = {t for t in want if t in new_content}
        boundary_total += len(want)
        boundary_kept += len(kept)
        rows.append(
            {
                "name": msg["name"],
                "tok_before": b_before,
                "tok_after": b_after,
                "ratio": round(b_after / b_before, 3) if b_before else 1.0,
                "boundary": f"{len(kept)}/{len(want)}",
                "prompt_input_tokens": msg.get("prompt_input_tokens"),
            }
        )

    # print table
    print(f"\n{'tool':28} {'tok_before':>11} {'tok_after':>10} {'ratio':>7} {'boundary_kept':>14}")
    print("-" * 75)
    for r in rows:
        print(f"{r['name'][:28]:28} {r['tok_before']:>11} {r['tok_after']:>10} {r['ratio']:>7} {r['boundary']:>14}")

    saved = tot_before - tot_after
    pct = (saved / tot_before * 100) if tot_before else 0.0
    bpct = (boundary_kept / boundary_total * 100) if boundary_total else 100.0
    print("-" * 75)
    print(f"{'TOTAL':28} {tot_before:>11} {tot_after:>10} {round(tot_after/tot_before,3) if tot_before else 1.0:>7}")
    print(f"\nTool-output tokens saved : {saved}  ({pct:.1f}% reduction on tool outputs)")
    print(f"Boundary values preserved: {boundary_kept}/{boundary_total} ({bpct:.1f}%)")

    real = [r["prompt_input_tokens"] for r in rows if r["prompt_input_tokens"]]
    if real:
        print(f"Sample real prompt input_tokens (from trace usage): min={min(real)} max={max(real)}")

    # verdict
    ok_savings = pct >= 15.0
    ok_boundary = bpct >= 99.5
    print("\nVERDICT:",
          "PASS" if (ok_savings and ok_boundary) else "REVIEW",
          f"(savings {'OK' if ok_savings else 'low'}, boundary {'OK' if ok_boundary else 'LOSS'})")
    print("Note: ground-truth prod savings = compare gen_ai.usage.input_tokens with the flag")
    print("      OFF vs ON on the same conversations in staging; this harness measures the")
    print("      compression step in isolation.")
    return 0 if (ok_savings and ok_boundary) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", action="append", default=[], help="OTLP trace JSON file(s)")
    ap.add_argument("--synthetic", action="store_true", help="use built-in corpus")
    args = ap.parse_args()

    messages: list[dict] = []
    for t in args.trace:
        found = extract_tool_messages_from_trace(t)
        print(f"extracted {len(found)} tool message(s) from {t}")
        messages.extend(found)
    if args.synthetic or not messages:
        if not messages:
            print("no tool messages from traces; using synthetic corpus")
        messages.extend(synthetic_tool_messages())

    return run(messages)


if __name__ == "__main__":
    sys.exit(main())
