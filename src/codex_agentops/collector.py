from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings, load_settings
from .outbox import Outbox
from .paths import collector_log_path, collector_pid_path, otel_heartbeat_path, outbox_path
from .usage import TokenUsage


MAX_REQUEST_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class OtelUsageEvent:
    event_id: str
    session_id: str
    observed_ns: int
    model: str | None
    usage: TokenUsage


@dataclass(frozen=True)
class OtelToolResultEvent:
    event_id: str
    conversation_id: str
    call_id: str
    tool_name: str
    observed_ns: int
    duration_ms: float
    success: bool


def ensure_collector(settings: Settings, *, wait_seconds: float = 2.0) -> None:
    if collector_health(settings.otel_port):
        return
    log_path = collector_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "codex_agentops.cli", "_collector"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if collector_health(settings.otel_port):
            return
        time.sleep(0.1)
    raise RuntimeError(f"Codex AgentOps OTel collector did not start on 127.0.0.1:{settings.otel_port}.")


def collector_health(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.3) as response:
            payload = json.loads(response.read())
        return response.status == 200 and payload.get("service") == "codex-agentops-otel"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def serve_collector(settings: Settings | None = None) -> None:
    active = settings or load_settings()
    target = collector_pid_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{os.getpid()}\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", active.otel_port), _handler(active))
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            target.unlink()
        except FileNotFoundError:
            pass


def parse_otlp_logs(payload: dict[str, Any]) -> tuple[OtelUsageEvent, ...]:
    events: list[OtelUsageEvent] = []
    for resource_logs in payload.get("resourceLogs", []):
        for scope_logs in resource_logs.get("scopeLogs", []):
            for record in scope_logs.get("logRecords", []):
                attrs = _attributes(record.get("attributes"))
                if attrs.get("event.name") != "codex.sse_event":
                    continue
                if attrs.get("event.kind") != "response.completed":
                    continue
                if "input_token_count" not in attrs or "output_token_count" not in attrs:
                    continue
                session_id = str(attrs.get("conversation.id") or "").strip()
                if not session_id:
                    continue
                usage = TokenUsage.from_dict(
                    {
                        "input_tokens": attrs.get("input_token_count"),
                        "output_tokens": attrs.get("output_token_count"),
                        "cached_input_tokens": attrs.get("cached_token_count"),
                        "cache_write_input_tokens": attrs.get("cache_write_token_count"),
                        "reasoning_output_tokens": attrs.get("reasoning_token_count"),
                    }
                )
                observed_ns = _timestamp_ns(record, attrs)
                canonical = json.dumps(
                    {
                        "session_id": session_id,
                        "observed_ns": observed_ns,
                        "model": attrs.get("model"),
                        "usage": usage.__dict__,
                        "trace_id": record.get("traceId"),
                        "span_id": record.get("spanId"),
                    },
                    sort_keys=True,
                    default=str,
                )
                events.append(
                    OtelUsageEvent(
                        event_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                        session_id=session_id,
                        observed_ns=observed_ns,
                        model=str(attrs.get("model") or "") or None,
                        usage=usage,
                    )
                )
    return tuple(events)


def parse_otlp_tool_results(payload: dict[str, Any]) -> tuple[OtelToolResultEvent, ...]:
    events: list[OtelToolResultEvent] = []
    for resource_logs in payload.get("resourceLogs", []):
        for scope_logs in resource_logs.get("scopeLogs", []):
            for record in scope_logs.get("logRecords", []):
                attrs = _attributes(record.get("attributes"))
                if attrs.get("event.name") != "codex.tool_result":
                    continue
                conversation_id = str(attrs.get("conversation.id") or "").strip()
                call_id = str(attrs.get("call_id") or "").strip()
                tool_name = str(attrs.get("tool_name") or "").strip()
                if not conversation_id or not call_id or not tool_name:
                    continue
                observed_ns = _timestamp_ns(record, attrs)
                duration_ms = _as_float(attrs.get("duration_ms"))
                success = _as_bool(attrs.get("success"))
                canonical = json.dumps(
                    {
                        "conversation_id": conversation_id,
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "observed_ns": observed_ns,
                        "duration_ms": duration_ms,
                        "success": success,
                        "tool_result_seq": attrs.get("tool_result_seq"),
                    },
                    sort_keys=True,
                    default=str,
                )
                events.append(
                    OtelToolResultEvent(
                        event_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                        conversation_id=conversation_id,
                        call_id=call_id,
                        tool_name=tool_name,
                        observed_ns=observed_ns,
                        duration_ms=duration_ms,
                        success=success,
                    )
                )
    return tuple(events)


def ingest_otlp_logs(payload: dict[str, Any], outbox: Outbox) -> int:
    inserted = 0
    for event in parse_otlp_logs(payload):
        if outbox.append_token_usage(
            event_id=event.event_id,
            session_id=event.session_id,
            observed_ns=event.observed_ns,
            source="otel",
            usage=event.usage,
            model=event.model,
        ):
            inserted += 1
    for event in parse_otlp_tool_results(payload):
        if outbox.append_tool_observation(
            event_id=event.event_id,
            conversation_id=event.conversation_id,
            call_id=event.call_id,
            tool_name=event.tool_name,
            observed_ns=event.observed_ns,
            duration_ms=event.duration_ms,
            success=event.success,
        ):
            inserted += 1
    _write_heartbeat("logs", inserted)
    return inserted


def _handler(settings: Settings):
    outbox = Outbox(outbox_path())

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self._send(404, {"error": "not_found"})
                return
            self._send(200, {"service": "codex-agentops-otel", "version": __version__})

        def do_POST(self) -> None:
            if self.path not in {"/v1/logs", "/v1/metrics"}:
                self._send(404, {"error": "not_found"})
                return
            length = int(self.headers.get("content-length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._send(413, {"error": "invalid_size"})
                return
            if "json" not in self.headers.get("content-type", "").lower():
                self._send(415, {"error": "json_required"})
                return
            try:
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("OTLP payload must be an object")
                if self.path == "/v1/logs":
                    inserted = ingest_otlp_logs(payload, outbox)
                    if inserted:
                        _launch_uploader()
                else:
                    _write_heartbeat("metrics", 0)
                self._send(200, {})
            except (json.JSONDecodeError, ValueError):
                self._send(400, {"error": "invalid_json"})

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def _attributes(items: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict) or "key" not in item:
            continue
        value = item.get("value")
        if not isinstance(value, dict):
            continue
        for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if key in value:
                result[str(item["key"])] = value[key]
                break
    return result


def _timestamp_ns(record: dict[str, Any], attrs: dict[str, Any]) -> int:
    try:
        value = int(record.get("timeUnixNano") or 0)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    raw = str(attrs.get("event.timestamp") or "")
    if raw:
        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp() * 1_000_000_000)
        except ValueError:
            pass
    return time.time_ns()


def _as_float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _write_heartbeat(signal: str, inserted: int) -> None:
    target = otel_heartbeat_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"signal": signal, "token_events_inserted": inserted, "observed_at_ns": time.time_ns()}
        )
        + "\n",
        encoding="utf-8",
    )


def _launch_uploader() -> None:
    log_path = collector_log_path()
    with log_path.open("ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "codex_agentops.cli", "_upload", "--wait-for-usage", "2"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
