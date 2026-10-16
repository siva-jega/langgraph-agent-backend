import inspect
import json
import logging
import re
import uuid
from contextvars import ContextVar
from functools import wraps
from typing import Any, Dict

from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from opentelemetry import context as _otel_context
from opentelemetry import trace
from opentelemetry.propagate import get_global_textmap, set_global_textmap
from opentelemetry.propagators.textmap import (
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import get_current_span


# --------------------------------------------------------------------------- #
#  (De-)serialisation helpers
# --------------------------------------------------------------------------- #


def _serialize_arg(val: Any) -> Any:
    if isinstance(val, BaseModel):
        return val.model_dump()
    if isinstance(val, dict):
        return val
    if isinstance(val, (str, int, float, bool)) or val is None:
        return val
    return str(val)


def _safe_json(obj: Any, max_len: int = 2048) -> str:
    raw = json.dumps(obj, default=str)
    return raw if len(raw) <= max_len else raw[:max_len] + "…(truncated)"


# --------------------------------------------------------------------------- #
#  Correlation ID
# --------------------------------------------------------------------------- #

_correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    """Return the correlation ID for the current async context.

    Resolution order (first non-empty wins):
    1. Explicit ``X-Correlation-ID`` stored by CorrelationIdMiddleware.
    2. Active OpenTelemetry trace ID.
    3. New UUID4 (generated once and cached for the lifetime of this context).
    """
    corr_id = _correlation_id_ctx.get()
    if corr_id:
        return corr_id

    span = get_current_span()
    if span and span.is_recording():
        ctx = span.get_span_context()
        if ctx.is_valid:
            corr_id = f"{ctx.trace_id:032x}"
            _correlation_id_ctx.set(corr_id)
            return corr_id

    corr_id = uuid.uuid4().hex
    _correlation_id_ctx.set(corr_id)
    return corr_id


# --------------------------------------------------------------------------- #
#  Logging helpers
# --------------------------------------------------------------------------- #


class TraceIdFilter(logging.Filter):
    def filter(self, record):
        span = get_current_span()
        if span and span.is_recording():
            ctx = span.get_span_context()
            record.trace_id = f"{ctx.trace_id:032x}"
            record.span_id = f"{ctx.span_id:016x}"
        else:
            record.trace_id = ""
            record.span_id = ""
        record.correlation_id = get_correlation_id()
        return True


class JsonLogFormatter(logging.Formatter):
    """Single-line JSON formatter for Loki/Grafana consumption."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        log_record: dict = {
            "timestamp": str(int(record.created * 1_000_000_000)),
            "severity": record.levelname,
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", ""),
            "trace_id": getattr(record, "trace_id", ""),
            "span_id": getattr(record, "span_id", ""),
            "message": message,
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            log_record["exception"] = record.exc_text
        return json.dumps(log_record, default=str)


def configure_json_logging(
    level: int = logging.INFO,
    console: bool = False,
    extra_noisy: list[str] | None = None,
) -> None:
    """Install structured logging on the root logger.

    Defaults to single-line JSON (Loki/Grafana/Datadog-friendly) with trace IDs
    so any deployment is structured by default. Pass ``console=True`` only for
    human-readable local output. ``extra_noisy`` adds additional loggers to
    silence at WARNING level.
    """
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, logging.StreamHandler)]

    handler = logging.StreamHandler()
    if console:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s [%(correlation_id)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    else:
        handler.setFormatter(JsonLogFormatter())
    handler.addFilter(TraceIdFilter())
    root.addHandler(handler)
    root.setLevel(level)

    for noisy in ["uvicorn.access", "httpx", "httpcore"] + (extra_noisy or []):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Tracing decorator
# --------------------------------------------------------------------------- #


def traced(fn):
    """Decorator that opens a span named ``<module>.<function>`` and records
    truncated JSON of arguments and the return value.
    Works for sync, async and async-generator callables.
    """

    def _get_tracer():
        return trace.get_tracer(fn.__module__)

    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            tracer = _get_tracer()
            with tracer.start_as_current_span(f"{fn.__module__}.{fn.__name__}") as span:
                span.set_attribute("correlation.id", get_correlation_id())
                try:
                    bound = inspect.signature(fn).bind_partial(*args, **kwargs)
                    params: Dict[str, Any] = {
                        k: _serialize_arg(v)
                        for k, v in bound.arguments.items()
                        if k not in {"self", "cls"}
                    }
                    span.set_attribute("function.args", _safe_json(params))
                except Exception as exc:
                    logger.debug("arg capture failed: %s", exc, exc_info=False)

                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.StatusCode.ERROR, str(exc))
                    raise

                try:
                    span.set_attribute(
                        "function.return_value", _safe_json(_serialize_arg(result))
                    )
                except Exception as exc:
                    logger.debug("ret-val capture failed: %s", exc, exc_info=False)

                return result

        return async_wrapper

    elif inspect.isasyncgenfunction(fn):

        @wraps(fn)
        async def agen_wrapper(*args, **kwargs):
            tracer = _get_tracer()
            with tracer.start_as_current_span(f"{fn.__module__}.{fn.__name__}") as span:
                span.set_attribute("correlation.id", get_correlation_id())
                try:
                    bound = inspect.signature(fn).bind_partial(*args, **kwargs)
                    params: Dict[str, Any] = {
                        k: _serialize_arg(v)
                        for k, v in bound.arguments.items()
                        if k not in {"self", "cls"}
                    }
                    span.set_attribute("function.args", _safe_json(params))
                except Exception as exc:
                    logger.debug("arg capture failed: %s", exc, exc_info=False)

                # Accumulate yielded items (capped) so the streamed output is
                # recorded as function.return_value, mirroring sync/async wrappers.
                items: list = []
                try:
                    async for item in fn(*args, **kwargs):
                        if len(items) < 500:
                            items.append(_serialize_arg(item))
                        yield item
                except GeneratorExit:
                    raise
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.StatusCode.ERROR, str(exc))
                    raise
                finally:
                    try:
                        span.set_attribute(
                            "function.return_value", _safe_json(items)
                        )
                    except Exception as exc:
                        logger.debug("ret-val capture failed: %s", exc, exc_info=False)

        return agen_wrapper

    else:

        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            tracer = _get_tracer()
            with tracer.start_as_current_span(f"{fn.__module__}.{fn.__name__}") as span:
                span.set_attribute("correlation.id", get_correlation_id())
                try:
                    bound = inspect.signature(fn).bind_partial(*args, **kwargs)
                    params = {
                        k: _serialize_arg(v)
                        for k, v in bound.arguments.items()
                        if k not in {"self", "cls"}
                    }
                    span.set_attribute("function.args", _safe_json(params))
                except Exception as exc:
                    logger.debug("arg capture failed: %s", exc, exc_info=False)

                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.StatusCode.ERROR, str(exc))
                    raise

                try:
                    span.set_attribute(
                        "function.return_value", _safe_json(_serialize_arg(result))
                    )
                except Exception as exc:
                    logger.debug("ret-val capture failed: %s", exc, exc_info=False)

                return result

        return sync_wrapper


# --------------------------------------------------------------------------- #
#  no_trace decorator
# --------------------------------------------------------------------------- #


def no_trace(fn):
    """Mark a function so ``auto_trace_module_functions`` skips it, and drop
    its span from the OTLP exporter at call time.
    """
    fn._no_trace = True

    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            span = get_current_span()
            if span and span.is_recording():
                span.set_attribute("_no_trace", True)
            token = _otel_context.attach(trace.set_span_in_context(trace.INVALID_SPAN))
            try:
                return await fn(*args, **kwargs)
            finally:
                _otel_context.detach(token)

        return async_wrapper

    @wraps(fn)
    def sync_wrapper(*args, **kwargs):
        span = get_current_span()
        if span and span.is_recording():
            span.set_attribute("_no_trace", True)
        token = _otel_context.attach(trace.set_span_in_context(trace.INVALID_SPAN))
        try:
            return fn(*args, **kwargs)
        finally:
            _otel_context.detach(token)

    return sync_wrapper


# --------------------------------------------------------------------------- #
#  Auto-tracing
# --------------------------------------------------------------------------- #


def auto_trace_module_functions(
    module, decorator=traced, recursive=True, only_local=True
):
    """Automatically decorate all functions in a module (and submodules) with
    ``decorator``. Functions tagged ``@no_trace`` or modules with
    ``NO_TRACE = True`` are skipped.
    """
    import types

    def should_decorate(obj, mod):
        return (
            (
                inspect.isfunction(obj)
                or inspect.iscoroutinefunction(obj)
                or inspect.isasyncgenfunction(obj)
            )
            and obj.__module__ == mod.__name__
            and not getattr(obj, "_no_trace", False)
        )

    otel_utils_modname = __name__

    if getattr(module, "NO_TRACE", False):
        return

    for name, obj in vars(module).items():
        if inspect.ismodule(obj) and obj.__name__ == otel_utils_modname:
            continue
        if should_decorate(obj, module):
            setattr(module, name, decorator(obj))
        elif inspect.isclass(obj) and obj.__module__ == module.__name__:
            for attr_name, attr in vars(obj).items():
                if should_decorate(attr, module):
                    setattr(obj, attr_name, decorator(attr))
        elif (
            recursive
            and isinstance(obj, types.ModuleType)
            and obj.__name__.startswith(module.__name__)
            and obj.__name__ != otel_utils_modname
        ):
            auto_trace_module_functions(obj, decorator, recursive, only_local)


# --------------------------------------------------------------------------- #
#  Correlation ID middleware + outbound propagation
# --------------------------------------------------------------------------- #

# Matches W3C traceparent: 00-<32hex trace_id>-<16hex parent_id>-<2hex flags>
_TRACEPARENT_RE = re.compile(
    r"^[\da-f]{2}-(?P<trace_id>[\da-f]{32})-[\da-f]{16}-[\da-f]{2}$"
)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate a correlation ID for the lifetime of each HTTP request.

    Inbound resolution order (first non-empty wins):
    1. ``X-Correlation-ID`` request header.
    2. ``traceparent`` request header (W3C trace-id).
    3. Lazy via ``get_correlation_id()`` (OTel trace ID or UUID4).
    """

    async def dispatch(self, request, call_next):
        corr_id = request.headers.get("X-Correlation-ID", "").strip()

        if not corr_id:
            tp = request.headers.get("traceparent", "").strip()
            m = _TRACEPARENT_RE.match(tp)
            if m:
                corr_id = m.group("trace_id")

        # The chat session id is a separate grouping dimension (one chat spans
        # many requests/traces), kept distinct from correlation_id == trace_id.
        chat_id = request.headers.get("X-Chat-ID", "").strip()
        if chat_id:
            span = get_current_span()
            if span and span.is_recording():
                span.set_attribute("chat.id", chat_id)

        token = _correlation_id_ctx.set(corr_id)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = get_correlation_id()
            return response
        finally:
            _correlation_id_ctx.reset(token)


def _requests_correlation_hook(span, request):
    """Inject ``X-Correlation-ID`` into outbound ``requests`` calls."""
    corr_id = get_correlation_id()
    if corr_id:
        request.headers["X-Correlation-ID"] = corr_id


# --------------------------------------------------------------------------- #
#  Frontend-originated correlation id  ->  root trace id
# --------------------------------------------------------------------------- #
#
# A browser/client with no OpenTelemetry SDK can still originate the correlation
# id by sending a plain ``X-Correlation-ID: <32 hex>`` header (and NO
# ``traceparent``). For such entry-point requests we want the auto-created
# SERVER span to be the ROOT of a trace whose ``trace_id`` equals that
# correlation id, so that:
#   * logs (correlation_id) and traces (trace_id) line up, and
#   * the trace has a real root span — no phantom browser parent that Tempo
#     reports forever as "<root span not yet received>".
#
# Wiring: the OTel ASGI middleware calls the global propagator's ``extract()``
# immediately before it mints the server span. ``_CorrelationIdPropagator``
# stashes the desired trace id in a ContextVar during ``extract`` (only when no
# real remote parent is present), and ``_CorrelationIdGenerator`` consumes it
# when the SDK mints the root trace id. Backend-to-backend hops that carry a
# real ``traceparent`` (SQS, internal HTTP) are untouched — they keep nesting as
# children of the emitting upstream span.

_forced_trace_id_ctx: ContextVar[int] = ContextVar("forced_trace_id", default=0)


def _parse_trace_id(value: str) -> int:
    """Parse a 32-hex W3C trace id to int; return 0 if invalid or all-zero."""
    value = value.strip()
    if len(value) != 32:
        return 0
    try:
        return int(value, 16)  # 0 (all-zero) is already the invalid sentinel
    except ValueError:
        return 0


class _CorrelationIdGenerator(RandomIdGenerator):
    """Mint the root trace id from ``X-Correlation-ID`` when the client set one.

    Every other span gets a random id (children inherit their parent's trace id
    and never call this).
    """

    def generate_trace_id(self) -> int:
        forced = _forced_trace_id_ctx.get()
        if forced:
            # Consume so only the first (root) span of the request adopts it;
            # any later root in the same context gets a fresh random id.
            _forced_trace_id_ctx.set(0)
            return forced
        return super().generate_trace_id()


class _CorrelationIdPropagator(TextMapPropagator):
    """Wrap the global propagator; adopt ``X-Correlation-ID`` as the root trace id.

    Acts only when the carrier has NO valid remote parent (a true entry point).
    When a ``traceparent`` is present the wrapped propagator yields a remote
    parent and the span nests normally.
    """

    def __init__(self, inner: TextMapPropagator) -> None:
        self._inner = inner

    def extract(self, carrier, context=None, getter=default_getter):
        ctx = self._inner.extract(carrier, context, getter)
        if get_current_span(ctx).get_span_context().is_valid:
            return ctx  # real remote parent -> keep nesting as a child
        values = getter.get(carrier, "x-correlation-id")
        tid = _parse_trace_id(values[0]) if values else 0
        if tid:
            _forced_trace_id_ctx.set(tid)
        return ctx

    def inject(self, carrier, context=None, setter=default_setter):
        self._inner.inject(carrier, context, setter)

    @property
    def fields(self):
        return self._inner.fields


def install_correlation_id_propagator() -> None:
    """Wrap the global textmap propagator once (idempotent)."""
    current = get_global_textmap()
    if isinstance(current, _CorrelationIdPropagator):
        return
    set_global_textmap(_CorrelationIdPropagator(current))


# --------------------------------------------------------------------------- #
#  Instrumentation helpers
# --------------------------------------------------------------------------- #


class _NoTraceSpanProcessor(SpanProcessor):
    """Wraps another SpanProcessor and drops spans tagged ``_no_trace = True``."""

    def __init__(self, inner: SpanProcessor) -> None:
        self._inner = inner

    def on_start(self, span, parent_context=None):
        self._inner.on_start(span, parent_context)

    def on_end(self, span):
        if not (span.attributes or {}).get("_no_trace", False):
            self._inner.on_end(span)

    def shutdown(self):
        return self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000):
        return self._inner.force_flush(timeout_millis)


def _safe_instrument(instrumentor_cls, *args, **kwargs):
    inst = instrumentor_cls()
    flag = getattr(inst, "is_instrumented_by_opentelemetry", False)
    if not (flag() if callable(flag) else flag):
        inst.instrument(*args, **kwargs)
    return inst
