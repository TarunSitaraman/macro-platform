"""Observability and tracing configuration using OpenTelemetry."""

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from src.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_otel_available = False
trace = None  # type: ignore


class _NoOpSpan:
    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *_args: Any) -> None:
        pass


class _NoOpTracer:
    @contextmanager
    def start_as_current_span(self, _name: str, **_kwargs: Any) -> Iterator[_NoOpSpan]:
        yield _NoOpSpan()


def _noop_tracer(_name: str) -> _NoOpTracer:
    return _NoOpTracer()


try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    trace = _otel_trace
    _otel_available = True
except ImportError:
    logger.debug("OpenTelemetry not installed — tracing disabled")


def setup_observability(app=None, engine=None):
    """Initialise tracing and instrumentation."""
    if not _otel_available:
        logger.info("Observability stack skipped (OpenTelemetry not installed)")
        return

    resource = Resource.create(
        attributes={
            "service.name": "macro-intelligence-platform",
            "environment": settings.app_env,
        }
    )

    provider = TracerProvider(resource=resource)

    otlp_endpoint = getattr(settings, "otlp_endpoint", None)
    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    else:
        exporter = ConsoleSpanExporter()

    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    if engine:
        SQLAlchemyInstrumentor().instrument(engine=engine)

    if app:
        FastAPIInstrumentor.instrument_app(app)

    logger.info(
        "Observability stack initialised (Tracing: %s)",
        "OTLP" if otlp_endpoint else "Console",
    )


def get_tracer(name: str):
    if not _otel_available or trace is None:
        return _noop_tracer(name)
    return trace.get_tracer(name)
