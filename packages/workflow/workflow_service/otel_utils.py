"""Workflow-specific OTel setup.

All generic utilities live in ``common.otel_utils``; this module re-exports
them and adds the workflow-specific ``instrument_app`` (HTTPX instrumentation)
bound to the local ``app_settings``.
"""

import logging

from common.otel_utils import (
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
    JsonLogFormatter,
    no_trace,
    traced,
    TraceIdFilter,
)

from workflow_service.core.config import app_settings

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)


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
    )


# --------------------------------------------------------------------------- #
#  Workflow-specific hooks
# --------------------------------------------------------------------------- #


def _httpx_request_hook(span, request):
    """Inject ``X-Correlation-ID`` into outbound HTTPX calls."""
    corr_id = get_correlation_id()
    if corr_id:
        request.headers["X-Correlation-ID"] = corr_id


# --------------------------------------------------------------------------- #
#  App instrumentation
# --------------------------------------------------------------------------- #


def instrument_app(app, service_name: str | None = None):
    resolved_service = service_name or app_settings.otel_resource_service_name()
    if not is_opentelemetry_enabled():
        resource = Resource(attributes={"service.name": resolved_service})
        trace.set_tracer_provider(TracerProvider(resource=resource))
        app.add_middleware(CorrelationIdMiddleware)
        logger.info(
            "OpenTelemetry OTLP + auto-instrumentation disabled; plain logs + correlation middleware"
        )
        return

    otlp_endpoint = _resolve_otlp_trace_endpoint()

    resource = Resource(attributes={"service.name": resolved_service})
    provider = TracerProvider(resource=resource)
    if otlp_endpoint:
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(
            _NoTraceSpanProcessor(BatchSpanProcessor(otlp_exporter))
        )
    trace.set_tracer_provider(provider)

    _safe_instrument(FastAPIInstrumentor).instrument_app(
        app,
        excluded_urls="/health/?$",
        exclude_spans=["receive", "send"],
    )
    app.add_middleware(CorrelationIdMiddleware)
    _safe_instrument(RequestsInstrumentor, request_hook=_requests_correlation_hook)
    _safe_instrument(HTTPXClientInstrumentor, request_hook=_httpx_request_hook)
    _safe_instrument(AsyncPGInstrumentor)

    if otlp_endpoint:
        logger.info(
            "OpenTelemetry instrumentation done (traces -> OTLP at %s)", otlp_endpoint
        )
    else:
        logger.info("OpenTelemetry instrumentation setup done (no OTLP endpoint set)")
