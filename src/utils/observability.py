"""Observability and tracing configuration using OpenTelemetry."""

import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

from src.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


def setup_observability(app=None, engine=None):
    """Initialise tracing and instrumentation."""
    resource = Resource.create(
        attributes={
            "service.name": "macro-intelligence-platform",
            "environment": settings.app_env,
        }
    )

    provider = TracerProvider(resource=resource)
    
    # Export to OTLP if configured, otherwise console
    # (In production you'd point this to a collector like Jaeger or Honeycomb)
    otlp_endpoint = getattr(settings, "otlp_endpoint", None)
    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    else:
        exporter = ConsoleSpanExporter()

    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    # Instrument SQLAlchemy
    if engine:
        SQLAlchemyInstrumentor().instrument(engine=engine)

    # Instrument FastAPI
    if app:
        FastAPIInstrumentor.instrument_app(app)

    logger.info("Observability stack initialised (Tracing: %s)", "OTLP" if otlp_endpoint else "Console")


def get_tracer(name: str):
    return trace.get_tracer(name)
