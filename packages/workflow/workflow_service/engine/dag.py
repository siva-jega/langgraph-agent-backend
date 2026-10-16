import asyncio
import json as json_mod
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from common.db_queries import get_workspace_owner_query
from common.infrastructure import get_pg
from workflow_service.engine.error_policy import evaluate_error_policy
from workflow_service.engine.expression import resolve_dict
from workflow_service.engine.nodes import STEP_EXECUTORS
from workflow_service.engine.nodes.wait import PauseRequired
from workflow_service.modules.runs.repository import RunRepository
from workflow_service.modules.workflows.repository import WorkflowRepository

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Naive UTC datetime suitable for TIMESTAMP (no tz) postgres columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


_MAX_VAL_LEN = 300


def _truncate(value: Any, max_len: int = _MAX_VAL_LEN) -> Any:
    if isinstance(value, str):
        return (
            value
            if len(value) <= max_len
            else value[:max_len] + f"…[+{len(value) - max_len}]"
        )
    if isinstance(value, dict):
        return {k: _truncate(v, max_len) for k, v in value.items()}
    if isinstance(value, list):
        truncated = [_truncate(v, max_len) for v in value[:5]]
        return (
            truncated if len(value) <= 5 else truncated + [f"…+{len(value) - 5} more"]
        )
    return value


def _fmt(data: Any) -> str:
    try:
        return json_mod.dumps(_truncate(data), ensure_ascii=False, indent=2)
    except Exception:
        return str(data)[:_MAX_VAL_LEN]


def _summary(data: Any, max_len: int = 160) -> str:
    """Compact single-line key=value summary — skips None/empty values."""
    if not isinstance(data, dict):
        s = str(data)
        return s[:max_len] + "…" if len(s) > max_len else s
    parts = []
    for k, v in data.items():
        if v is None or v == "" or v == {}:
            continue
        if isinstance(v, dict):
            v_str = "{…}" if v else "{}"
        elif isinstance(v, list):
            v_str = f"[{len(v)}]" if v else "[]"
        elif isinstance(v, str) and len(v) > 50:
            v_str = v[:50] + "…"
        else:
            v_str = str(v)
        parts.append(f"{k}={v_str}")
    result = "  ".join(parts)
    return result[:max_len] + "…" if len(result) > max_len else result


_run_repo = RunRepository()
_workflow_repo = WorkflowRepository()


async def _load_resume_state(run_id: str) -> tuple[dict, set, set]:
    """Reconstruct execution state from existing WorkflowNodeRun records.
    Returns (context, executed, skipped_nodes) so the DAG loop can skip already-done nodes."""
    steps = await _run_repo.get_run_steps(run_id)
    context: dict[str, Any] = {}
    executed: set[str] = set()
    skipped_nodes: set[str] = set()
    for step in steps:
        node_id = step["nodeId"]
        status = step.get("status", "")
        if status == "COMPLETED":
            output_raw = step.get("outputData") or {}
            if isinstance(output_raw, str):
                try:
                    output_raw = json_mod.loads(output_raw)
                except Exception:
                    output_raw = {}
            context[node_id] = {"output": output_raw}
            executed.add(node_id)
        elif status == "SKIPPED":
            skipped_nodes.add(node_id)
            executed.add(node_id)
    return context, executed, skipped_nodes


async def _schedule_resume(
    run_id: str,
    workflow_id: str,
    resume_at: datetime,
    node_id: str,
    error_policy: str,
) -> None:
    from workflow_service.engine.temporal_client import get_temporal_client
    try:
        client = await get_temporal_client()
        await client.start_workflow(
            "WaitResumeWorkflow",
            args=[run_id, workflow_id, resume_at.isoformat(), node_id, error_policy],
            id=f"wait-resume-{run_id}",
            task_queue="light",
        )
        logger.info("[%s] resume scheduled via Temporal at %s", run_id[:8], resume_at.isoformat())
    except Exception as e:
        logger.error(
            "[%s] failed to schedule resume via Temporal — run will stay PAUSED: %s", run_id[:8], e
        )


class DagExecutor:
    """Executes a workflow run as a DAG using Kahn's algorithm."""

    async def execute(self, run_id: str) -> None:
        run = await _run_repo.get_by_id(run_id)
        if not run:
            logger.error("Run %s not found", run_id)
            return

        workflow_id = str(run["workflowId"])
        is_resume = run.get("status") == "PAUSED"

        workflow = await _workflow_repo.get_by_id(workflow_id)
        workspace_id = str(workflow["workspaceId"]) if workflow else ""

        # Look up workspace owner once — injected into CONNECTOR nodes as userId
        workspace_owner_id = ""
        if workspace_id:
            try:
                pg = get_pg()
                async with pg.acquire() as conn:
                    owner_row = await conn.fetchrow(get_workspace_owner_query(), workspace_id)
                if owner_row:
                    workspace_owner_id = str(owner_row["id"])
            except Exception as _e:
                logger.warning("Could not resolve workspace owner for %s: %s", workspace_id, _e)

        now = _utcnow()
        run_start = now
        # On resume, startedAt is already set from the original run — don't overwrite it.
        if is_resume:
            await _run_repo.update_status(run_id, "RUNNING")
        else:
            await _run_repo.update_status(run_id, "RUNNING", started_at=now)

        nodes = await _workflow_repo.get_nodes(workflow_id)
        edges = await _workflow_repo.get_edges(workflow_id)

        run_tag = run_id[:8]
        logger.info(
            "[%s] ── START  workflow=%s  workspace=%s  nodes=%d",
            run_tag,
            workflow_id[:8],
            workspace_id[:8] if workspace_id else "?",
            len(nodes),
        )

        if not nodes:
            logger.info(
                "[%s] ── RUN DONE  status=COMPLETED nodes=0 (no nodes defined)", run_tag
            )
            await _run_repo.update_status(
                run_id, "COMPLETED", completed_at=_utcnow()
            )
            return

        # Build graph structures — read-only after this point; each run copies in_degree
        node_map: dict[str, dict] = {n["nodeId"]: n for n in nodes}
        adjacency: dict[str, list[dict]] = defaultdict(
            list
        )  # sourceNodeId → [edge dicts]
        base_in_degree: dict[str, int] = {n["nodeId"]: 0 for n in nodes}

        for edge in edges:
            src = edge["sourceNodeId"]
            tgt = edge["targetNodeId"]
            adjacency[src].append(edge)
            if tgt in base_in_degree:
                base_in_degree[tgt] += 1

        # Per-run mutable copy of in_degree
        in_degree = dict(base_in_degree)
        # Tracks how many non-skipped predecessors have signalled each node.
        # A node whose in_degree reaches 0 with active_pred == 0 was only reached
        # via skipped paths and should itself be skipped (cascading branch skip).
        active_pred: dict[str, int] = {n["nodeId"]: 0 for n in nodes}

        # Seed context with run inputData so IdentityTriggerExecutor can read it
        _input = run.get("inputData") or {}
        if isinstance(_input, str):
            try:
                _input = json_mod.loads(_input)
            except (ValueError, TypeError):
                _input = {}
        if not isinstance(_input, dict):
            _input = {}
        context: dict[str, Any] = {
            "__run__": {"inputData": _input},
        }

        # Kahn's BFS seed: all nodes with in_degree == 0
        queue: deque[str] = deque(
            node_id for node_id, deg in in_degree.items() if deg == 0
        )

        skipped_nodes: set[str] = set()
        executed: set[str] = set()
        failed = False
        paused = False
        paused_resume_at: datetime | None = None

        if is_resume:
            saved_context, saved_executed, saved_skipped = await _load_resume_state(run_id)
            context.update(saved_context)
            executed.update(saved_executed)
            skipped_nodes.update(saved_skipped)
            logger.info(
                "[%s] RESUMING  prev_executed=%d  prev_skipped=%d",
                run_tag,
                len(saved_executed),
                len(saved_skipped),
            )

        while queue and not failed and not paused:
            node_id = queue.popleft()

            if node_id in skipped_nodes or node_id in executed:
                if node_id in skipped_nodes and node_id not in executed:
                    skip_step = await _run_repo.create_run_step(run_id, node_id)
                    if skip_step:
                        await _run_repo.update_run_step(
                            str(skip_step["id"]),
                            status="SKIPPED",
                            completed_at=_utcnow(),
                        )
                is_skip = node_id in skipped_nodes
                for edge in adjacency.get(node_id, []):
                    target = edge["targetNodeId"]
                    if not is_skip:
                        active_pred[target] = active_pred.get(target, 0) + 1
                    if target in in_degree:
                        in_degree[target] -= 1
                        if in_degree[target] == 0:
                            if active_pred.get(target, 0) == 0:
                                skipped_nodes.add(target)
                            queue.append(target)
                continue

            node_def = node_map.get(node_id)
            if not node_def:
                logger.warning("Node %s not found in definition", node_id)
                continue

            node_type = node_def["type"]

            # Build config from inputParameters (user-facing params)
            raw_params = node_def.get("inputParameters") or []
            if isinstance(raw_params, str):
                raw_params = json_mod.loads(raw_params)
            config_json = {
                entry["key"]: entry["value"]
                for entry in raw_params
                if isinstance(entry, dict) and "key" in entry and "value" in entry
            }

            error_policy = node_def.get("errorPolicy", "FAIL")

            # Inject workspaceId and userId for CONNECTOR nodes (from workflow, not inputParameters)
            if node_type == "CONNECTOR" and workspace_id:
                config_json.setdefault("workspaceId", workspace_id)
                if workspace_owner_id:
                    config_json.setdefault("userId", workspace_owner_id)

            # WhatsApp template SEND_MESSAGE nodes dispatch through user-backend,
            # which needs the run/node/workspace identity (not carried in params).
            if (
                node_type == "SEND_MESSAGE"
                and str(config_json.get("channel", "")).upper() == "WHATSAPP"
            ):
                if workspace_id:
                    config_json.setdefault("workspaceId", workspace_id)
                config_json.setdefault("workflowRunId", run_id)
                config_json.setdefault("nodeId", node_id)

            # Connected Gmail SEND_MESSAGE nodes also dispatch through user-backend
            # (to send via the customer's OAuth credentials). Only inject when
            # agentIntegrationId is present — platform sender (no ID) uses Resend directly.
            if (
                node_type == "SEND_MESSAGE"
                and str(config_json.get("channel", "")).upper() == "GMAIL"
                and config_json.get("agentIntegrationId")
            ):
                if workspace_id:
                    config_json.setdefault("workspaceId", workspace_id)
                config_json.setdefault("workflowRunId", run_id)
                config_json.setdefault("nodeId", node_id)

            # On resume the WAIT node already has a RUNNING step record — reuse it.
            run_step = None
            if is_resume:
                run_step = await _run_repo.get_run_step_by_node(run_id, node_id)
                if run_step and run_step.get("status") == "RUNNING" and node_type == "WAIT":
                    output = {"waited": True, "durationMs": 0}
                    await _run_repo.update_run_step(
                        str(run_step["id"]),
                        status="COMPLETED",
                        output_data=output,
                        completed_at=_utcnow(),
                    )
                    context[node_id] = {"output": output}
                    executed.add(node_id)
                    logger.info("[%s] ✓ WAIT resumed, completing node=%s", run_tag, node_id)
                    for edge in adjacency.get(node_id, []):
                        target = edge["targetNodeId"]
                        active_pred[target] = active_pred.get(target, 0) + 1
                        if target in in_degree:
                            in_degree[target] -= 1
                            if in_degree[target] == 0:
                                if active_pred.get(target, 0) == 0:
                                    skipped_nodes.add(target)
                                queue.append(target)
                    continue

            if not run_step:
                run_step = await _run_repo.create_run_step(run_id, node_id)
            if not run_step:
                logger.error("Failed to create run node record for %s", node_id)
                continue

            run_step_id = str(run_step["id"])

            resolved_config = resolve_dict(config_json, context)
            step_now = _utcnow()
            await _run_repo.update_run_step(
                run_step_id,
                status="RUNNING",
                input_data=resolved_config,
                started_at=step_now,
            )

            node_label = node_def.get("label", node_id)
            # For CONNECTOR nodes, include provider/action inline for readability
            extra = ""
            if node_type == "CONNECTOR":
                provider = resolved_config.get("provider", "")
                action = resolved_config.get("action", "")
                if provider or action:
                    extra = f"  {provider}/{action}"
            logger.info(
                '[%s] ▶ %-10s %s "%s"%s',
                run_tag,
                node_type,
                node_id,
                node_label,
                extra,
            )
            logger.info("[%s]   in:  %s", run_tag, _summary(resolved_config))
            logger.debug("[%s]   INPUT (full)\n%s", run_tag, _fmt(resolved_config))

            executor_cls = STEP_EXECUTORS.get(node_type)
            if not executor_cls:
                logger.warning("No executor for node type %s, skipping", node_type)
                await _run_repo.update_run_step(
                    run_step_id,
                    status="SKIPPED",
                    error_message=f"Unsupported node type: {node_type}",
                    completed_at=_utcnow(),
                )
                for edge in adjacency.get(node_id, []):
                    target = edge["targetNodeId"]
                    if target in in_degree:
                        in_degree[target] -= 1
                        if in_degree[target] == 0:
                            if active_pred.get(target, 0) == 0:
                                skipped_nodes.add(target)
                            queue.append(target)
                continue

            executor = executor_cls()
            retry_count = 0
            output = None
            node_error = None
            node_skipped = False

            while True:
                try:
                    output = await executor.execute(resolved_config, context)
                    node_error = None
                    break
                except PauseRequired as pause_signal:
                    await _run_repo.update_status(run_id, "PAUSED")
                    await _schedule_resume(run_id, workflow_id, pause_signal.resume_at, node_id, error_policy)
                    paused = True
                    paused_resume_at = pause_signal.resume_at
                    logger.info(
                        "[%s]   PAUSED  node=%s  resume_at=%s",
                        run_tag,
                        node_id,
                        pause_signal.resume_at.isoformat(),
                    )
                    break
                except Exception as e:
                    node_error = e
                    policy_result = evaluate_error_policy(
                        error_policy, retry_count, config_json
                    )

                    if policy_result.action == "retry":
                        retry_count += 1
                        await _run_repo.update_run_step(
                            run_step_id, retry_count=retry_count
                        )
                        logger.warning(
                            "[%s]    RETRY  attempt=%d delay=%.1fs error=%s",
                            run_tag,
                            retry_count,
                            policy_result.delay_seconds,
                            e,
                        )
                        await asyncio.sleep(policy_result.delay_seconds)
                        continue

                    elif policy_result.action == "continue":
                        output = policy_result.output or {}
                        node_skipped = True
                        logger.warning(
                            "[%s]    SKIPPED (on_error=continue)  error=%s", run_tag, e
                        )
                        await _run_repo.update_run_step(
                            run_step_id,
                            status="SKIPPED",
                            error_message=str(e),
                            output_data=output,
                            retry_count=retry_count,
                            completed_at=_utcnow(),
                        )
                        node_error = None
                        break

                    else:
                        logger.error("[%s]    FAILED  error=%s", run_tag, e)
                        await _run_repo.update_run_step(
                            run_step_id,
                            status="FAILED",
                            error_message=str(e),
                            output_data=getattr(e, "output", None),
                            retry_count=retry_count,
                            completed_at=_utcnow(),
                        )
                        await _run_repo.update_status(
                            run_id,
                            "FAILED",
                            error_message=f"Node {node_id} failed: {e}",
                            completed_at=_utcnow(),
                        )
                        failed = True
                        break

            if failed:
                break

            if node_error is None and output is not None and not node_skipped:
                logger.info("[%s]   out: %s", run_tag, _summary(output))
                logger.debug("[%s]   OUTPUT (full)\n%s", run_tag, _fmt(output))
                await _run_repo.update_run_step(
                    run_step_id,
                    status="COMPLETED",
                    output_data=output,
                    retry_count=retry_count,
                    completed_at=_utcnow(),
                )
                # Wrap in {"output": ...} so expressions like {{ $node.output.field }} resolve correctly
                context[node_id] = {"output": output}
                executed.add(node_id)
            elif node_skipped:
                # Output still available to downstream nodes even when skipped
                context[node_id] = {"output": output}
                executed.add(node_id)

            outbound = adjacency.get(node_id, [])

            # Branch routing: CONDITION nodes use `result`; branching CONNECTOR nodes use `branch`
            branch_result = None
            if node_type == "CONDITION" and output:
                branch_result = str(output.get("result", "true")).lower()
            elif node_type == "CONNECTOR" and isinstance(output, dict) and output.get("branch"):
                branch_result = str(output["branch"]).lower()

            if branch_result is not None:
                logger.info("[%s]   branch=%s", run_tag, branch_result)
                for edge in outbound:
                    target = edge["targetNodeId"]
                    source_handle = (edge.get("sourceHandle") or "").lower()
                    edge_skipped = bool(source_handle and source_handle != branch_result)
                    if edge_skipped:
                        skipped_nodes.add(target)
                        logger.info(
                            "[%s]   skip node=%s (handle='%s' != branch='%s')",
                            run_tag, target, source_handle, branch_result,
                        )
                    else:
                        active_pred[target] = active_pred.get(target, 0) + 1
                    if target in in_degree:
                        in_degree[target] -= 1
                        if in_degree[target] == 0:
                            if active_pred.get(target, 0) == 0:
                                skipped_nodes.add(target)
                            queue.append(target)
            else:
                for edge in outbound:
                    target = edge["targetNodeId"]
                    active_pred[target] = active_pred.get(target, 0) + 1
                    if target in in_degree:
                        in_degree[target] -= 1
                        if in_degree[target] == 0:
                            if active_pred.get(target, 0) == 0:
                                skipped_nodes.add(target)
                            queue.append(target)

        duration = (_utcnow() - run_start).total_seconds()
        if paused:
            logger.info(
                "[%s] ── PAUSED  resume_at=%s  nodes_done=%d  duration=%.2fs",
                run_tag,
                paused_resume_at.isoformat() if paused_resume_at else "?",
                len(executed),
                duration,
            )
        elif not failed:
            await _run_repo.update_status(
                run_id, "COMPLETED", completed_at=_utcnow()
            )
            logger.info(
                "[%s] ── DONE  status=COMPLETED  nodes=%d  duration=%.2fs",
                run_tag,
                len(executed),
                duration,
            )
        else:
            logger.error(
                "[%s] ── DONE  status=FAILED  nodes=%d  duration=%.2fs",
                run_tag,
                len(executed),
                duration,
            )


async def _fetch_workspace_owner_email(workspace_id: str) -> str | None:
    from common.db_queries import get_workspace_owner_query
    from common.infrastructure import get_pg

    try:
        pg = get_pg()
        query = get_workspace_owner_query()
        async with pg.acquire() as conn:
            row = await conn.fetchrow(query, workspace_id)
        return row["email"] if row else None
    except Exception as e:
        logger.warning("Failed to fetch workspace owner email: %s", e)
    return None


async def _send_completion_email(
    workflow_name: str,
    run_id: str,
    workspace_id: str,
    nodes_executed: int,
) -> None:
    from common.modules.email.service import EmailService
    from common.project_settings import project_settings

    s = project_settings()
    if not s.RESEND_API_KEY:
        logger.debug("RESEND_API_KEY not set, skipping completion email")
        return

    email_addr = await _fetch_workspace_owner_email(workspace_id)
    if not email_addr:
        logger.debug("No owner email found for workspace %s", workspace_id)
        return

    email_service = EmailService(s)

    subject = f"Workflow completed: {workflow_name}"
    body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 560px; margin: 0 auto; padding: 24px;">
      <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 16px 20px; margin-bottom: 20px;">
        <span style="color: #16a34a; font-size: 18px; font-weight: 600;">&#10003; Workflow Completed</span>
      </div>
      <p style="color: #374151; font-size: 15px; line-height: 1.6;">
        Your workflow <strong>{workflow_name}</strong> has finished executing successfully.
      </p>
      <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
        <tr>
          <td style="padding: 8px 12px; color: #6b7280; font-size: 13px;">Run ID</td>
          <td style="padding: 8px 12px; font-family: monospace; font-size: 13px; color: #111827;">{run_id[:12]}...</td>
        </tr>
        <tr style="background: #f9fafb;">
          <td style="padding: 8px 12px; color: #6b7280; font-size: 13px;">Nodes executed</td>
          <td style="padding: 8px 12px; font-size: 13px; color: #111827;">{nodes_executed}</td>
        </tr>
        <tr>
          <td style="padding: 8px 12px; color: #6b7280; font-size: 13px;">Status</td>
          <td style="padding: 8px 12px; font-size: 13px; color: #16a34a; font-weight: 600;">COMPLETED</td>
        </tr>
      </table>
      <p style="color: #9ca3af; font-size: 12px; margin-top: 24px;">
        — LangGraph Agent Backend Workflows
      </p>
    </div>
    """

    await email_service.send_email(email_addr, subject, body)
    logger.info("Completion email sent to %s for run %s", email_addr, run_id)
