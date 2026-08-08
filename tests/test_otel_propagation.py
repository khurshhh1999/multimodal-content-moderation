from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from app.config import Settings
from app.queue import enqueue_sqs
from app.telemetry import inject_trace_context, setup_tracing, start_span
from worker.telemetry import (
    attach_trace_context,
    carrier_from_sqs_attributes,
    setup_tracing as setup_worker_tracing,
    start_span as worker_start_span,
)

_EXPORTER = InMemorySpanExporter()
_PROVIDER_READY = False


def _ensure_sdk_provider() -> InMemorySpanExporter:
    """Install a single SDK TracerProvider for this module (OTel forbids overrides)."""
    global _PROVIDER_READY
    import app.telemetry as api_tel
    import worker.telemetry as worker_tel

    if not _PROVIDER_READY:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace.set_tracer_provider(provider)
        _PROVIDER_READY = True
    api_tel._initialized = True
    worker_tel._initialized = True
    _EXPORTER.clear()
    return _EXPORTER


@pytest.fixture(autouse=True)
def _otel_sdk():
    _ensure_sdk_provider()
    yield
    _EXPORTER.clear()


def test_inject_and_extract_preserves_trace_id():
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("producer") as parent:
        parent_trace_id = parent.get_span_context().trace_id
        carrier = inject_trace_context()

    assert "traceparent" in carrier

    with attach_trace_context(carrier):
        with worker_start_span("consumer", kind=SpanKind.CONSUMER) as child:
            assert child.get_span_context().trace_id == parent_trace_id


def test_carrier_from_sqs_attributes():
    attrs = {
        "traceparent": {
            "DataType": "String",
            "StringValue": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        },
        "job_id": {"DataType": "String", "StringValue": "abc"},
        "empty": {"DataType": "String"},
    }
    carrier = carrier_from_sqs_attributes(attrs)
    assert carrier["traceparent"].startswith("00-4bf92f3577b34da6")
    assert carrier["job_id"] == "abc"
    assert "empty" not in carrier


@patch("app.queue.boto3.client")
def test_enqueue_sqs_attaches_traceparent_attribute(mock_client_factory):
    exporter = _ensure_sdk_provider()
    client = MagicMock()
    client.send_message.return_value = {"MessageId": "mid-1"}
    mock_client_factory.return_value = client

    settings = Settings(
        sqs_endpoint_url="http://localhost:4566",
        sqs_queue_url="http://localhost:4566/000000000000/moderation-jobs",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        aws_default_region="us-east-1",
        policy_version="policy-v1",
        pipeline_version="pipeline-v1",
    )
    job_id = uuid4()
    content_id = uuid4()

    with start_span("http.ingest"):
        enqueue_sqs(
            settings=settings,
            job_id=job_id,
            content_id=content_id,
            content_hash="abc123",
            object_key="content/ab/abc123/x.jpg",
            caption="hello",
        )

    kwargs = client.send_message.call_args.kwargs
    attrs = kwargs["MessageAttributes"]
    assert "traceparent" in attrs
    assert attrs["traceparent"]["DataType"] == "String"
    assert attrs["job_id"]["StringValue"] == str(job_id)
    assert attrs["content_hash"]["StringValue"] == "abc123"

    names = {s.name for s in exporter.get_finished_spans()}
    assert "queue.enqueue" in names
    assert "http.ingest" in names


def test_setup_tracing_disabled_marks_initialized():
    import app.telemetry as api_tel
    import worker.telemetry as worker_tel

    # Exercise the disabled path without fighting the module provider.
    api_tel._initialized = False
    worker_tel._initialized = False
    setup_tracing(service_name="x", enabled=False)
    setup_worker_tracing(service_name="y", enabled=False)
    assert api_tel._initialized is True
    assert worker_tel._initialized is True
    # Restore so later tests keep using the SDK provider.
    api_tel._initialized = True
    worker_tel._initialized = True
