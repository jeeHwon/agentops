from __future__ import annotations

import os
import signal
from types import SimpleNamespace

import pytest

from codex_agentops import collector
from codex_agentops.config import Settings


SETTINGS = Settings("chosen", "https://example.com")


def test_old_collector_is_not_considered_healthy(monkeypatch):
    monkeypatch.setattr(collector, "collector_info", lambda port: {"version": "1.2.0"})

    assert collector.collector_health(SETTINGS.otel_port) is False
    with pytest.raises(RuntimeError, match="aops collector-restart"):
        collector.ensure_collector(SETTINGS)


def test_restart_refuses_pending_spool(monkeypatch):
    monkeypatch.setattr(collector, "collector_info", lambda port: {
        "version": "1.2.0", "otel_spool": {"pending_batches": 1},
    })
    monkeypatch.setattr(collector.os, "kill", lambda *args: pytest.fail("must not stop a busy collector"))

    with pytest.raises(RuntimeError, match="pending OTel records"):
        collector.restart_collector(SETTINGS)


def test_restart_refuses_unrelated_pid(monkeypatch, tmp_path):
    pid_file = tmp_path / "collector.pid"
    pid_file.write_text(str(os.getpid()))
    monkeypatch.setattr(collector, "collector_pid_path", lambda: pid_file)
    monkeypatch.setattr(collector, "collector_info", lambda port: {
        "version": "1.2.0", "otel_spool": {},
    })
    monkeypatch.setattr(collector, "Outbox", lambda path: SimpleNamespace(
        stats=lambda: {"open": 0, "uploading": 0, "session_uploading": 0},
    ))
    monkeypatch.setattr(collector.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="python -m other_service",
    ))
    monkeypatch.setattr(collector.os, "kill", lambda *args: pytest.fail("must not stop another process"))

    with pytest.raises(RuntimeError, match="does not belong to AgentOps"):
        collector.restart_collector(SETTINGS)


def test_restart_stops_only_verified_idle_collector(monkeypatch, tmp_path):
    pid_file = tmp_path / "collector.pid"
    pid_file.write_text(str(os.getpid()))
    monkeypatch.setattr(collector, "collector_pid_path", lambda: pid_file)
    active = iter(({"version": "1.2.0", "otel_spool": {}}, None))
    monkeypatch.setattr(collector, "collector_info", lambda port: next(active))
    monkeypatch.setattr(collector, "Outbox", lambda path: SimpleNamespace(
        stats=lambda: {"open": 0, "uploading": 0, "session_uploading": 0},
    ))
    monkeypatch.setattr(collector.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout="/usr/bin/python -m codex_agentops.cli _collector",
    ))
    signals = []
    monkeypatch.setattr(collector.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    started = []
    monkeypatch.setattr(collector, "ensure_collector", lambda settings, wait_seconds: started.append(settings))

    collector.restart_collector(SETTINGS)

    assert signals == [(os.getpid(), signal.SIGTERM)]
    assert started == [SETTINGS]
