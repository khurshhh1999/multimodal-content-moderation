from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

_initialized = False
TRACER_NAME = "moderation.api"


def _otlp_http_traces_endpoint(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    if base.endswith("/v1/traces"):
        return base
    return f"{base}/v1/traces"


def setup_tracing(
    *,
    service_name: str,
    enabled: bool,
    otlp_endpoint: str = "",
    console_exporter: bool = False,
) -> None:
    """Configure a TracerProvider once. No-ops when disabled."""
    global _initialized
    if _initialized:
        return
    if not enabled:
        logger.info("OpenTelemetry tracing disabled")
        _initialized = True
        return

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "moderation",
        }
    )
    provider = TracerProvider(resource=resource)

    if otlp_endpoint.strip():
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        url = _otlp_http_traces_endpoint(otlp_endpoint.strip())
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=url)))
        logger.info("OpenTelemetry OTLP exporter → %s", url)

    if console_exporter:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("OpenTelemetry console span exporter enabled")

    if not otlp_endpoint.strip() and not console_exporter:
        logger.warning(
            "OpenTelemetry enabled but no OTLP endpoint / console exporter; spans stay in-process only"
        )

    trace.set_tracer_provider(provider)
    _initialized = True


def get_tracer(name: str = TRACER_NAME):
    return trace.get_tracer(name)


def inject_trace_context() -> dict[str, str]:
    """Serialize the current context as a W3C carrier (traceparent / tracestate)."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


@contextmanager
def start_span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    tracer = get_tracer()
    with tracer.start_as_current_span(name, kind=kind) as span:
        if attributes:
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:  # noqa: BLE001
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            raise


@contextmanager
def attach_trace_context(carrier: dict[str, str] | None) -> Iterator[None]:
    ctx = propagate.extract(carrier or {})
    token = otel_context.attach(ctx)
    try:
        yield
    finally:
        otel_context.detach(token)
