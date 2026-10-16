"""Agent-specific OTel setup.

All generic utilities live in ``common.otel_utils``; this module re-exports
them and adds the agent-specific ``instrument_app`` (Milvus, OpenAI,
streaming SSE middleware) bound to the local ``app_settings``.
"""

import logging
import re
from typing import Dict

from common.otel_utils import (
    _CorrelationIdGenerator,
    _NoTraceSpanProcessor,
    _correlation_id_ctx,
    _requests_correlation_hook,
    _safe_instrument,
    _safe_json,
    _serialize_arg,
    _TRACEPARENT_RE,
    auto_trace_module_functions,
    configure_json_logging as _base_configure_json_logging,
    CorrelationIdMiddleware,
    get_correlation_id,
    install_correlation_id_propagator,
    JsonLogFormatter,
    no_trace,
    traced,
    TraceIdFilter,
)

from app.core.config import app_settings

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.milvus import MilvusInstrumentor
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import get_current_span
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_EXCLUDED_URLS_RE = r"/(?:health|ready|metrics)(?:/.*)?$|/ops/milvus-warmup/?$"


# --------------------------------------------------------------------------- #
#  Settings-bound helpers
# --------------------------------------------------------------------------- #


def is_development_environment() -> bool:
    return app_settings.is_development_environment()


def is_opentelemetry_enabled() -> bool:
    return not app_settings.OTEL_DISABLE


def _resolve_otlp_trace_endpoint() -> str | None:
    raw = app_settings.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
    if raw is None:
        return None
    s = str(raw).strip()
    if app_settings.otlp_endpoint_is_disabled(s):
        return None
    return s


def configure_json_logging(level: int = logging.INFO) -> None:
    _base_configure_json_logging(
        level,
        console=app_settings.log_format_is_console(),
        extra_noisy=["openai._base_client"],
    )


# --------------------------------------------------------------------------- #
#  Agent-specific streaming middleware
# --------------------------------------------------------------------------- #

_RE_MULTI_SPACE = re.compile(r" {2,}")
_RE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%)°/])")
_RE_AFTER_OPENING = re.compile(r"([\[(/])\s+")
_RE_BEFORE_CLOSE = re.compile(r"\s+([\]])")


def tidy(text: str) -> str:
    lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        ln = _RE_MULTI_SPACE.sub(" ", ln)
        ln = _RE_BEFORE_PUNCT.sub(r"\1", ln)
        ln = _RE_AFTER_OPENING.sub(r"\1", ln)
        ln = _RE_BEFORE_CLOSE.sub(r"\1", ln)
        lines.append(ln)
    return "\n".join(lines).strip()


class StreamResponseSpanMiddleware(BaseHTTPMiddleware):
    """Accumulate SSE events from streaming responses into the active span."""

    MAX_ACCUMULATED_BYTES = 64 * 1024

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        span = trace.get_current_span()

        if not (hasattr(response, "body_iterator") and span and span.is_recording()):
            return response

        original_iter = response.body_iterator
        buckets: Dict[str, list[str]] = {
            "end_of_chat": [],
            "chat_message": [],
            "tool_message": [],
        }

        async def wrapped_iter():
            buffer = b""
            try:
                async for chunk in original_iter:
                    buffer += chunk
                    if len(buffer) > self.MAX_ACCUMULATED_BYTES:
                        buffer = buffer[-self.MAX_ACCUMULATED_BYTES :]

                    while b"\n\n" in buffer:
                        raw_event, buffer = buffer.split(b"\n\n", 1)
                        text = raw_event.decode("utf-8", errors="ignore")
                        event_type = None
                        data_lines = []
                        for line in text.splitlines():
                            if line.startswith("event:"):
                                event_type = line.split("event:", 1)[1].strip()
                            elif line.startswith("data:"):
                                data_lines.append(line.split("data:", 1)[1])
                        payload = "\n".join(data_lines)
                        if event_type in buckets:
                            buckets[event_type].append(payload)
                    yield chunk
            finally:
                for ev_name, fragments in buckets.items():
                    if fragments:
                        joined = tidy("".join(fragments).replace("\\n", "\n"))
                        span.set_attribute(f"response.{ev_name}", joined)

        response.body_iterator = wrapped_iter()
        return response


# --------------------------------------------------------------------------- #
#  App instrumentation
# --------------------------------------------------------------------------- #


def _patch_openai_streaming_service_tier_capture() -> None:
    """Capture ``service_tier`` from OpenAI streaming chunks into OTel spans.

    ``opentelemetry-instrumentation-openai`` accumulates stream chunks in
    ``_accumulate_stream_items`` but only copies model/id/usage/choices.
    OpenAI emits the authoritative ``service_tier`` (priority vs default) on
    late stream chunks; without this patch ``openai.response.service_tier`` is
    missing for streamed chat completions.
    """
    from functools import wraps

    from opentelemetry.instrumentation.openai.shared import chat_wrappers
    from opentelemetry.instrumentation.openai.shared import model_as_dict
    from opentelemetry.instrumentation.openai.utils import is_openai_v1

    original = chat_wrappers._accumulate_stream_items
    if getattr(original, "_service_tier_patched", False):
        return

    @wraps(original)
    def _accumulate_with_service_tier(item, complete_response):
        original(item, complete_response)
        chunk = model_as_dict(item) if is_openai_v1() else item
        if not isinstance(chunk, dict):
            return
        tier = chunk.get("service_tier")
        if tier:
            complete_response["service_tier"] = tier

    _accumulate_with_service_tier._service_tier_patched = True
    chat_wrappers._accumulate_stream_items = _accumulate_with_service_tier


def instrument_app(app, service_name: str | None = None):
    resolved_service = service_name or app_settings.otel_resource_service_name()
    # Adopt a frontend-sent X-Correlation-ID as the root trace id (see
    # common.otel_utils): keeps correlation_id == trace_id and gives the trace a
    # real root span instead of a phantom browser parent.
    install_correlation_id_propagator()
    if not is_opentelemetry_enabled():
        resource = Resource(attributes={"service.name": resolved_service})
        trace.set_tracer_provider(
            TracerProvider(resource=resource, id_generator=_CorrelationIdGenerator())
        )
        app.add_middleware(CorrelationIdMiddleware)
        logger.info(
            "OpenTelemetry OTLP + auto-instrumentation disabled; plain logs + correlation middleware"
        )
        return

    otlp_endpoint = _resolve_otlp_trace_endpoint()

    resource = Resource(attributes={"service.name": resolved_service})
    provider = TracerProvider(
        resource=resource, id_generator=_CorrelationIdGenerator()
    )
    if otlp_endpoint:
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(
            _NoTraceSpanProcessor(BatchSpanProcessor(otlp_exporter))
        )
    trace.set_tracer_provider(provider)

    # excluded_urls is matched against the full URL (http://host/path), not path alone.
    _safe_instrument(FastAPIInstrumentor).instrument_app(
        app,
        excluded_urls=_EXCLUDED_URLS_RE,
        exclude_spans=["receive", "send"],
    )
    app.add_middleware(StreamResponseSpanMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    _safe_instrument(RequestsInstrumentor, request_hook=_requests_correlation_hook)
    _safe_instrument(HTTPXClientInstrumentor)
    _safe_instrument(MilvusInstrumentor)
    _patch_openai_streaming_service_tier_capture()
    _safe_instrument(OpenAIInstrumentor)
    _safe_instrument(AsyncPGInstrumentor)

    if otlp_endpoint:
        logger.info(
            "OpenTelemetry instrumentation done (traces → OTLP at %s)", otlp_endpoint
        )
    else:
        logger.info("OpenTelemetry instrumentation setup done (no OTLP endpoint set)")
