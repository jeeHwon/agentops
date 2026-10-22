from __future__ import annotations

import json
import os
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .config import Settings
from .manifest import (
    AGENT_ID_RE,
    SPEC_TEMPLATE,
    AgentManifest,
    ManifestError,
    _UniqueKeyLoader,
    load_manifest,
)


EXCLUDED_PARTS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".aops", ".agents",
    ".codex", ".claude", ".agentbricks", "node_modules", "eval", ".databrickscfg",
    "credentials", "secrets", ".ssh", ".aws",
}
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_SOURCE_FILES = 10000


def default_project(framework: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "framework": framework,
        "source_paths": ["genie.json"] if framework == "genie" else [],
        "runtime": {
            "resource_id": "",
            "workspace_host": "",
            "invocation_path": "",
            "warehouse_id": "",
            "parent_path": "",
        },
        "tracing": {
            "experiment_id": "",
            "workspace_host": "",
            "catalog": "",
            "schema": "",
            "warehouse_id": "",
        },
    }


def load_project(root: str | Path) -> dict[str, Any]:
    manifest = load_manifest(root)
    path = manifest.root / "aops.yaml"
    if not path.exists() and manifest.build_type == "skill":
        return default_project("codex")
    if path.is_symlink():
        raise ManifestError("Symbolic links are not allowed for aops.yaml.")
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"Cannot read aops.yaml: {exc}") from exc
    required = {"schema_version", "framework", "source_paths"}
    if not isinstance(raw, dict) or not required <= set(raw) or set(raw) - required - {"runtime", "tracing"}:
        raise ManifestError("aops.yaml requires schema_version, framework, source_paths; runtime and tracing are optional.")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ManifestError("Unsupported aops.yaml schema_version.")
    allowed = {"skill": {"codex"}, "code": {"langgraph", "openai"}, "no-code": {"genie"}}
    if not isinstance(raw["framework"], str) or raw["framework"] not in allowed[manifest.build_type]:
        raise ManifestError("aops.yaml framework does not match SPEC.md build_type.")
    paths = raw["source_paths"]
    if not isinstance(paths, list) or not all(isinstance(item, str) and item.strip() for item in paths):
        raise ManifestError("source_paths must be a list of relative paths.")
    for group in ("runtime", "tracing"):
        expected = default_project(raw["framework"])[group]
        values = raw.get(group, {})
        if not isinstance(values, dict) or set(values) - set(expected):
            raise ManifestError(f"aops.yaml {group} only supports {', '.join(expected)}.")
        if not all(isinstance(value, str) for value in values.values()):
            raise ManifestError(f"aops.yaml {group} values must be strings.")
        raw[group] = {**expected, **values}
        host = raw[group]["workspace_host"]
        parsed = urlsplit(host)
        if host and (
            parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
        ):
            raise ManifestError(f"{group}.workspace_host must be an HTTPS workspace origin.")
    runtime = raw["runtime"]
    host = runtime["workspace_host"]
    if bool(runtime["resource_id"]) != bool(host):
        raise ManifestError("resource_id and workspace_host must be set together.")
    invocation = runtime["invocation_path"]
    if invocation and (not invocation.startswith("/") or invocation.startswith("//") or "://" in invocation):
        raise ManifestError("invocation_path must be an absolute path on the bound App.")
    tracing = raw["tracing"]
    if tracing["experiment_id"] and not tracing["experiment_id"].isdigit():
        raise ManifestError("tracing.experiment_id must be a numeric Databricks experiment ID.")
    return raw


def resolve_project(root: str | Path, settings: Settings | None = None) -> dict[str, Any]:
    project = load_project(root)
    if settings is not None:
        project["tracing"] = {
            **settings.tracing,
            **{key: value for key, value in project["tracing"].items() if value},
        }
    return project


def save_project(root: str | Path, project: dict[str, Any]) -> None:
    target = Path(root) / "aops.yaml"
    if target.is_symlink():
        raise ManifestError("Symbolic links are not allowed for aops.yaml.")
    fd, temporary = tempfile.mkstemp(prefix="aops-", suffix=".yaml", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            compact = dict(project)
            for group in ("runtime", "tracing"):
                values = {key: value for key, value in project.get(group, {}).items() if value}
                if values:
                    compact[group] = values
                else:
                    compact.pop(group, None)
            yaml.safe_dump(compact, handle, allow_unicode=True, sort_keys=False)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_project(manifest: AgentManifest) -> None:
    project = load_project(manifest.root)
    files = project_source_files(manifest.root, project)
    names = {path.relative_to(manifest.root).as_posix() for path in files}
    if manifest.build_type == "code":
        if manifest.serving_type.lower() != "apps":
            raise ManifestError("Agent Bricks CLI projects currently require serving_type: apps.")
        for name in ("agent.toml", "pyproject.toml"):
            if name not in names:
                raise ManifestError(f"Code source_paths must include {name}.")
            try:
                config = tomllib.loads((manifest.root / name).read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                raise ManifestError(f"Invalid {name}: {exc}") from exc
            if name == "agent.toml" and config.get("agent", {}).get("framework") != project["framework"]:
                raise ManifestError("agent.toml framework must match aops.yaml framework.")
    elif manifest.build_type == "no-code":
        if "genie.json" not in names:
            raise ManifestError("Genie source_paths must include genie.json.")
        load_genie_definition(manifest.root)


def load_genie_definition(root: str | Path) -> dict[str, Any]:
    try:
        raw = json.loads((Path(root) / "genie.json").read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ManifestError(f"Invalid genie.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError("genie.json must contain the exported serialized_space JSON object.")
    return raw


def _excluded(path: Path) -> bool:
    if path == Path(".agentbricks/project.toml"):
        return False
    return (
        any(part in EXCLUDED_PARTS for part in path.parts)
        or any(part.startswith(".env") for part in path.parts)
        or path.name in {".DS_Store", "token", "tokens.json"}
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".pyc"}
    )


def project_source_files(root: Path, project: dict[str, Any]) -> list[Path]:
    files: set[Path] = {root / "SPEC.md", root / "aops.yaml"}
    for name in ("README.md", "AGENTS.md"):
        if (root / name).exists():
            files.add(root / name)
    for value in project["source_paths"]:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts or relative == Path(".") or _excluded(relative):
            raise ManifestError(f"Unsafe or excluded source_paths entry: {value}")
        selected = root / relative
        for component in (relative, *relative.parents):
            if (root / component).is_symlink():
                raise ManifestError(f"Symbolic links are not allowed in source_paths: {value}")
        if not selected.exists():
            raise ManifestError(f"source_paths entry does not exist: {value}")
        entries = [selected, *selected.rglob("*")] if selected.is_dir() else [selected]
        for path in entries:
            if _excluded(path.relative_to(root)):
                continue
            if path.is_symlink():
                raise ManifestError(f"Symbolic links are not allowed in Agent source: {path}")
            if path.is_file():
                files.add(path)
    total = 0
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ManifestError(f"Missing source file or symbolic link: {path}")
        total += path.stat().st_size
    if len(files) > MAX_SOURCE_FILES or total > MAX_SOURCE_BYTES:
        raise ManifestError("Agent source exceeds the 50 MiB / 10000 file snapshot limit.")
    return sorted(files)


def create_project(
    agent_id: str,
    destination: str | Path,
    *,
    build_type: str,
    framework: str = "langgraph",
    profile: str | None = None,
    runner: Any = None,
) -> Path:
    if not AGENT_ID_RE.fullmatch(agent_id) or "--" in agent_id:
        raise ManifestError("agent_id must use lower-case kebab-case.")
    root = Path(destination).expanduser().resolve()
    if root.exists():
        raise ManifestError(f"Destination already exists: {root}")
    if build_type == "code":
        if not profile or not profile.strip():
            raise ManifestError("Code init requires an explicitly selected --profile.")
        if framework not in {"langgraph", "openai"}:
            raise ManifestError("Agent Bricks framework must be langgraph or openai.")
        command = [
            "agentbricks", "--profile", profile, "--output", "json", "init",
            "--framework", framework, "--server", "agentbricks", "--disable-chat-app", str(root),
        ]
        result = (runner or _run)(command)
        if result.returncode:
            raise ManifestError("Agent Bricks init failed; inspect its local project and retry.")
        if not root.is_dir() or (root / "SPEC.md").exists() or (root / "aops.yaml").exists():
            raise ManifestError("Agent Bricks init did not produce a fresh project directory.")
        project = default_project(framework)
        project["source_paths"] = sorted(
            path.name for path in root.iterdir()
            if not _excluded(Path(path.name)) and not path.name.startswith(".")
        )
        if (root / ".agentbricks/project.toml").is_file():
            project["source_paths"].append(".agentbricks/project.toml")
        for name in (".gitignore", ".databricksignore"):
            if (root / name).is_file():
                project["source_paths"].append(name)
    elif build_type == "no-code":
        root.mkdir(parents=True)
        project = default_project("genie")
        (root / "genie.json").write_text("{}\n", encoding="utf-8")
        (root / "README.md").write_text(
            f"# {agent_id}\n\nExport a real Genie definition into genie.json before deployment.\n",
            encoding="utf-8",
        )
    else:
        raise ManifestError("create_project supports code and no-code.")
    spec = SPEC_TEMPLATE.format(agent_id=agent_id, name=agent_id.replace("-", " ").title())
    spec = spec.replace("build_type: skill", f"build_type: {build_type}")
    spec = spec.replace(
        "사용자의 업무 요청을 정의된 Skill 절차로 처리합니다.",
        "사용자의 업무 요청을 이 Agent의 네이티브 실행 정의로 처리합니다.",
    )
    if build_type == "no-code":
        spec = spec.replace("serving_type: apps", "serving_type: genie")
        spec = spec.replace("model_service: system.ai.gpt-5-6-sol", "model_service: genie-managed")
    (root / "SPEC.md").write_text(spec, encoding="utf-8")
    save_project(root, project)
    return root


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)
