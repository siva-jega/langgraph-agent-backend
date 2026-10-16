# Headroom Integration — TODO

Worktree: `ai-backend-headroom` · branch `feat/headroom-tool-compression` (off `staging` @ 22f0a25)
Plan: `../work/headroom-ai-backend-integration-plan.md`

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Phase 0 — Setup & dependency
- [x] Add `headroom-ai[langchain]` to `packages/agent` deps (v0.26.0), `uv lock`/`sync` — NOT `[all]` (avoids pytorch/ML)
- [x] Verify installed import paths (all in `headroom.integrations.langchain`): `wrap_tools_with_headroom`, `create_compress_tool_messages_node`, `HeadroomConfig`, `HeadroomMode`, `get_tool_metrics`
- [x] Set `HEADROOM_TELEMETRY=off` in `.env.example`
- [x] Add `HEADROOM_ENABLED` (default False) + `HEADROOM_MIN_TOKENS_TO_COMPRESS` + `HEADROOM_PRESERVE_ERRORS` to `config.py` + `.env.example`
- [ ] Confirm CCR store disk needs in k8s (only relevant if/when we enable `headroom_retrieve`)

## Phase 1 — Tool-output compression (main agent only)  ✅ implemented + verified
- [x] Custom compress node `agents/graph/headroom_compress.py` (NOT `wrap_tools_with_headroom` — broken on LC 1.1; NOT stock node directly — drops `.id`). Preserves id/name/tool_call_id; returns only changed msgs so `add_messages` replaces in place.
- [x] Wire flag-gated into `graph.py` between `tools`→`trim` (flag OFF = byte-identical graph)
- [x] Defensive: any compression error falls back to original messages (never breaks convo)
- [x] OTel span attrs `headroom.chars_before/after/saved/ratio` + structured log
- [x] Unit tests (6) + full-graph compile smoke (flag off/on) — all green
- [ ] (deferred) Register `headroom_retrieve` tool for original recovery — only if aggressive config drops needed detail
- [ ] (deferred) Keep OFF for structured generators — N/A: node only touches the main agent graph, not product_summary/faq/persona services

## Phase 1.5 — Baseline + diagnosis harness  ✅ harness built + run on real traces
- [x] `scripts/headroom_diagnose.py` — extracts real `role:"tool"` outputs from OTLP traces, runs node compression, reports tokens before/after + boundary-value preservation + verdict
- [x] Ran on real prod traces + synthetic corpus (results in Decision log)
- [ ] Build live A/B replay fixture (30–50 tool-heavy convos) and run flag OFF vs ON in staging, comparing real `gen_ai.usage.input_tokens`, TTFT, p95 latency, answer-equivalence
- [ ] Tune compression aggressiveness (config / HeadroomMode) — default is conservative on prose; revisit `min_tokens` + SmartCrusher config

## Phase 2 — (gated on Phase 1 clean) model wrap
- [ ] `HeadroomChatModel` around `ChatOpenAI` behind its own flag; validate astream + include_usage + bind_tools
- [ ] Extend tool compression to `channels/ai` and `copilot`

## Phase 3 — Rollout
- [ ] Canary: enable flag for subset of projects in staging → prod
- [ ] Grafana/Tempo dashboard for headroom savings
- [ ] Docs + PR

---

## Decision log

**API verification (critical — done before wiring):**
- `headroom-ai` 0.26.0 installed with `[langchain]` extra (Python 3.13 venv locally; repo targets 3.11).
- ❌ `wrap_tools_with_headroom` is UNUSABLE on LangChain 1.1: it mangles the tool args schema
  (`customer_id` → generic `*args/**kwargs`) and its invoke path raises `TypeError`. Rejected.
- ⚠️ Stock `create_compress_tool_messages_node` compresses well (TOON-style tabular) but DROPS
  `.id` on the returned ToolMessages. Under the graph's `add_messages` reducer that APPENDS a
  duplicate instead of replacing — leaving both the big original AND the compressed copy (more
  tokens + two messages for one tool_call_id → OpenAI 400). So we wrap it: reuse its compression,
  re-attach original id/name/tool_call_id, return only changed msgs. Verified: reducer replaces (3
  msgs, no dup), boundary values survive.
- They already import `toon-format` in `channels/ai/nodes.py` but it's a no-op (raises
  NotImplementedError). Headroom delivers the TOON compression that was reaching for.

**Diagnosis results (node compression measured in isolation, tiktoken o200k):**
- REAL prod traces (Trace-3f5a94): tool outputs 10,710 → 9,490 tok = **11.4% reduction**,
  **boundary values 100% preserved (116/116)**. Verdict REVIEW (savings low on this sample).
  Real prompt input_tokens on these turns: 8.5k–12.1k.
- SYNTHETIC corpus: **37.4% reduction** (10,561 → 6,611), boundary 100% (167/167).
  Per-shape: orders JSON 0.47, KB search 0.19 (big win), product prose 0.89, plain logs 1.0 (none).
- **Takeaways:** (1) compression is real and **lossless on identifiers/codes/errors** in every run;
  (2) magnitude is highly content-dependent — tabular JSON & search results compress hard, prose &
  plain logs barely; (3) default config is conservative → needs tuning to move real-trace savings up.
- **No-side-effects gate:** boundary preservation 100% across all runs is the key safety signal.
  Ground-truth prod savings still require the live staging A/B (flag OFF vs ON) on real conversations.

**Local test runner note:** the `shared` editable `.pth` isn't honored inside a git worktree venv;
run tests with `PYTHONPATH=packages/shared:packages/agent uv run --package agent-service pytest ...`.
