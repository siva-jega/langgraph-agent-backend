# Web agent evals

End-to-end evaluation harness for the web agent (`app/modules/agents/`).
Scores each run on five dimensions and pushes the experiment to LangSmith.

See [`/EVAL_PLAN.md`](../../../../../EVAL_PLAN.md) at the repo root for the
full phased plan and design rationale. This README is the day-to-day operator
guide.

---

## What gets measured

Three quality dimensions, five evaluators:

| Dimension | Evaluator | Source |
|---|---|---|
| **Answer quality** | `faithfulness` | RAGAS `Faithfulness` — every claim grounded in retrieved context |
| **Answer quality** | `response_relevancy` | RAGAS `ResponseRelevancy` — reply addresses the user's query |
| **Answer quality** | `answer_correctness` | LLM judge — reply satisfies the per-case `golden_reply` rubric |
| **Retrieval quality** | `context_precision` | RAGAS `LLMContextPrecisionWithoutReference` — retrieved chunks were useful |
| **Retrieval quality** | `trajectory` | Programmatic — actual tools match `expected_tools` |
| **Prompt quality** | (aggregate) | Mean of the above, grouped by `prompt_builder_version` in LangSmith |

All five run per case. Each appears as a `feedback` on the LangSmith experiment
and rolls up into per-experiment averages.

---

## One-time setup

### 1. Staging eval account

Create a dedicated user account `evals@example.invalid` (or similar) in the
staging workspace. That user owns the eval agents:

1. Sign up via the normal flow.
2. Create one agent per "persona shape" you want to benchmark. The two
   reference agents the bundled dataset uses are:
   - **shopify_kb_aide** — Shopify + KB + lead/ticket/handoff skills
   - **shopify_only** — Shopify only, no KB or aide
3. For each agent, configure persona, attach KB documents, connect Shopify
   (sandbox store), and add the skills exactly as a production agent would.
4. Record each agent's `agent_id` (visible in the URL after creation).

### 2. Update the dataset

Open `packages/agent/eval/datasets/web_agent_v1.json` and replace the
`REPLACE_WITH_STAGING_AGENT_ID_*` placeholders with the real uuids from step 1.

### 3. Environment variables

Add to your staging deployment env (and your local `.env` for CLI use):

```bash
LANGSMITH_API_KEY=ls__yourkey         # from smith.langchain.com → settings
LANGSMITH_PROJECT=web-agent-evals     # canonical project for evals
DATABASE_URL=postgresql://...staging  # for AgentsRepository.get_config
```

The agent service must have these for `POST /api/evals/runs` to work.

### 4. LangSmith projects

In LangSmith (web UI), create two projects:

- `web-agent-evals` — receives every eval run's traces (set as `LANGSMITH_PROJECT`)
- `web-agent-prod` — reserved for future production tracing (no traffic yet)

Keeping them separate means eval-comparison views aren't polluted by ad-hoc
trace browsing.

### 5. Upload the dataset

One-time push of the cases to LangSmith:

```bash
python -m app.modules.evals.cli upload-dataset \
  --name web_agent_v1 \
  --path packages/agent/eval/datasets/web_agent_v1.json
```

Re-running is safe — the upload is idempotent for the dataset, and appends new
examples on each call.

---

## Day-to-day workflows

### Iterating on a prompt change

```bash
# 1. Edit the prompt
$EDITOR packages/agent/app/modules/agents/graph/prompt.py
# (bump PROMPT_BUILDER_VERSION when the change is material)

# 2. Trigger the eval
python -m app.modules.evals.cli run \
  --dataset web_agent_v1 \
  --experiment "$(git branch --show-current)-$(git rev-parse --short HEAD)" \
  --git-sha "$(git rev-parse HEAD)"

# 3. Open the experiment in LangSmith
python -m app.modules.evals.cli open "<experiment-name-from-step-2>"
```

The experiment carries `prompt_builder_version` + `prompt_hash` metadata, so
the LangSmith comparison view will diff your run against the previous one.

### Triggering from CI / a webhook

`POST /api/evals/runs` is the canonical trigger. Body:

```json
{
  "dataset": "web_agent_v1",
  "experiment_prefix": "ci-${GITHUB_SHA:0:7}",
  "no_judges": false,
  "metadata": {"git_sha": "<sha>", "triggered_by": "<user>"}
}
```

Headers: `x-internal-secret: <INTERNAL_WEBHOOK_SECRET>`.

Response: `202 Accepted` with `{run_id, dataset, status}`. Poll
`GET /api/evals/runs/{run_id}` for completion + experiment URL.

The bundled [`.github/workflows/run-eval.yml`](../../../../../.github/workflows/run-eval.yml)
wraps this in a `workflow_dispatch` job — anyone with repo access can trigger
an eval from GitHub Actions UI without leaving the browser.

### Triggering from LangSmith UI

LangSmith doesn't natively run experiments against arbitrary backends, but you
can wire its **Webhook Annotator** feature to hit our endpoint:

1. LangSmith → your project → Settings → Annotation Queues → Add Annotator
2. Type: **Webhook**
3. URL: `https://<staging-host>/api/evals/runs`
4. Headers: `x-internal-secret: <secret>`
5. Body: `{"dataset": "web_agent_v1", "experiment_prefix": "from-langsmith"}`

A "Run eval" button appears on annotated traces.

### Triggering locally for a quick sanity check

```bash
# Smoke run — no scoring, just verify traces show up
python -m app.modules.evals.cli run \
  --dataset web_agent_v1 \
  --experiment smoke \
  --no-judges
```

---

## Reading the LangSmith dashboard

For each experiment, the **Comparison view** shows per-case rows with one
column per evaluator. Click into any case to see:

- The full trace (every chain step — trim, llm, tools, escalation_check)
- The retrieved context (what each tool returned)
- The reply
- Per-evaluator score + comment + extras (e.g. trajectory's
  `{missing, unexpected, expected, actual}`)

**Filters worth pinning**:

- `prompt_builder_version = X.Y.Z` — focus on one prompt iteration
- `metadata.git_sha = <sha>` — exact commit
- `score < 0.7` — find regressions fast

**Comparison workflow**: open two experiments side-by-side and LangSmith
auto-diffs scores per case. Use this to confirm a prompt change improved the
metric you targeted without regressing others.

---

## Adding a new case

**Single-turn case** — just add an entry:

```json
{
  "id": "product-005",
  "query": "do you have winter boots?",
  "agent_id": "<staging-uuid>",
  "expected_tools": ["get_products"],
  "tags": ["product"]
}
```

**Multi-turn case** — share a `chat_id` (UUID) across turns and order them
with `sequence`. The runner uses the case's `chat_id` as the LangGraph
thread_id (scoped per eval run), so the production checkpointer threads
history naturally — same code path as a real conversation.

Use `score_judges: false` on setup turns so they accumulate context without
dragging down judge averages:

```json
{ "id": "refund-1", "query": "my order is broken", "agent_id": "<uuid>",
  "chat_id": "b8e07461-ec47-47e2-81a5-8ac8fd637522", "sequence": 1,
  "score_judges": false, "expected_tools": [] },

{ "id": "refund-2", "query": "Aditya, adi@example.com", "agent_id": "<uuid>",
  "chat_id": "b8e07461-ec47-47e2-81a5-8ac8fd637522", "sequence": 2,
  "score_judges": false, "expected_tools": [] },

{ "id": "refund-3", "query": "yes please", "agent_id": "<uuid>",
  "chat_id": "b8e07461-ec47-47e2-81a5-8ac8fd637522", "sequence": 3,
  "expected_tools": ["create_ticket_aide"], "tags": ["confirmation"] }
```

Generate a fresh UUID for every new conversation (e.g. `uuidgen` or
`python -c "import uuid; print(uuid.uuid4())"`). Re-using UUIDs across
different conversations would conflate their histories.

Then re-upload:

```
python -m app.modules.evals.cli upload-dataset --name web_agent_v1 --path <path>
```

**Side-effect safety**: for cases that exercise non-interrupt write tools
(`send_email`, Slack send, HubSpot writes, calendar bookings), add
`"stub_write_tools": true` to capture their args without firing.

---

## Adding a new metric

1. Write the evaluator at `app/modules/evals/judges/`. Signature:

   ```python
   async def my_evaluator(run, example) -> dict:
       # run.outputs has reply, tool_calls, retrieved_contexts
       # example.outputs has expected_tools, golden_reply
       return {"key": "my_metric", "score": 0.0, "comment": "..."}
   ```

2. Add to `default_judges()` in `app/modules/evals/judges/__init__.py`.
3. The next `aevaluate` call picks it up — no LangSmith-side configuration
   needed.

---

## Tool execution policy during evals

| Tool category | Behavior during eval | Why |
|---|---|---|
| KB search, product search, order status, gmail/calendar **search** | Real API calls | Need real retrieval data for RAG scoring |
| `create_ticket_aide`, `create_lead_aide`, `human_handoff_trigger` (web) | Real call hits the interrupt → no record persisted | Interrupt-style tools pause for form submission; no submitter during eval, so no side effect |
| `send_email`, `slack_send_message`, `book_meeting_*`, HubSpot writes | Real API calls against the eval account's sandbox apps | These are non-interrupt; the eval user's connected apps must be sandbox-only |
| Anything with `stub_write_tools: true` in the case | Args captured, no execution | Opt-in safety for cases that might touch external services you don't control |

---

## Troubleshooting

**"AgentsRepository.get_config returned None"** — the `agent_id` in your case
doesn't exist in the staging Postgres. Verify the agent exists and
`DATABASE_URL` points at the right DB.

**Trace shows up empty** — confirm `LANGSMITH_API_KEY` is set on the agent
service. `langsmith` SDK silently no-ops without it.

**Judge scores all 0.0** — usually an OpenAI auth issue. Check
`OPENAI_API_KEY` is set; RAGAS judges need it for `gpt-4o`. Check the
`comment` field on the failed scores — exceptions are surfaced there.

**Experiment URL wrong** — the URL builder is best-effort and works for
hosted LangSmith. If you're on a self-hosted instance, override
`LANGSMITH_ENDPOINT`; the URL builder mirrors it.

**Local install fails** — `.venv` may be owned by root from a prior Docker
run. Fix with `sudo chown -R "$(whoami)" .venv && uv sync`.

---

## Files

```
app/modules/evals/
├── __init__.py
├── dataset.py        Case Pydantic model, JSON loader, LangSmith dataset upload
├── tool_stubs.py     Opt-in stub wrapper for non-interrupt write tools
├── runner.py         predict(inputs) + run_dataset() — orchestrates aevaluate
├── route.py          POST /evals/runs, GET /evals/runs/{run_id}
├── cli.py            python -m app.modules.evals.cli ...
├── judges/
│   ├── __init__.py        default_judges() bundle
│   ├── ragas_adapter.py   SingleTurnSample builder + cached judge LLM/embeddings
│   ├── llm.py             Faithfulness / ResponseRelevancy / ContextPrecision
│   └── programmatic.py    trajectory
└── README.md         this file
```
