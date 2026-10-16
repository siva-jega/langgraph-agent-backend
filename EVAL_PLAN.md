# Agent evals — phased rebuild plan

## Context

The eval system is being rebuilt from scratch. Two earlier scaffolds (`tool_selection/` and `full_flow/`) under `packages/agent/eval/` grew organically across one session and ended up too complicated. They have been removed.

**Goal**: a small, phased, reviewable system that scores web-agent runs along three dimensions — **answer quality**, **retrieval quality**, **prompt quality** — and exposes results in **LangSmith** (hosted) so we don't build our own dashboard.

**Decisions locked in**
- Platform: **LangSmith** (hosted) — owns dataset + experiment + comparison UI
- LLM judges: **RAGAS** (`Faithfulness`, `ResponseRelevancy`, `LLMContextPrecisionWithoutReference`)
- Programmatic judges: **custom** for tool trajectory
- Scope: web agent only (`packages/agent/app/modules/agents/`); channels out of scope
- **Eval account on staging**: real agent configs from a dedicated staging user are the canonical source. JSON templates exist only for hermetic unit tests of the runner.
- **`is_preview=False`** by default — full agent path runs against staging. Side effects on the eval user's data are acceptable.
- Triggers: **HTTP endpoint** on the agent service (Phase 5), invokable from LangSmith webhooks, CI, curl, or an internal admin tool. CLI is the manual alternative.

## Staging eval account — the foundation

Create one user account in the staging workspace, e.g. `evals@example.invalid`. That user creates the agents the eval references. Conventions:

- One agent per "persona shape" we want to benchmark — e.g. `eval-shopify-kb-aide`, `eval-shopify-only`, `eval-handoff-heavy`. Each gets a real `agent_id` (uuid).
- Each agent's **Persona, Apps, Skills, KB attachments** are configured exactly as a production-shape agent would be. The KB documents and Shopify product catalog backing them are the **ground truth** the eval scores against — if you change them, scores drift.
- Dataset cases reference these `agent_id`s. The runner loads each case's config via `AgentsRepository.get_config(agent_id)` from staging Postgres — same code path the real `/chat` endpoint uses.
- Side effects: web-agent write tools (`create_ticket_aide`, `create_lead_aide`, `human_handoff_trigger`) are interrupt-style and pause before persisting — they won't actually create records during eval since no one submits the form. Non-interrupt writes (`send_email`, `slack_send_message`, HubSpot/Calendly, etc.) **do** fire for real against the eval user's connected apps — those apps should be sandbox-only (test Gmail, test Slack workspace, test HubSpot org).
- Locked-down maintenance: no one should manually edit the eval user's agents (would shift the baseline). Treat them like fixtures.

**One-time setup checklist** (documented for the README in Phase 6):
1. Create user `evals@example.invalid` in staging via the normal signup flow
2. Connect the test integrations (test Shopify store, test KB documents, test Gmail / Slack accounts as appropriate)
3. Create the eval agents; record their `agent_id`s
4. Stash the agent_ids in the dataset JSON / env

## What "trigger from anywhere" looks like

LangSmith's UI gives:
- **Playground**: re-run any captured trace with different prompts or models (per-case, interactive)
- **Comparison view**: experiment A vs B per case
- **Annotation queue**: humans label failures

LangSmith does not have a native "Run this dataset" button for arbitrary predict functions. Trigger story:
- **Manual / on-demand**: hit `POST /evals/runs` on the agent service (one URL, JSON body with `{dataset, agent_id?, experiment_name?}`). 202 returns with `run_id`; eval runs in the background; the LangSmith experiment URL is logged.
- **CI**: GitHub Actions workflow calls the same endpoint (or the CLI). Pin to staging URL + a secret header.
- **LangSmith UI button**: a Webhook annotator configured to POST to `/evals/runs` when a button is clicked on an experiment. Set up once in LangSmith, no agent-side code change.
- **CLI**: `python -m app.modules.evals.cli run --dataset web_agent_v1` for local use.

## Phases

Each phase is independently reviewable. Stop after any phase and you have something useful.

---

### Phase 0 — Cleanup + plan file landing ✅

**Goal**: Delete the prior eval scaffolding and place this plan at the project root.

**Done**:
- Removed `packages/agent/eval/` (15 Python files + 4 JSON files)
- Removed `packages/agent/app/modules/agents/graph/tool_selection.py`
- Removed `packages/agent/tests/test_tool_selection_eval.py`, `packages/agent/tests/test_full_flow_eval.py`
- This file now lives at `/Users/adi/Desktop/ai-backend/EVAL_PLAN.md`

**Verification**: `pytest packages/agent/tests/ -q` is green (channel tests unaffected); `rg "tool_selection|full_flow"` finds no stale references.

---

### Phase 1 — LangSmith config + tracing helpers (eval-only)

**Scope correction**: prod / staging traffic does **not** get auto-traced. LangSmith is used by the eval runner only. The runner enables tracing for the duration of an `evaluate(...)` call so each scored case has a clickable trace alongside its scores; outside of evals, the agent graph behaves exactly as before.

**Goal**: Land the LangSmith dependency, env vars, and small tracing-helper module so Phase 2's runner can attach a tracer to its predict calls without touching `graph.py`.

**Files changed**
- `packages/agent/pyproject.toml` — added `langsmith>=0.1.100`
- `packages/agent/app/core/config.py` — added `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT`
- `.env.example` — documented the env vars; clarified they are eval-only
- `packages/agent/app/modules/agents/graph/tracing.py` — **new** module: `langsmith_enabled()`, `build_langsmith_callbacks()`, `build_trace_metadata()`. Used by the eval runner in Phase 2.
- `packages/agent/tests/test_langsmith_tracing.py` — **new** test file (7 tests covering disabled-by-default, env propagation, metadata shaping)

**Verification (done)**
- `pytest packages/agent/tests/ -q` → 103 passed
- `graph.py` unchanged from baseline — no callbacks or metadata wiring added
- `langsmith` importable at v0.4.59

**Review checkpoint**: The helper module is ready for Phase 2 to import. Production agent traffic is untouched. Tracing happens only when the eval runner explicitly turns it on for an `evaluate()` invocation.

---

### Phase 2 — Dataset + runner skeleton  ⚠️ amended

**Scope amendment**: cases reference a real staging `agent_id` (not a local JSON template). The runner loads config via `AgentsRepository.get_config(agent_id)` so the agent's persona / apps / skills / KB attachments are exactly what staging serves. Templates remain available for hermetic unit tests of the runner itself but aren't the canonical eval path.

**Goal**: A curated dataset uploaded to LangSmith, plus a runner that runs each case through the real `Graph.run_non_streaming` against the real staging agent config and creates an experiment.

**Files created (Phase 2 baseline — already landed)**
- `packages/agent/app/modules/evals/__init__.py`
- `packages/agent/app/modules/evals/dataset.py` — `Case` model + `load_cases` + `upload_to_langsmith`
- `packages/agent/app/modules/evals/tool_stubs.py` — `stubbed_write_tools()` context manager. **Now opt-in only** — staging-realistic evals don't use it; cases that exercise non-interrupt write tools (`send_email` etc.) can opt-in for safety.
- `packages/agent/app/modules/evals/runner.py` — `predict()` + `run_dataset()`
- `packages/agent/eval/configs/{shopify_kb_aide,shopify_only}.json` — templates kept for hermetic tests
- `packages/agent/eval/datasets/web_agent_v1.json` — 15 cases

**Phase 2 amendments to apply** (in progress at sign-off):

1. **`Case` model adds `agent_id: Optional[str]`** alongside the existing `agent_config_template`. Validator: exactly one must be set.
2. **`predict()` branches on which is set**:
   - `agent_id` → call `AgentsRepository.get_config(agent_id)` (needs `DATABASE_URL` pointed at staging)
   - `agent_config_template` → load local JSON template (used by hermetic tests)
3. **Default `is_preview=False`** — runs the full graph path. Tests opt into `is_preview=True` to keep them hermetic.
4. **Stubs default off**. `stubbed_write_tools()` becomes an opt-in safety wrapper for cases that exercise non-interrupt writes (`send_email`, Slack send, HubSpot create_ticket). The Case schema gains an optional `stub_write_tools: bool = False` flag.
5. **Bundled dataset is rewritten** to point at staging eval agent_ids (placeholders until the eval account is created; documented in README).

**Key reused code**
- `app.modules.agents.graph.graph.Graph` — the agent runtime
- `app.modules.agents.repository.AgentsRepository.get_config` — loads real agent configs from Postgres
- `app.modules.agents.graph.nodes._get_tools` — already configurable via the registry singleton; opt-in stubs use the same monkey-patch path

**Verification**
- `pytest packages/agent/tests/test_evals_phase2.py -q` → green (uses templates, hermetic)
- Manual smoke (requires staging access): `python -m app.modules.evals.cli upload-dataset --name web_agent_v1` then `... run --dataset web_agent_v1 --agent-id <real_uuid>` → experiment with one trace per case visible in LangSmith
- Read tools (KB, Shopify) return real data — verify by inspecting one trace's retrieved chunks

**Review checkpoint**: Is the eval staging user set up? Are the agent_ids stable? Is the bundled dataset pointing at the right agents?

---

### Phase 3 — RAGAS + programmatic metrics ✅

**Goal**: Each case in the experiment gets scored along five dimensions. Scores attach to the LangSmith experiment so the dashboard shows them.

**Files landed**
- `packages/agent/app/modules/evals/judges/__init__.py` — exports + `default_judges()` (canonical 5-judge bundle)
- `packages/agent/app/modules/evals/judges/ragas_adapter.py` — `to_singleturn(run, example)` plus cached `get_judge_llm()` / `get_judge_embeddings()` so RAGAS metrics share one LLM
- `packages/agent/app/modules/evals/judges/llm.py` — three RAGAS-backed LangSmith evaluators:
  - `faithfulness_evaluator` → `ragas.metrics.Faithfulness`
  - `response_relevancy_evaluator` → `ragas.metrics.ResponseRelevancy`
  - `context_precision_evaluator` → `ragas.metrics.LLMContextPrecisionWithoutReference`
  - All three short-circuit on empty reply / empty retrieval / judge exception with a structured `{key, score, comment}` rather than crashing the experiment
- `packages/agent/app/modules/evals/judges/programmatic.py` — `trajectory_evaluator` (set-semantics with per-extra penalty cap)
- `packages/agent/app/modules/evals/runner.py` — `run_dataset()` now defaults to the 5-judge bundle when `evaluators=None`

**Mapping to the three quality dimensions**
- **Answer quality**: Faithfulness (no hallucinations) + ResponseRelevancy (addresses the query)
- **Retrieval quality**: LLMContextPrecisionWithoutReference (was retrieved context useful?) + trajectory (did we even call the right retrieval tool?)
- **Prompt quality**: aggregate of all of the above, grouped by `prompt_builder_version` (Phase 4 supplies the version)

**Design choices worth flagging**
- LLM judges fail-soft: any exception → `score=0.0` with the exception message in `comment`. A flaky RAGAS call never poisons the whole experiment.
- "No retrieval" is treated as vacuously-1.0 for Faithfulness + ContextPrecision. The trajectory judge handles "should have retrieved but didn't" misses; these LLM judges only score actual retrievals.
- All RAGAS imports are lazy. The eval module imports cleanly without ragas installed; the package is only needed when a judge actually runs.
- `_LLM_SINGLETON` / `_EMBED_SINGLETON` cache the wrapped LLM + embedding model so judges within one experiment share connections.

**Dependencies** — added to `packages/agent/pyproject.toml`:
- `ragas>=0.2.0`

**Verification (done)**
- `pytest packages/agent/tests/test_evals_phase3.py -q` → **23/23 passed**
- Full suite: **153/153 passed**
- RAGAS metrics monkey-patched at the import seam — tests are hermetic, do not require ragas installed

**Pending manual smoke (requires staging + ragas install + LANGSMITH_API_KEY)**
- `python -m app.modules.evals.cli run --dataset web_agent_v1 --experiment first-scored` → every case in LangSmith shows 5 scores
- Manually inspect 3 cases — RAGAS scores should match subjective quality

**Review checkpoint**: Are the judge prompts (RAGAS defaults) producing sensible scores on real data? Are the no-retrieval vacuous-pass semantics right, or should they fail? Anything important missing?

---

### Phase 4 — Prompt version stamping ✅

**Files landed**
- `packages/agent/app/modules/agents/graph/prompt.py`:
  - Added `PROMPT_BUILDER_VERSION = "0.1.0"` constant
  - Added `get_system_prompt_with_meta(additional_params) -> (prompt, version, sha256_hex)`
- `packages/agent/app/modules/agents/graph/graph.py`:
  - Calls `get_system_prompt_with_meta(...)` and stamps `prompt_builder_version` + `prompt_hash` onto `metadata={...}` on the runnable config (alongside agent_id, model, chat_id). Only LangSmith tracers consume this; prod traffic ignores it.
- `packages/agent/app/modules/evals/runner.py`:
  - `run_dataset()` injects `prompt_builder_version` into the experiment metadata via `aevaluate(..., metadata=...)`; caller can override via `metadata={"prompt_builder_version": "..."}`.

**Verification (done)** — `pytest packages/agent/tests/test_evals_phase4.py -q` → 8/8 passed (constant shape, hash determinism, hash changes with prompt content, run_dataset metadata propagation).

---

### Phase 5 — CLI + HTTP trigger ✅

**Files landed**
- `packages/agent/app/modules/evals/cli.py` — argparse with subcommands `upload-dataset`, `run`, `list-runs`, `open`. The `run` subcommand prints the LangSmith experiment URL.
- `packages/agent/app/modules/evals/route.py` — FastAPI router with:
  - `POST /evals/runs` returning `202 Accepted` with a `run_id`. Auth via `x-internal-secret` header matching `resolved_nestjs_internal_secret()` (same shared secret used by `product_faq_generation`). Body: `{dataset, experiment_prefix?, metadata?, is_preview?, no_judges?}`.
  - `GET /evals/runs/{run_id}` returning the current `pending | running | succeeded | failed` plus optional `experiment_url` and `error`. In-memory registry for v1.
- `packages/agent/app/modules/routes.py` — registered the evals router under the existing `/api` prefix.
- `.github/workflows/run-eval.yml` — `workflow_dispatch` job that curls `POST /api/evals/runs` on staging with the user-supplied dataset + experiment prefix; stamps `git_sha` + `triggered_by` into the experiment metadata.

**LangSmith webhook integration** (no code, configured in LangSmith UI):
- Project Settings → Annotation Queues → Add Annotator (type: Webhook). URL: `https://<staging>/api/evals/runs`. Headers: `x-internal-secret: <secret>`. Body: `{"dataset": "web_agent_v1", "experiment_prefix": "from-langsmith"}`. A "Run eval" button appears in the LangSmith UI.

**Verification (done)** — `pytest packages/agent/tests/test_evals_phase5.py -q` → 13/13 passed (argparse routing, dry-run, run-with-metadata, open URL formatting, HTTP 202 + run_id + status round-trip + auth failures + 503 when secret unconfigured + failed-run surfacing).

---

### Phase 6 — LangSmith dashboard config + README ✅

**Documentation landed**
- `packages/agent/app/modules/evals/README.md` — end-to-end operator guide covering: what gets measured, one-time setup (staging account, env vars, dataset upload, LangSmith projects), day-to-day workflows (prompt iteration, CI trigger, LangSmith webhook trigger, local smoke), reading the LangSmith dashboard, adding new cases / metrics, tool-execution policy, troubleshooting.

**LangSmith-side configuration** (manual, documented in README §"Reading the LangSmith dashboard"):
- Two projects: `web-agent-evals` (`LANGSMITH_PROJECT`) and `web-agent-prod` (reserved for future prod tracing — no traffic today).
- Experiment naming convention: `<dataset>-<prompt_version>-<git_short_sha>` produced by the CLI / GitHub workflow.
- Filters worth pinning per project: `prompt_builder_version`, `metadata.git_sha`, `score < 0.7`.
- Annotation queue enabled for failed cases — humans label root causes for prompt iteration.

**Verification**: a teammate following the README can run an eval end-to-end without touching the eval code.

---

## Total scope

| Phase | Output | Est. effort |
|---|---|---|
| 0 | Cleanup + plan file in place | done |
| 1 | LangSmith auto-tracing | 1–2 hours |
| 2 | Dataset + runner skeleton | 3–4 hours |
| 3 | RAGAS + programmatic metrics | 4–6 hours |
| 4 | Prompt version stamping | 1–2 hours |
| 5 | CLI ergonomics | 2 hours |
| 6 | LangSmith config + README | 2 hours |
| **Total** | **End-to-end LangSmith dashboard** | **~14–18 hours** |

CI integration is deferred until the team is comfortable with the LangSmith signal.

## Phase gate

After each phase, stop and review:
1. Does it work end-to-end on a real example?
2. Are the deliverables in the right place?
3. Are any assumptions wrong?
4. Should the next phase change shape based on what we learned?

Only move to the next phase after confirming yes/yes/no/no.
