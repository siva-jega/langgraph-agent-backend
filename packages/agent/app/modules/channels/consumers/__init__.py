"""Inbound channel consumers."""

from app.modules.channels.consumers.sqs_consumer import start_consumer, stop_consumer

__all__ = ["start_consumer", "stop_consumer"]
