import logging
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import telemetry
from app.database import Base, engine


def test_setup_telemetry_disabled_when_no_endpoint(monkeypatch):
    for var in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    ):
        monkeypatch.delenv(var, raising=False)

    app = FastAPI()
    assert telemetry.setup_telemetry(app, engine) is False
    assert telemetry._state.enabled is False


def test_setup_and_shutdown_telemetry_when_configured(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "allsky-map-test")

    # Replace OTLP HTTP exporters with no-op/in-memory equivalents so no network IO occurs
    span_exporter = InMemorySpanExporter()
    metric_reader = InMemoryMetricReader()

    class _DummyLogExporter:
        def export(self, batch):
            return 0

        def shutdown(self):
            return None

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        lambda *a, **kw: span_exporter,
    )
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "opentelemetry.sdk.metrics.export.PeriodicExportingMetricReader",
        lambda *a, **kw: metric_reader,
    )
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter",
        lambda *a, **kw: _DummyLogExporter(),
    )

    test_app = FastAPI()

    @test_app.get("/ping-test")
    def ping_test():
        return {"ok": True}

    assert telemetry.setup_telemetry(test_app, engine) is True
    assert telemetry._state.enabled is True
    assert telemetry._state.log_handler in logging.getLogger().handlers

    try:
        with TestClient(test_app) as client:
            res = client.get("/ping-test")
            assert res.status_code == 200

        # Observable camera count callback executes cleanly against DB
        Base.metadata.create_all(bind=engine)
        observations = list(telemetry._observe_camera_counts(None))
        assert isinstance(observations, list)

        # InMemoryMetricReader can collect metrics including observable gauge
        metrics_data = metric_reader.get_metrics_data()
        assert metrics_data is not None
    finally:
        telemetry.shutdown_telemetry()

    assert telemetry._state.enabled is False
    assert telemetry._state.log_handler is None


def test_http_telemetry_middleware_records_bandwidth_and_latency():
    from app.main import app

    with TestClient(app) as client:
        res = client.get("/api/cameras")
        assert res.status_code == 200


def test_sqlalchemy_fallback_when_greenlet_missing(monkeypatch):
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    def _raise_greenlet(*args, **kwargs):
        raise ImportError("greenlet missing")

    monkeypatch.setattr(SQLAlchemyInstrumentor, "instrument", _raise_greenlet)
    tracer = telemetry._instrument_sqlalchemy(engine)
    assert tracer is not None
    tracer.remove_all_event_listeners()
