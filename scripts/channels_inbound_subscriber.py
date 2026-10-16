#!/usr/bin/env python3
"""Standalone SQS subscriber for channels inbound queue."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Receive and log channel inbound payloads from SQS."
    )
    parser.add_argument(
        "--queue-url",
        default=os.getenv("CHANNELS_INBOUND_QUEUE_URL", "").strip(),
        help="SQS queue URL. Default: CHANNELS_INBOUND_QUEUE_URL",
    )
    parser.add_argument(
        "--region",
        default=(
            os.getenv("CHANNELS_AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or "us-west-2"
        ).strip(),
        help="AWS region. Default: CHANNELS_AWS_REGION or AWS_DEFAULT_REGION",
    )
    parser.add_argument(
        "--wait-time-seconds",
        type=int,
        default=20,
        help="SQS long poll wait time. Default: 20",
    )
    parser.add_argument(
        "--visibility-timeout",
        type=int,
        default=30,
        help="SQS visibility timeout in seconds. Default: 30",
    )
    parser.add_argument(
        "--max-number-of-messages",
        type=int,
        default=1,
        help="Messages per poll. Default: 1",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete message after successful log.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Exit after first poll cycle.",
    )
    return parser.parse_args()


def resolve_aws_credentials() -> dict[str, str]:
    access_key = (
        os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY") or ""
    ).strip()
    secret_key = (
        os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("AWS_KEY_SECRET") or ""
    ).strip()
    session_token = (os.getenv("AWS_SESSION_TOKEN") or "").strip()

    creds: dict[str, str] = {}
    if access_key and secret_key:
        creds["aws_access_key_id"] = access_key
        creds["aws_secret_access_key"] = secret_key
    if session_token:
        creds["aws_session_token"] = session_token
    return creds


def build_sqs_client(region: str):
    return boto3.client(
        "sqs",
        region_name=region,
        config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
        **resolve_aws_credentials(),
    )


def pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def main() -> int:
    load_dotenv(ROOT_DIR / ".env", override=False)
    configure_logging()
    args = parse_args()

    if not args.queue_url:
        logging.error(
            "Missing queue URL. Set CHANNELS_INBOUND_QUEUE_URL or pass --queue-url."
        )
        return 1

    sqs = build_sqs_client(args.region)
    stop = False

    def handle_signal(signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True
        logging.info("Signal received. stop=%s", signum)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logging.info(
        "Listening SQS queue=%s region=%s delete=%s once=%s",
        args.queue_url,
        args.region,
        args.delete,
        args.once,
    )

    while not stop:
        response = sqs.receive_message(
            QueueUrl=args.queue_url,
            MaxNumberOfMessages=args.max_number_of_messages,
            WaitTimeSeconds=args.wait_time_seconds,
            VisibilityTimeout=args.visibility_timeout,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )
        messages = response.get("Messages", [])

        if not messages:
            logging.info("No messages received.")
            if args.once:
                break
            continue

        for message in messages:
            receipt_handle = message["ReceiptHandle"]
            raw_body = message.get("Body", "")
            logging.info("Received raw SQS message:\n%s", pretty_json(message))

            try:
                envelope = json.loads(raw_body)
            except json.JSONDecodeError:
                logging.warning("Body not JSON. body=%s", raw_body)
                envelope = {"rawBody": raw_body}

            logging.info("Received channel envelope:\n%s", pretty_json(envelope))

            payload = envelope.get("payload") if isinstance(envelope, dict) else None
            if payload is not None:
                logging.info("Received nested payload:\n%s", pretty_json(payload))

            if args.delete:
                sqs.delete_message(
                    QueueUrl=args.queue_url,
                    ReceiptHandle=receipt_handle,
                )
                logging.info("Deleted message from queue.")
            else:
                logging.info("Message kept in queue. Reappears after visibility timeout.")

        if args.once:
            break

    logging.info("Subscriber stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
