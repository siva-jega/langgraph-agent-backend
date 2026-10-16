import asyncio
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import aioboto3

from workflow_service.core.config import app_settings
from workflow_service.engine.dag import DagExecutor
from workflow_service.modules.runs.repository import RunRepository

logger = logging.getLogger(__name__)

_worker_task: asyncio.Task | None = None
_run_repo = RunRepository()


async def _execute_run(run_id: str) -> None:
    try:
        dag = DagExecutor()
        await dag.execute(run_id)
    except Exception:
        logger.exception("Unhandled error executing run %s", run_id)
        try:
            await _run_repo.update_status(
                run_id,
                "FAILED",
                error_message="Unhandled executor error",
                completed_at=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("Failed to update run %s status to FAILED", run_id)


async def _delete_message(sqs: object, queue_url: str, receipt_handle: str) -> None:
    try:
        await sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    except Exception:
        logger.warning("Failed to delete SQS message (receipt=%s...)", receipt_handle[:20])


async def _run_loop() -> None:
    queue_url = app_settings.WORKFLOW_QUEUE_URL
    if not queue_url:
        logger.error("WORKFLOW_QUEUE_URL is not set — workflow worker cannot start")
        return

    concurrency = app_settings.WORKER_CONCURRENCY
    semaphore = asyncio.Semaphore(concurrency)
    session = aioboto3.Session()

    parsed = urlparse(queue_url)
    endpoint_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme == "http" else None

    logger.info(
        "Workflow worker started (queue=%s, concurrency=%d, region=%s%s)",
        queue_url,
        concurrency,
        app_settings.AWS_REGION,
        f", endpoint={endpoint_url}" if endpoint_url else "",
    )

    async with session.client("sqs", region_name=app_settings.AWS_REGION, endpoint_url=endpoint_url) as sqs:
        while True:
            try:
                response = await sqs.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=app_settings.SQS_MAX_MESSAGES,
                    WaitTimeSeconds=app_settings.SQS_WAIT_SECONDS,
                    VisibilityTimeout=app_settings.SQS_VISIBILITY_TIMEOUT,
                )

                messages = response.get("Messages", [])
                if not messages:
                    continue

                for msg in messages:
                    body = msg.get("Body")
                    receipt = msg.get("ReceiptHandle")

                    if not body or not receipt:
                        continue

                    try:
                        job = json.loads(body)
                    except json.JSONDecodeError:
                        logger.warning("SQS message has invalid JSON, deleting: %s", body)
                        await _delete_message(sqs, queue_url, receipt)
                        continue

                    run_id = job.get("workflowRunId")
                    if not run_id:
                        logger.warning("SQS message missing workflowRunId: %s", job)
                        await _delete_message(sqs, queue_url, receipt)
                        continue

                    logger.info("[%s] ── dequeued", run_id[:8])

                    async def _bounded(rid: str = run_id, rh: str = receipt) -> None:
                        async with semaphore:
                            await _execute_run(rid)
                        # Delete after execution completes (success or DB-recorded failure).
                        # If _execute_run raises unexpectedly, the message re-surfaces after
                        # SQS_VISIBILITY_TIMEOUT and retries until it lands in the DLQ.
                        await _delete_message(sqs, queue_url, rh)

                    asyncio.create_task(_bounded())

            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                break
            except Exception:
                logger.exception("Worker loop error, retrying in 2s")
                await asyncio.sleep(2)


def start_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_run_loop())
    logger.info("Workflow worker task created")


async def stop_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    _worker_task = None
    logger.info("Workflow worker stopped")
