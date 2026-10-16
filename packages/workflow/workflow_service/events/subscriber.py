import asyncio
import json
import logging

import redis.asyncio as aioredis

from common.infrastructure.redis import get_redis
from workflow_service.core.config import app_settings
from workflow_service.events.trigger_evaluator import TriggerFilterEvaluator
from workflow_service.modules.nodes.repository import WorkflowNodeRepository
from workflow_service.modules.runs.repository import RunRepository

logger = logging.getLogger(__name__)

_subscriber_task: asyncio.Task | None = None

CONVERSATION_EVENTS_CHANNEL = "conversation_events"

_node_repo = WorkflowNodeRepository()
_run_repo = RunRepository()
_evaluator = TriggerFilterEvaluator()


async def _handle_conversation_event(event: dict) -> None:
    """Query active CONVERSATION_EVENT trigger nodes, evaluate filters, enqueue matching runs."""
    event_workspace = str(event.get("workspaceId", ""))

    nodes = await _node_repo.get_all_active_conversation_event_triggers()

    for node in nodes:
        node_workspace = str(node.get("workspaceId", ""))
        if node_workspace != event_workspace:
            continue

        # agentId/channel are user-configured and stored in inputParameters
        raw_params = node.get("inputParameters") or []
        if isinstance(raw_params, str):
            import json as _json
            try:
                raw_params = _json.loads(raw_params)
            except (ValueError, TypeError):
                raw_params = []
        filter_config = {
            entry["key"]: entry["value"]
            for entry in raw_params
            if isinstance(entry, dict) and "key" in entry and "value" in entry
        }
        if not _evaluator.evaluate(filter_config, event):
            continue

        workflow_id = str(node["workflowId"])
        trigger_node_id = str(node["id"])

        run = await _run_repo.create(
            workflow_id=workflow_id,
            trigger_node_id=trigger_node_id,
            input_data=event,
            correlation_id=event.get("conversationId"),
        )
        if not run:
            logger.error("Failed to create run for trigger node %s", trigger_node_id)
            continue

        run_id = str(run["id"])

        try:
            redis = get_redis()
            await redis.lpush(
                app_settings.WORKFLOW_QUEUE_KEY,
                json.dumps({
                    "workflowRunId": run_id,
                    "workflowId": workflow_id,
                }),
            )
            logger.info(
                "Enqueued workflow run %s for trigger node %s (conversation %s)",
                run_id,
                trigger_node_id,
                event.get("conversationId"),
            )
        except Exception:
            logger.exception("Failed to enqueue run %s", run_id)


async def _subscribe_loop() -> None:
    """Redis Pub/Sub subscriber for conversation events."""
    logger.info("Conversation event subscriber starting")

    if not app_settings.REDIS_URI:
        logger.warning("Redis not available, subscriber not started")
        return

    # Dedicated connection for Pub/Sub (no socket_timeout — must block indefinitely)
    sub_redis = aioredis.from_url(
        app_settings.REDIS_URI,
        decode_responses=True,
        socket_connect_timeout=10,
    )
    pubsub = sub_redis.pubsub()
    await pubsub.subscribe(CONVERSATION_EVENTS_CHANNEL)

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                event = json.loads(message["data"])
                await _handle_conversation_event(event)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON in conversation event")
            except Exception:
                logger.exception("Error handling conversation event")
    except asyncio.CancelledError:
        logger.info("Subscriber loop cancelled")
    finally:
        await pubsub.unsubscribe(CONVERSATION_EVENTS_CHANNEL)
        await pubsub.aclose()
        await sub_redis.aclose()


def start_subscriber() -> None:
    global _subscriber_task
    if _subscriber_task and not _subscriber_task.done():
        return
    _subscriber_task = asyncio.create_task(_subscribe_loop())
    logger.info("Conversation event subscriber task created")


async def stop_subscriber() -> None:
    global _subscriber_task
    if _subscriber_task and not _subscriber_task.done():
        _subscriber_task.cancel()
        try:
            await _subscriber_task
        except asyncio.CancelledError:
            pass
    _subscriber_task = None
    logger.info("Conversation event subscriber stopped")
