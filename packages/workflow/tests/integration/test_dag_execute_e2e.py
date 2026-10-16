"""
DAG execution end-to-end integration tests.

Uses a real Postgres database via the `pg_pool` fixture.
HTTP calls from HTTP_REQUEST and SEND_MESSAGE nodes are intercepted by respx.
Email calls are intercepted by the `fake_email` fixture.

Run with: uv run pytest tests/integration -m integration
"""
import json
import pytest
import pytest_asyncio
import respx
import httpx
from uuid import uuid4

from workflow_service.engine.dag import DagExecutor
from tests.integration.factories import (
    make_workflow,
    make_node,
    make_edge,
    make_run,
    seed_graph,
)


pytestmark = pytest.mark.integration


# ── helpers ───────────────────────────────────────────────────────────────────

async def run_steps(pg_pool, run_id: str) -> list[dict]:
    """Fetch all node-runs for a run, ordered by startedAt."""
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM public."WorkflowNodeRuns"
            WHERE "workflowRunId" = $1
            ORDER BY "startedAt" ASC NULLS LAST
            """,
            run_id,
        )
    return [dict(r) for r in rows]


async def run_status(pg_pool, run_id: str) -> str:
    async with pg_pool.acquire() as conn:
        return await conn.fetchval(
            'SELECT status FROM public."WorkflowRuns" WHERE id = $1',
            run_id,
        )


# ── empty workflow ────────────────────────────────────────────────────────────

async def test_empty_workflow_completes(pg_pool, workspace_seed):
    ws_id = workspace_seed["workspace_id"]
    wf = await make_workflow(pg_pool, ws_id, status="ACTIVE")
    run = await make_run(pg_pool, wf["id"])

    await DagExecutor().execute(run["id"])

    assert await run_status(pg_pool, run["id"]) == "COMPLETED"
    steps = await run_steps(pg_pool, run["id"])
    assert steps == []


# ── golden path ───────────────────────────────────────────────────────────────

@respx.mock
async def test_golden_path_webhook_http_condition_send_message(pg_pool, workspace_seed, fake_email):
    """
    WEBHOOK → HTTP_REQUEST → CONDITION(true) → SEND_MESSAGE(GMAIL)

    Asserts:
    - run COMPLETED
    - all node-runs COMPLETED
    - expression in SEND_MESSAGE subject resolved from HTTP response
    """
    ws_id = workspace_seed["workspace_id"]

    # Stub the HTTP_REQUEST target
    respx.get("https://api.example.com/order/42").mock(
        return_value=httpx.Response(200, json={"orderId": "42", "status": "paid"})
    )

    trigger_id = "webhook-1"
    http_id = "http-1"
    cond_id = "cond-1"
    send_id = "send-1"

    wf = await seed_graph(
        pg_pool,
        ws_id,
        nodes=[
            {"node_id": trigger_id, "type": "WEBHOOK", "category": "TRIGGER", "label": "Trigger"},
            {
                "node_id": http_id,
                "type": "HTTP_REQUEST",
                "category": "ACTION",
                "label": "Fetch Order",
                "input_parameters": [
                    {"key": "url", "value": "https://api.example.com/order/42"},
                    {"key": "method", "value": "GET"},
                ],
            },
            {
                "node_id": cond_id,
                "type": "CONDITION",
                "category": "ACTION",
                "label": "Check status",
                "input_parameters": [
                    {
                        "key": "conditions",
                        "value": {
                            "combinator": "AND",
                            "rules": [
                                {"left": f"{{{{ ${http_id}.status }}}}", "operator": "equals", "right": "paid"},
                            ],
                        },
                    }
                ],
            },
            {
                "node_id": send_id,
                "type": "SEND_MESSAGE",
                "category": "ACTION",
                "label": "Notify",
                "input_parameters": [
                    {"key": "channel", "value": "GMAIL"},
                    {"key": "to", "value": "customer@example.com"},
                    {"key": "subject", "value": f"Order {{{{ ${http_id}.orderId }}}} is paid"},
                    {"key": "message", "value": "Your order is confirmed."},
                ],
            },
        ],
        edges=[
            {"source": trigger_id, "target": http_id},
            {"source": http_id, "target": cond_id},
            {"source": cond_id, "target": send_id, "source_handle": "true"},
        ],
        status="ACTIVE",
    )

    run = await make_run(pg_pool, wf["id"], input_data={"body": {}})
    await DagExecutor().execute(run["id"])

    assert await run_status(pg_pool, run["id"]) == "COMPLETED"

    steps = await run_steps(pg_pool, run["id"])
    step_map = {s["nodeId"]: s for s in steps}

    assert step_map[trigger_id]["status"] == "COMPLETED"
    assert step_map[http_id]["status"] == "COMPLETED"
    assert step_map[cond_id]["status"] == "COMPLETED"
    assert step_map[send_id]["status"] == "COMPLETED"

    # Verify expression was resolved in SEND_MESSAGE inputData
    send_input = step_map[send_id].get("inputData")
    if isinstance(send_input, str):
        send_input = json.loads(send_input)
    if send_input:
        subject = send_input.get("subject", "")
        assert "42" in subject, f"Expected orderId in subject, got: {subject}"

    assert fake_email, "expected email to have been sent"


# ── condition false → downstream skipped ─────────────────────────────────────

@respx.mock
async def test_condition_false_branch_downstream_skipped(pg_pool, workspace_seed):
    ws_id = workspace_seed["workspace_id"]

    respx.get("https://api.example.com/check").mock(
        return_value=httpx.Response(200, json={"active": False})
    )
    respx.post("http://nestjs-test/internal/meta/send").mock(
        return_value=httpx.Response(200, json={"messageId": "msg-1"})
    )

    trigger_id = "wh-2"
    http_id = "http-2"
    cond_id = "cond-2"
    skip_send_id = "send-skip"
    true_send_id = "send-true"

    wf = await seed_graph(
        pg_pool,
        ws_id,
        nodes=[
            {"node_id": trigger_id, "type": "WEBHOOK", "category": "TRIGGER", "label": "Trigger"},
            {
                "node_id": http_id,
                "type": "HTTP_REQUEST",
                "category": "ACTION",
                "label": "Check",
                "input_parameters": [
                    {"key": "url", "value": "https://api.example.com/check"},
                    {"key": "method", "value": "GET"},
                ],
            },
            {
                "node_id": cond_id,
                "type": "CONDITION",
                "category": "ACTION",
                "label": "Is active?",
                "input_parameters": [
                    {
                        "key": "conditions",
                        "value": {
                            "combinator": "AND",
                            "rules": [{"left": f"{{{{ ${http_id}.active }}}}", "operator": "is_true"}],
                        },
                    }
                ],
            },
            {"node_id": true_send_id, "type": "SEND_MESSAGE", "category": "ACTION", "label": "True branch"},
            {"node_id": skip_send_id, "type": "SEND_MESSAGE", "category": "ACTION", "label": "False branch"},
        ],
        edges=[
            {"source": trigger_id, "target": http_id},
            {"source": http_id, "target": cond_id},
            {"source": cond_id, "target": true_send_id, "source_handle": "true"},
            {"source": cond_id, "target": skip_send_id, "source_handle": "false"},
        ],
        status="ACTIVE",
    )

    run = await make_run(pg_pool, wf["id"], input_data={"body": {}})
    await DagExecutor().execute(run["id"])

    assert await run_status(pg_pool, run["id"]) == "COMPLETED"

    steps = await run_steps(pg_pool, run["id"])
    step_map = {s["nodeId"]: s for s in steps}

    # active=False → condition result=false → false branch runs, true branch skipped
    assert step_map[skip_send_id]["status"] == "COMPLETED"
    assert step_map[true_send_id]["status"] == "SKIPPED"


# ── FAIL policy halts run ──────────────────────────────────────────────────────

@respx.mock
async def test_fail_policy_halts_run_and_downstream(pg_pool, workspace_seed):
    ws_id = workspace_seed["workspace_id"]

    # HTTP_REQUEST returns 500 → raises HttpStepError → FAIL policy → run FAILED
    respx.get("https://api.example.com/fail").mock(
        return_value=httpx.Response(500, json={"error": "internal"})
    )

    trigger_id = "wh-3"
    http_id = "http-3"
    downstream_id = "after-fail"

    wf = await seed_graph(
        pg_pool,
        ws_id,
        nodes=[
            {"node_id": trigger_id, "type": "WEBHOOK", "category": "TRIGGER", "label": "Trigger"},
            {
                "node_id": http_id,
                "type": "HTTP_REQUEST",
                "category": "ACTION",
                "label": "Fail",
                "input_parameters": [
                    {"key": "url", "value": "https://api.example.com/fail"},
                    {"key": "method", "value": "GET"},
                ],
                "error_policy": "FAIL",
            },
            {"node_id": downstream_id, "type": "HTTP_REQUEST", "category": "ACTION", "label": "Downstream"},
        ],
        edges=[
            {"source": trigger_id, "target": http_id},
            {"source": http_id, "target": downstream_id},
        ],
        status="ACTIVE",
    )

    run = await make_run(pg_pool, wf["id"], input_data={"body": {}})
    await DagExecutor().execute(run["id"])

    assert await run_status(pg_pool, run["id"]) == "FAILED"

    steps = await run_steps(pg_pool, run["id"])
    step_map = {s["nodeId"]: s for s in steps}

    assert step_map[http_id]["status"] == "FAILED"
    # Downstream node-run should never be created
    assert downstream_id not in step_map


# ── RETRY policy increments retryCount ────────────────────────────────────────

@respx.mock
async def test_retry_policy_increments_retry_count(pg_pool, workspace_seed):
    ws_id = workspace_seed["workspace_id"]
    call_count = 0

    def flaky_handler(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"ok": True})

    respx.get("https://api.example.com/flaky").mock(side_effect=flaky_handler)

    trigger_id = "wh-retry"
    http_id = "http-retry"

    wf = await seed_graph(
        pg_pool,
        ws_id,
        nodes=[
            {"node_id": trigger_id, "type": "WEBHOOK", "category": "TRIGGER", "label": "Trigger"},
            {
                "node_id": http_id,
                "type": "HTTP_REQUEST",
                "category": "ACTION",
                "label": "Flaky",
                "input_parameters": [
                    {"key": "url", "value": "https://api.example.com/flaky"},
                    {"key": "method", "value": "GET"},
                ],
                "error_policy": "RETRY",
                "config": {"errorPolicy": {"maxRetries": 3, "fallbackPolicy": "FAIL"}},
            },
        ],
        edges=[{"source": trigger_id, "target": http_id}],
        status="ACTIVE",
    )

    run = await make_run(pg_pool, wf["id"], input_data={"body": {}})
    await DagExecutor().execute(run["id"])

    steps = await run_steps(pg_pool, run["id"])
    step_map = {s["nodeId"]: s for s in steps}

    # After 2 failures + 1 success the step should be COMPLETED with retryCount=2
    assert step_map[http_id]["status"] == "COMPLETED"
    assert step_map[http_id]["retryCount"] == 2


# ── expression resolved from upstream output ──────────────────────────────────

@respx.mock
async def test_2_hop_expression_context_threading(pg_pool, workspace_seed):
    """Node 3 references output of node 2, which references output of node 1."""
    ws_id = workspace_seed["workspace_id"]

    respx.get("https://api.example.com/hop1").mock(
        return_value=httpx.Response(200, json={"userId": "u-99"})
    )
    respx.get("https://api.example.com/hop2/u-99").mock(
        return_value=httpx.Response(200, json={"profile": "data"})
    )

    trigger_id = "wh-hop"
    http1_id = "http-hop1"
    http2_id = "http-hop2"

    wf = await seed_graph(
        pg_pool,
        ws_id,
        nodes=[
            {"node_id": trigger_id, "type": "WEBHOOK", "category": "TRIGGER", "label": "Trigger"},
            {
                "node_id": http1_id,
                "type": "HTTP_REQUEST",
                "category": "ACTION",
                "label": "Get User ID",
                "input_parameters": [
                    {"key": "url", "value": "https://api.example.com/hop1"},
                    {"key": "method", "value": "GET"},
                ],
            },
            {
                "node_id": http2_id,
                "type": "HTTP_REQUEST",
                "category": "ACTION",
                "label": "Get Profile",
                "input_parameters": [
                    {"key": "url", "value": f"https://api.example.com/hop2/{{{{ ${http1_id}.userId }}}}"},
                    {"key": "method", "value": "GET"},
                ],
            },
        ],
        edges=[
            {"source": trigger_id, "target": http1_id},
            {"source": http1_id, "target": http2_id},
        ],
        status="ACTIVE",
    )

    run = await make_run(pg_pool, wf["id"], input_data={"body": {}})
    await DagExecutor().execute(run["id"])

    assert await run_status(pg_pool, run["id"]) == "COMPLETED"
    steps = await run_steps(pg_pool, run["id"])
    step_map = {s["nodeId"]: s for s in steps}

    assert step_map[http1_id]["status"] == "COMPLETED"
    assert step_map[http2_id]["status"] == "COMPLETED"

    # Confirm the URL was built with the resolved userId
    http2_input = step_map[http2_id].get("inputData")
    if isinstance(http2_input, str):
        http2_input = json.loads(http2_input)
    if http2_input:
        url = http2_input.get("url", "")
        assert "u-99" in url


# ── no completion email ────────────────────────────────────────────────────────

async def test_no_completion_email_sent(pg_pool, workspace_seed, fake_email):
    """Regression: DagExecutor.execute() must NOT send a completion email."""
    ws_id = workspace_seed["workspace_id"]
    wf = await make_workflow(pg_pool, ws_id, status="ACTIVE")
    run = await make_run(pg_pool, wf["id"])

    await DagExecutor().execute(run["id"])

    assert await run_status(pg_pool, run["id"]) == "COMPLETED"
    assert fake_email == [], f"Unexpected emails sent: {fake_email}"


# ── CONNECTOR workspaceId injection ──────────────────────────────────────────

async def test_connector_workspace_id_injected(pg_pool, workspace_seed, monkeypatch):
    """CONNECTOR node should receive workspaceId from the workflow record."""
    ws_id = workspace_seed["workspace_id"]

    captured = {}

    class FakeConnectorExecutor:
        async def execute(self, config: dict, context: dict) -> dict:
            captured["workspaceId"] = config.get("workspaceId")
            return {"ok": True}

    import workflow_service.engine.nodes as nodes_mod
    original = nodes_mod.STEP_EXECUTORS.get("CONNECTOR")
    nodes_mod.STEP_EXECUTORS["CONNECTOR"] = FakeConnectorExecutor
    try:
        trigger_id = "wh-conn"
        conn_id = "conn-1"

        wf = await seed_graph(
            pg_pool,
            ws_id,
            nodes=[
                {"node_id": trigger_id, "type": "WEBHOOK", "category": "TRIGGER", "label": "Trigger"},
                {"node_id": conn_id, "type": "CONNECTOR", "category": "CONNECTOR", "label": "Connector"},
            ],
            edges=[{"source": trigger_id, "target": conn_id}],
            status="ACTIVE",
        )

        run = await make_run(
            pg_pool,
            wf["id"],
            input_data={"body": {}},
        )
        await DagExecutor().execute(run["id"])

        assert captured.get("workspaceId") == ws_id, (
            f"Expected workspaceId={ws_id!r}, got {captured.get('workspaceId')!r}"
        )
    finally:
        if original is not None:
            nodes_mod.STEP_EXECUTORS["CONNECTOR"] = original
        else:
            del nodes_mod.STEP_EXECUTORS["CONNECTOR"]
