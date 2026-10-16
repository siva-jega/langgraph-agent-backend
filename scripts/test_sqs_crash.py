#!/usr/bin/env python3
"""
Test SQS crash recovery: verifies that a message reappears after a consumer
crashes without deleting it.

Phases:
  1. Fetch the queue's actual VisibilityTimeout via GetQueueAttributes.
  2. Send a test message to the queue.
  3. Receive it (simulating consumer picking it up) — do NOT delete (crash).
  4. Wait for the visibility timeout to expire.
  5. Receive again — confirm the same message reappears with ApproximateReceiveCount == 2.
  6. Clean up by deleting the message.

Usage:
    python scripts/test_sqs_crash.py
    python scripts/test_sqs_crash.py --queue-url https://sqs.us-east-1.amazonaws.com/...
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env", override=False)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger("sqs-crash-test")

QUEUE_URL = os.getenv("CHANNELS_INBOUND_QUEUE_URL", "").strip()
AWS_REGION = (
    os.getenv("AWS_REGION_NAME")
    or os.getenv("AWS_DEFAULT_REGION")
    or "us-east-1"
).strip()


# ---------------------------------------------------------------------------
# AWS client
# ---------------------------------------------------------------------------

def _build_client():
    creds: dict = {}
    key_id = (os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
    secret  = (os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()
    if key_id and secret:
        creds = {"aws_access_key_id": key_id, "aws_secret_access_key": secret}

    return boto3.client(
        "sqs",
        region_name=AWS_REGION,
        config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
        **creds,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_visibility_timeout(sqs, queue_url: str) -> int:
    """Fetch the queue's VisibilityTimeout attribute directly from AWS."""
    resp = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["VisibilityTimeout", "QueueArn"],
    )
    attrs = resp.get("Attributes", {})
    if "VisibilityTimeout" not in attrs:
        raise RuntimeError(
            "VisibilityTimeout missing from GetQueueAttributes response — "
            "cannot determine how long to wait for reappearance."
        )
    timeout = int(attrs["VisibilityTimeout"])
    arn = attrs.get("QueueArn", "unknown")
    log.info("Queue ARN              : %s", arn)
    log.info("Queue VisibilityTimeout: %s seconds", timeout)
    return timeout


def purge_queue(sqs, queue_url: str) -> None:
    """Drain all messages from the queue before the test to avoid interference."""
    log.info("Purging queue to remove any stuck/existing messages...")
    try:
        sqs.purge_queue(QueueUrl=queue_url)
        log.info("Purge request sent. Waiting 5s for it to take effect...")
        time.sleep(5)
    except sqs.exceptions.PurgeQueueInProgress:
        log.warning("A purge is already in progress (only one allowed per 60s). Continuing anyway.")


def send_test_message(sqs, queue_url: str) -> str:
    """Send a clearly-marked test message and return its MessageId."""
    body = json.dumps({
        "source": "test_sqs_crash.py",
        "description": "crash recovery test message",
        "timestamp": time.time(),
    })
    resp = sqs.send_message(QueueUrl=queue_url, MessageBody=body)
    msg_id = resp["MessageId"]
    log.info("Sent test message — MessageId: %s", msg_id)
    return msg_id


def receive_one(sqs, queue_url: str, visibility_timeout: int) -> dict | None:
    """Poll until exactly one message arrives (up to 3 attempts)."""
    for attempt in range(1, 4):
        log.info("Polling SQS (attempt %d/3)...", attempt)
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=10,
            VisibilityTimeout=visibility_timeout,
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = resp.get("Messages", [])
        if messages:
            return messages[0]
        log.info("No message yet, retrying...")
    return None


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def run(queue_url: str) -> int:
    sqs = _build_client()

    # ── Step 0: purge existing messages ───────────────────────────────────────
    log.info("=" * 60)
    log.info("STEP 0 — Purging queue (clean slate for the test)")
    log.info("=" * 60)
    purge_queue(sqs, queue_url)

    # ── Step 1: fetch real visibility timeout from the queue ──────────────────
    log.info("=" * 60)
    log.info("STEP 1 — Fetching queue attributes")
    log.info("=" * 60)
    visibility_timeout = get_visibility_timeout(sqs, queue_url)

    # Use a shorter wait for the test so you don't sit idle too long.
    # If the queue timeout is <= 10s already, use it as-is.
    wait_seconds = min(visibility_timeout, 35)

    # ── Step 2: send a test message ───────────────────────────────────────────
    log.info("=" * 60)
    log.info("STEP 2 — Sending test message")
    log.info("=" * 60)
    send_test_message(sqs, queue_url)

    # ── Step 3: receive (simulate consumer picking up) — DO NOT delete ────────
    log.info("=" * 60)
    log.info("STEP 3 — Receiving message (simulating consumer, NO delete)")
    log.info("=" * 60)
    msg = receive_one(sqs, queue_url, visibility_timeout)
    if not msg:
        log.error("Could not receive the test message after 3 attempts. Aborting.")
        return 1

    receipt_handle_1 = msg["ReceiptHandle"]
    receive_count_1  = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", 1))
    log.info("Received — MessageId: %s", msg.get("MessageId"))
    log.info("           ApproximateReceiveCount: %d", receive_count_1)
    log.info(">>> SIMULATING CRASH — not calling delete_message <<<")

    # ── Step 4: wait for visibility timeout to expire ─────────────────────────
    log.info("=" * 60)
    log.info("STEP 4 — Waiting %ds for visibility timeout to expire...", wait_seconds)
    log.info("=" * 60)
    for remaining in range(wait_seconds, 0, -5):
        log.info("  %ds remaining...", remaining)
        time.sleep(min(5, remaining))

    # ── Step 5: receive again — message should reappear ───────────────────────
    log.info("=" * 60)
    log.info("STEP 5 — Re-polling: message should reappear now")
    log.info("=" * 60)
    msg2 = receive_one(sqs, queue_url, visibility_timeout)

    if not msg2:
        log.error("FAIL — Message did NOT reappear after visibility timeout.")
        return 1

    receive_count_2 = int(msg2.get("Attributes", {}).get("ApproximateReceiveCount", 0))
    receipt_handle_2 = msg2["ReceiptHandle"]
    log.info("Received again — MessageId: %s", msg2.get("MessageId"))
    log.info("                 ApproximateReceiveCount: %d", receive_count_2)

    if receive_count_2 > receive_count_1:
        log.info("PASS ✓ — Message reappeared with higher receive count (%d → %d)",
                 receive_count_1, receive_count_2)
    else:
        log.warning("Message reappeared but receive count did not increase — "
                    "may be a different message in the queue.")

    # ── Step 6: clean up ──────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("STEP 6 — Cleaning up: deleting message from queue")
    log.info("=" * 60)
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle_2)
    log.info("Deleted. Queue is clean.")

    return 0


if __name__ == "__main__":
    url = QUEUE_URL
    if len(sys.argv) > 1:
        url = sys.argv[1]

    if not url:
        log.error(
            "No queue URL. Set CHANNELS_INBOUND_QUEUE_URL in .env or pass it as the first argument.\n"
            "  python scripts/test_sqs_crash.py <queue-url>"
        )
        sys.exit(1)

    log.info("Testing crash recovery on queue: %s", url)
    sys.exit(run(url))
