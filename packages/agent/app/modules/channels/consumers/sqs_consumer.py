import asyncio
import json
import logging
from typing import Optional
import aioboto3
from opentelemetry import trace
from opentelemetry.propagate import extract
from app.core.config import app_settings
from common.otel_utils import _correlation_id_ctx

logger = logging.getLogger(__name__)

_run_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None
_tracer = trace.get_tracer("channels.sqs.consumer")

#: Time allowed for workers to finish buffered/in-flight messages on shutdown.
#: Kept under the pod's terminationGracePeriodSeconds (30s) to leave margin.
_DRAIN_TIMEOUT = 25.0


async def _process_one(sqs_client, queue_url: str, msg: dict) -> None:
    """Process a single SQS message and delete it on success."""
    from app.modules.channels.service import process_inbound_message

    msg_id = msg.get("MessageId")

    try:
        data = json.loads(msg.get("Body", "{}"))
    except (ValueError, TypeError):
        logger.exception("Invalid SQS message body (MessageId: %s)", msg_id)
        return

    # Continue the originating trace if the producer (user-backend) injected
    # W3C context, so this span shares the upstream trace_id and the logged
    # correlation_id matches end-to-end. Falls back to a new root span when no
    # context is present.
    parent_ctx = extract(data)

    with _tracer.start_as_current_span(
        "sqs.process_inbound_message", context=parent_ctx
    ) as span:
        try:
            # Back-compat: an explicit traceId still seeds the correlation id
            # for older producers that don't propagate W3C traceparent.
            trace_id = data.get("traceId")
            if trace_id:
                _correlation_id_ctx.set(trace_id)

            span.set_attribute("channel", data.get("channel", "unknown"))
            span.set_attribute("source", data.get("source", "unknown"))
            span.set_attribute("message.id", msg_id or "")

            await process_inbound_message(data)
            await sqs_client.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=msg["ReceiptHandle"],
            )
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.StatusCode.ERROR, str(exc))
            logger.exception("Failed to process SQS message (MessageId: %s)", msg_id)


async def _poll_loop(poller_id: int, sqs_client, queue: asyncio.Queue) -> None:
    """Producer: long-poll SQS and hand each message to the worker queue.

    Putting onto a bounded queue applies back-pressure: when all workers are
    busy the put blocks, so we stop pulling messages that would otherwise sit
    invisible in SQS past their visibility timeout.
    """
    s = app_settings
    logger.info("Channels poller-%d started (batch_size=%d)", poller_id, s.CHANNELS_SQS_BATCH_SIZE)
    try:
        while True:
            try:
                response = await sqs_client.receive_message(
                    QueueUrl=s.CHANNELS_INBOUND_QUEUE_URL,
                    MaxNumberOfMessages=s.CHANNELS_SQS_BATCH_SIZE,
                    WaitTimeSeconds=20,
                    AttributeNames=["ApproximateReceiveCount"],
                )
                for msg in response.get("Messages", []):
                    await queue.put(msg)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Poller-%d error polling SQS. Retrying in 5 seconds...", poller_id)
                await asyncio.sleep(5.0)
    except asyncio.CancelledError:
        logger.info("Channels poller-%d cancelled", poller_id)
        raise


async def _worker_loop(worker_id: int, sqs_client, queue: asyncio.Queue) -> None:
    """Consumer: pull one message at a time and process it independently.

    A slow message ties up only this single worker; the rest of the pool keeps
    draining the queue, eliminating the per-batch head-of-line blocking.
    """
    logger.info("Channels worker-%d started", worker_id)
    s = app_settings
    try:
        while True:
            msg = await queue.get()
            try:
                await _process_one(sqs_client, s.CHANNELS_INBOUND_QUEUE_URL, msg)
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        logger.info("Channels worker-%d cancelled", worker_id)
        raise


async def _run(stop_event: asyncio.Event) -> None:
    """Own the poller(s) + worker pool for the lifetime of the consumer."""
    s = app_settings
    if not s.CHANNELS_INBOUND_QUEUE_URL:
        logger.info("Channels SQS consumer disabled (no CHANNELS_INBOUND_QUEUE_URL)")
        return

    n_pollers = max(1, s.CHANNELS_SQS_POLL_CONCURRENCY)
    n_workers = max(1, s.CHANNELS_SQS_WORKERS)
    # Small bounded buffer: large enough to keep workers fed, small enough that
    # few messages are stranded (and redelivered after visibility timeout) if a
    # drain times out on shutdown.
    queue: asyncio.Queue = asyncio.Queue(maxsize=n_workers)

    logger.info(
        "Channels consumer starting on SQS %s (pollers=%d, workers=%d)",
        s.CHANNELS_INBOUND_QUEUE_URL,
        n_pollers,
        n_workers,
    )

    session = aioboto3.Session(
        region_name=s.AWS_REGION_NAME,
        aws_access_key_id=s.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
    )

    try:
        async with session.client("sqs") as sqs_client:
            pollers = [
                asyncio.create_task(_poll_loop(i, sqs_client, queue))
                for i in range(1, n_pollers + 1)
            ]
            workers = [
                asyncio.create_task(_worker_loop(i, sqs_client, queue))
                for i in range(1, n_workers + 1)
            ]

            # Run until shutdown is requested.
            await stop_event.wait()

            # Graceful drain, bounded by terminationGracePeriodSeconds:
            # 1. Stop fetching new work.
            for p in pollers:
                p.cancel()
            await asyncio.gather(*pollers, return_exceptions=True)

            # 2. Let workers finish what's already buffered + in flight.
            try:
                await asyncio.wait_for(queue.join(), timeout=_DRAIN_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning(
                    "Channels consumer drain timed out; %d buffered message(s) will be redelivered",
                    queue.qsize(),
                )

            # 3. Stop workers.
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Channels consumer crashed")
    finally:
        logger.info("Channels consumer stopped")


def start_consumer() -> None:
    """Start the SQS consumer (poller(s) + worker pool) as a background task."""
    global _run_task, _stop_event
    if _run_task and not _run_task.done():
        logger.warning("Channels consumer already running")
        return
    _stop_event = asyncio.Event()
    _run_task = asyncio.create_task(_run(_stop_event))
    logger.info(
        "Channels consumer started (pollers=%d, workers=%d)",
        app_settings.CHANNELS_SQS_POLL_CONCURRENCY,
        app_settings.CHANNELS_SQS_WORKERS,
    )


async def stop_consumer() -> None:
    """Signal a graceful drain and wait for the consumer to finish."""
    global _run_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _run_task is not None:
        await asyncio.gather(_run_task, return_exceptions=True)
    _run_task = None
    _stop_event = None
    logger.info("Channels consumer stopped")


def start_subscriber() -> None:
    """Backward-compatible alias for the previous transport API."""
    start_consumer()


async def stop_subscriber() -> None:
    """Backward-compatible alias for the previous transport API."""
    await stop_consumer()
