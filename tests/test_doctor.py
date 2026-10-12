from __future__ import annotations

import subprocess

from codex_agentops import doctor


def test_plugin_timeout_is_reported_as_warning(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=30)

    monkeypatch.setattr(doctor.subprocess, "run", timeout)

    check = doctor._plugin_check()

    assert check.level == "WARN"
    assert check.name == "codex-plugin"
