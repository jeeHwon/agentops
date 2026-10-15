import json
import sqlite3
from dataclasses import asdict, replace

from codex_agentops.otel_bridge import OtelTraceAssembler, parse_otlp_traces
from codex_agentops.otel_spool import OtelSpool
from codex_agentops.collector import _drain_otel_spool
from codex_agentops.config import Settings
from test_otel_bridge import _payload


def test_otel_spool_survives_restart_before_and_after_assembly(tmp_path):
    spool_path = tmp_path / "spool.db"
    root, tool = parse_otlp_traces(_payload())
    spool = OtelSpool(spool_path)
    assert spool_path.stat().st_mode & 0o777 == 0o600
    spool.append(_payload())
    assert len(OtelSpool(spool_path).batches()) == 1
    batch_id, _ = spool.batches()[0]
    spool.persist_batch(batch_id, (tool,))
    assert [span.span_id for span in OtelSpool(spool_path).pending_spans()] == [tool.span_id]
    spool.append(_payload())
    root_batch_id = spool.batches()[0][0]
    spool.persist_batch(root_batch_id, (root,))
    assembler = OtelTraceAssembler()
    groups = assembler.ingest(spool.pending_spans())
    spool.save_groups(groups)
    assert len(OtelSpool(spool_path).completed()[0][1]) == 2
    spool.mark_exported(root.trace_id)
    assert OtelSpool(spool_path).completed() == []
    spool.append(_payload())
    duplicate_batch_id = spool.batches()[0][0]
    spool.persist_batch(duplicate_batch_id, (replace(root, span_id="ff" * 8),))
    assert spool.pending_spans() == ()


def test_otel_spool_discards_unrelated_http_spans(tmp_path):
    spool = OtelSpool(tmp_path / "spool.db")
    root, tool = parse_otlp_traces(_payload())
    infrastructure = replace(
        root, span_id="ab" * 8, name="HTTP websocket receive",
        attributes={"http.method": "GET"},
    )
    spool.append(_payload())
    batch_id, _ = spool.batches()[0]
    spool.persist_batch(batch_id, (infrastructure, root, tool))

    assert [span.name for span in spool.pending_spans()] == [root.name, tool.name]
    assert spool.batches() == []


def test_otel_spool_prunes_old_unrelated_spans_on_restart(tmp_path):
    path = tmp_path / "spool.db"
    OtelSpool(path)
    root, _ = parse_otlp_traces(_payload())
    infrastructure = replace(root, attributes={"http.method": "GET"})
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO spans VALUES (?, ?, ?)",
            (infrastructure.trace_id, infrastructure.span_id, json.dumps(asdict(infrastructure))),
        )

    assert OtelSpool(path).pending_spans() == ()


def test_otel_spool_retries_mlflow_failure_without_losing_acknowledged_batch(tmp_path, monkeypatch):
    spool = OtelSpool(tmp_path / "spool.db")
    spool.append(_payload())
    calls = []

    class Exporter:
        def __init__(self, settings):
            pass

        def export(self, spans):
            calls.append(len(spans))
            if len(calls) == 1:
                raise OSError("transient")

    monkeypatch.setattr("codex_agentops.collector.OtelMlflowExporter", Exporter)
    settings = Settings("test", "https://example.com")
    assembler = OtelTraceAssembler()
    _drain_otel_spool(spool, assembler, settings)
    assert spool.batches() == []
    assert len(spool.completed()) == 1
    _drain_otel_spool(spool, assembler, settings)
    assert calls == [2, 2]
    assert spool.completed() == []
