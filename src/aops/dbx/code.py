"""Code-based agents (Agent Bricks): checks, the deploy copy and `agentbricks deploy`.

A code agent folder is an Agent Bricks project (`agent.toml`, `app.yaml`, `pyproject.toml`, the agent and
runtime code) plus `aops.toml` and `SPEC.md`. The aops server deploys it itself, as its service principal:
on Databricks compute the `databricks` CLI that `agentbricks deploy` drives is web-terminal only, while
inside a Databricks App it works with the app's own credentials.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

AGENTBRICKS = "databricks-agentbricks==0.6.0"  # the CLI version aops was checked with
VENV = Path(os.environ.get("AOPS_AGENTBRICKS_VENV", "/tmp/aops-agentbricks"))
PROJECT_FILES = ("agent.toml", "app.yaml", "pyproject.toml")
APP_PREFIX = "agent-bricks-"
BASE_MAX = 17  # agentbricks: app name agent-bricks-<base>, 30 characters in all


def app_base(agent_id: str) -> str:
    """The deployment name agentbricks takes (the app is agent-bricks-<base>)."""
    if len(agent_id) <= BASE_MAX:
        return agent_id
    return f"{agent_id[:BASE_MAX - 5].rstrip('-')}-{hashlib.sha256(agent_id.encode()).hexdigest()[:4]}"


def app_name(agent_id: str) -> str:
    return APP_PREFIX + app_base(agent_id)


def check_project(files: dict[str, bytes]) -> dict[str, Any]:
    """The `project` validate step: an Agent Bricks project on the durable server."""
    problems = [f"{name}이 없습니다" for name in PROJECT_FILES if name not in files]
    if "agent.toml" in files:
        try:
            agent = tomllib.loads(files["agent.toml"].decode("utf-8")).get("agent") or {}
            if agent.get("server", "agentbricks") != "agentbricks":
                problems.append(f"agent.toml [agent] server가 {agent.get('server')!r}입니다 (agentbricks만)")
            if agent.get("framework") not in ("langgraph", "openai"):
                problems.append("agent.toml [agent] framework는 langgraph 또는 openai")
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
            problems.append(f"agent.toml을 읽지 못했습니다 ({error})")
    note = "" if "uv.lock" in files else " (uv.lock이 없어 배포 때 의존 버전이 고정되지 않습니다)"
    return {"name": "project", "status": "failed" if problems else "passed",
            "detail": "; ".join(problems) if problems else f"Agent Bricks 프로젝트 형식이 맞습니다{note}"}


def prepare(files: dict[str, bytes], dest: Path, experiment: str) -> list[str]:
    """Write the version's files into a fresh folder and point tracing at the production experiment.

    The registered zip stays as it is (its hash is the record); only this copy changes. Returns what changed.
    """
    if dest.exists():
        shutil.rmtree(dest)
    for rel, data in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    toml = dest / "agent.toml"
    text = toml.read_text(encoding="utf-8")
    line = f'experiment_name = "{experiment}"'
    if re.search(r"(?m)^\[tracing\]", text):
        if re.search(r"(?ms)^\[tracing\][^\[]*?^experiment_name\s*=", text):
            text = re.sub(r'(?m)^(experiment_name\s*=\s*).*$', line, text, count=1)
        else:
            text = re.sub(r"(?m)^\[tracing\]\s*$", "[tracing]\n" + line, text, count=1)
    else:
        text = text.rstrip("\n") + f"\n\n[tracing]\n{line}\n"
    toml.write_text(text, encoding="utf-8")
    return ["agent.toml"]


def agentbricks_cli() -> Path:
    """The agentbricks CLI in its own environment (kept apart from the server's packages)."""
    exe = VENV / "bin" / "agentbricks"
    if exe.exists():
        return exe
    uv = shutil.which("uv")
    if not uv:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "uv"], check=True, capture_output=True)
        uv = shutil.which("uv") or str(Path(sys.executable).parent / "uv")
    subprocess.run([uv, "venv", "-q", str(VENV), "--python", sys.executable], check=True, capture_output=True)
    subprocess.run([uv, "pip", "install", "-q", "--python", str(VENV / "bin" / "python"), AGENTBRICKS], check=True, capture_output=True)
    return exe


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def deploy(source: Path, base: str, workspace_path: str, profile: str | None = None, timeout: int = 1500) -> tuple[int, str]:
    """`agentbricks deploy <base>`; (exit code, the tail of its output without terminal codes)."""
    cmd = [str(agentbricks_cli())] + (["--profile", profile] if profile else []) + [
        "deploy", base, "--source", str(source), "--workspace-path", workspace_path]
    env = {**os.environ, "AGENTBRICKS_DISABLE_TELEMETRY": "1", "AGENTBRICKS_DISABLE_AGENT_HINT": "1",
           "PATH": f"{VENV / 'bin'}:{os.environ.get('PATH', '')}"}
    done = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    out = ANSI.sub("", (done.stdout or "") + "\n" + (done.stderr or "")).replace("\r", "\n")
    lines = [line for line in out.splitlines() if line.strip() and not line.strip()[:1] in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"]
    return done.returncode, "\n".join(lines[-40:])
