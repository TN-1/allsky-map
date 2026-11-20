"""OpenTelemetry (OTLP) instrumentation for LGTM (Loki, Grafana, Tempo, Mimir) observability.

Exports traces, metrics (including request latency and ingress/egress bandwidth),
and structured correlated logs over OTLP/HTTP when ``OTEL_EXPORTER_OTLP_ENDPOINT``
(or a signal-specific ``OTEL_EXPORTER_OTLP_*_ENDPOINT``) is configured.
When unconfigured, initialization is a zero-overhead no-op.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

from fastapi import FastAPI
from opentelemetry import _logs, metrics, trace
from opentelemetry.metrics import CallbackOptions, Observation
from sqlalchemy import func
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Application meter — returns no-op instruments until a MeterProvider is registered.
_meter = metrics.get_meter("allsky-map")

http_requests_total = _meter.create_counter(
    name="allsky.http.requests",
    unit="{request}",
    description="Total HTTP requests processed by route, method, and status code",
)
http_request_duration_ms = _meter.create_histogram(
    name="allsky.http.request.duration",
    unit="ms",
    description="HTTP request latency in milliseconds",
)
http_request_bytes = _meter.create_counter(
    name="allsky.http.request.bytes",
    unit="By",
    description="Total HTTP request payload bytes received",
)
http_response_bytes = _meter.create_counter(
    name="allsky.http.response.bytes",
    unit="By",
    description="Total HTTP response payload bytes transmitted",
)
camera_registrations = _meter.create_counter(
    name="allsky.camera.registrations",
    unit="{registration}",
    description="Total camera API key registrations",
)
camera_pings = _meter.create_counter(
    name="allsky.camera.pings",
    unit="{ping}",
    description="Total successful camera status pings",
)
image_upload_bytes = _meter.create_counter(
    name="allsky.camera.image_upload.bytes",
    unit="By",
    description="Total bytes of uploaded raw images and generated thumbnails",
)
bulk_exports = _meter.create_counter(
    name="allsky.bulk_exports",
    unit="{export}",
    description="Total bulk ZIP archive exports served",
)
bulk_export_bytes = _meter.create_counter(
    name="allsky.bulk_export.bytes",
    unit="By",
    description="Total bulk ZIP archive bytes transmitted",
)
bulk_export_cameras = _meter.create_histogram(
    name="allsky.bulk_export.cameras",
    unit="{camera}",
    description="Number of cameras included per bulk ZIP archive",
)
rate_limit_hits = _meter.create_counter(
    name="allsky.rate_limit.hits",
    unit="{hit}",
    description="Total requests rejected by in-memory rate limiters",
)
websocket_connections = _meter.create_up_down_counter(
    name="allsky.websocket.connections",
    unit="{connection}",
    description="Active WebSocket client connections",
)


def _otlp_configured() -> bool:
    return any(
        os.environ.get(var, "").strip()
        for var in (
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        )
    )


def _observe_camera_counts(options: CallbackOptions) -> Iterable[Observation]:
    """Read live camera counts grouped by status and research opt-in flag."""
    from app.database import SessionLocal
    from app.models import CameraDB

    db = SessionLocal()
    try:
        rows = (
            db.query(
                CameraDB.status,
                CameraDB.allow_research,
                CameraDB.name.isnot(None).label("registered"),
                func.count(CameraDB.api_key),
            )
            .group_by(CameraDB.status, CameraDB.allow_research, "registered")
            .all()
        )
        for status, allow_research, registered, count in rows:
            effective_status = status if registered else "unregistered"
            yield Observation(
                count,
                {
                    "status": effective_status or "unknown",
                    "allow_research": str(bool(allow_research)).lower(),
                },
            )
    except Exception as exc:
        logger.debug("Telemetry camera count observation skipped: %s", exc)
    finally:
        db.close()


class _TelemetryState:
    def __init__(self) -> None:
        self.enabled: bool = False
        self.tracer_provider = None
        self.meter_provider = None
        self.logger_provider = None
        self.log_handler: logging.Handler | None = None
        self._app: FastAPI | None = None
        self._engine: Engine | None = None
        self._engine_tracer = None


_state = _TelemetryState()


def _instrument_sqlalchemy(engine: Engine):
    """Instrument synchronous SQLAlchemy engine, falling back to EngineTracer if asyncio/greenlet is absent."""
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    try:
        SQLAlchemyInstrumentor().instrument(engine=engine, skip_dep_check=True)
        return None
    except ImportError:
        from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
        from opentelemetry.semconv.metrics import MetricInstruments

        tracer = trace.get_tracer("opentelemetry.instrumentation.sqlalchemy")
        meter = metrics.get_meter("opentelemetry.instrumentation.sqlalchemy")
        connections_usage = meter.create_up_down_counter(
            name=MetricInstruments.DB_CLIENT_CONNECTIONS_USAGE,
            unit="connections",
            description="The number of connections that are currently in state described by the state attribute.",
        )
        return EngineTracer(tracer, engine, connections_usage)


def setup_telemetry(app: FastAPI, engine: Engine) -> bool:
    """Configure OTLP tracing, metrics, and logging if an OTLP endpoint is set.

    Returns ``True`` when OTLP telemetry is enabled, ``False`` otherwise.
    """
    if _state.enabled:
        return True

    if not _otlp_configured():
        logger.info("OTLP telemetry disabled (OTEL_EXPORTER_OTLP_ENDPOINT not set)")
        return False

    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "allsky-map"),
        }
    )

    # 1. Traces -> Tempo
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    # 2. Metrics -> Mimir / Prometheus
    metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # Register observable gauge on the active meter provider
    active_meter = metrics.get_meter("allsky-map")
    active_meter.create_observable_gauge(
        name="allsky.cameras.count",
        callbacks=[_observe_camera_counts],
        unit="{camera}",
        description="Current number of cameras by status and research opt-in state",
    )

    # 3. Logs -> Loki (with trace_id/span_id correlation)
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    _logs.set_logger_provider(logger_provider)

    LoggingInstrumentor().instrument(set_logging_format=False)
    log_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logging.getLogger().addHandler(log_handler)

    # 4. Auto-instrumentation for FastAPI, SQLAlchemy, and outbound HTTPX
    FastAPIInstrumentor.instrument_app(app)
    _state._engine_tracer = _instrument_sqlalchemy(engine)
    HTTPXClientInstrumentor().instrument()

    _state.enabled = True
    _state.tracer_provider = tracer_provider
    _state.meter_provider = meter_provider
    _state.logger_provider = logger_provider
    _state.log_handler = log_handler
    _state._app = app
    _state._engine = engine

    logger.info(
        "OTLP telemetry enabled (service=%s, endpoint=%s)",
        resource.attributes.get("service.name"),
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "signal-specific"),
    )
    return True


def shutdown_telemetry() -> None:
    """Flush and tear down OTLP providers on application shutdown."""
    if not _state.enabled:
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        if _state._app is not None:
            FastAPIInstrumentor.uninstrument_app(_state._app)
        if _state._engine_tracer is not None:
            _state._engine_tracer.remove_all_event_listeners()
        elif _state._engine is not None:
            SQLAlchemyInstrumentor().uninstrument(engine=_state._engine)
        HTTPXClientInstrumentor().uninstrument()
        LoggingInstrumentor().uninstrument()
    except Exception as exc:
        logger.debug("Telemetry uninstrument error: %s", exc)

    if _state.log_handler is not None:
        logging.getLogger().removeHandler(_state.log_handler)

    for provider in (_state.tracer_provider, _state.meter_provider, _state.logger_provider):
        if provider is not None:
            try:
                provider.shutdown()
            except Exception as exc:
                logger.debug("Telemetry provider shutdown error: %s", exc)

    _state.enabled = False
    _state.tracer_provider = None
    _state.meter_provider = None
    _state.logger_provider = None
    _state.log_handler = None
    _state._app = None
    _state._engine = None
    _state._engine_tracer = None
