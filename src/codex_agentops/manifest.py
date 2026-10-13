from __future__ import annotations

import hashlib
import json
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


AGENT_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
VERSION_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")
SPEC_SCHEMA_VERSION = 1


class ManifestError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentManifest:
    root: Path
    agent_id: str
    name: str
    description: str
    schema_version: int
    agent_type: str
    service_criticality: str
    data_sensitivity: str
    security_access_level: str
    required_uc_permissions: tuple[str, ...]
    use_flagship_model: bool
    model: str
    owner: str
    owner_group: str
    source_system: str
    target_users: tuple[str, ...]
    cost_center: str
    risk_level: str
    hitl_required: bool
    success_criteria: dict[str, Any]


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    root: Path
    uc_mode: str
    uc_name: str


@dataclass(frozen=True)
class SubagentDefinition:
    name: str
    description: str
    sandbox_mode: str
    skills: tuple[str, ...]
    instructions: str
    root: Path


@dataclass(frozen=True)
class DefinitionChecksums:
    harness: str
    skills: dict[str, str]
    combined_skills: str


SPEC_TEMPLATE = """\
---
schema_version: 1
id: {agent_id}
name: {name}
description: 이 Agent가 해결하는 업무를 한 문장으로 작성하세요.
agent_type: harness
owner: ""
owner_group: ""
source_system: local
target_users: []
cost_center: ""
service_criticality: low
data_sensitivity: internal
security_access_level: standard
risk_level: low
hitl_required: false
required_uc_permissions: []
use_flagship_model: false
model: system.ai.gpt-5-6-sol
success_criteria:
  quality: "필수 결과를 빠짐없이 제공하고 근거 없는 사실을 생성하지 않습니다."
  latency_ms: null
  completion_rate: null
---

# Agent Specification

## 입력

- 사용자의 업무 요청
- 작업 수행에 필요한 배경 정보

## 출력

- 요청에 대한 최종 결과
- 판단 근거와 확인이 필요한 제한사항

## 성공 기준

- 사용자가 요청한 필수 결과를 모두 제공합니다.
- 근거 없는 사실을 생성하지 않습니다.
- 정의된 Skill 절차와 출력 형식을 따릅니다.
"""


AGENTS_TEMPLATE = """\
# Agent Instructions

## 목적

사용자의 업무 요청을 정의된 Skill 절차에 따라 처리하고 검토 가능한 결과를 반환합니다.

## 업무 범위

- `skills/`에 정의된 업무 절차를 사용합니다.
- 사용자에게 부여된 권한 안에서만 데이터와 도구를 사용합니다.

## Skill 사용

- 요청과 `description`이 일치하는 Skill만 사용합니다.
- 여러 Skill이 관련되면 필요한 Skill만 순서대로 사용합니다.

## Sub-agent 사용

- 독립적인 검증이 필요하면 `validator` Sub-agent에게 맡깁니다.

## 기본 원칙

- 제공되지 않은 사실은 추측하지 않습니다.
- 인증정보와 Secret을 출력하지 않습니다.
- 불확실한 내용은 명확히 표시합니다.
"""


EXAMPLE_SKILL = """\
---
name: request-structurer
description: 사용자의 업무 요청을 목적, 필수 입력, 제약사항과 다음 행동으로 구조화해야 할 때 사용합니다.
---

# Request Structurer

## 입력

- 사용자의 요청
- 사용자가 제공한 배경과 제약사항

## 수행 절차

1. 요청의 목적을 한 문장으로 정리합니다.
2. 필수 입력과 제약사항을 구분합니다.
3. 실행할 다음 행동을 순서대로 정리합니다.

## 출력 형식

- 목적
- 필수 입력
- 제약사항
- 다음 행동
"""


EXAMPLE_REFERENCE = """\
# Request Structurer 참고사항

- 사용자에게 없는 정보를 임의로 채우지 않습니다.
- 확인이 필요한 항목과 실행 가능한 항목을 분리합니다.
"""


EXAMPLE_SUBAGENT = """\
---
name: validator
description: 결과가 Agent의 성공 기준과 Skill 출력 형식을 충족하는지 독립적으로 검증할 때 사용합니다.
sandbox_mode: read-only
skills:
  - request-structurer
---

# Validator

결과의 사실성, 누락과 요구사항 충족 여부를 검증합니다.
문제를 발견하면 근거와 수정 방향을 간단히 반환합니다.
파일은 수정하지 않습니다.
"""


def find_agent_root(start: str | Path) -> Path | None:
    path = Path(start).expanduser().resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / "spec.md").is_file() and (candidate / "AGENTS.md").is_file():
            return candidate
    return None


def load_manifest(root: str | Path) -> AgentManifest:
    agent_root = Path(root).expanduser().resolve()
    metadata, body = read_markdown_frontmatter(agent_root / "spec.md", "spec.md")
    if not body.strip():
        raise ManifestError(f"spec.md instructions are empty: {agent_root / 'spec.md'}")
    schema_version = metadata.get("schema_version")
    if schema_version != SPEC_SCHEMA_VERSION:
        raise ManifestError(f"spec.md schema_version must be {SPEC_SCHEMA_VERSION}.")
    agent_id = _required_string(metadata, "id", "spec.md")
    if not AGENT_ID_RE.fullmatch(agent_id) or "--" in agent_id:
        raise ManifestError("spec.md id must use lower-case kebab-case and be at most 64 characters.")
    agent_type = _required_string(metadata, "agent_type", "spec.md")
    if agent_type != "harness":
        raise ManifestError("1차 구현에서 spec.md agent_type은 harness만 지원합니다.")
    required_permissions = metadata.get("required_uc_permissions")
    if not isinstance(required_permissions, list) or not all(
        isinstance(item, str) and item.strip() for item in required_permissions
    ):
        raise ManifestError("spec.md required_uc_permissions must be a string list.")
    use_flagship_model = metadata.get("use_flagship_model")
    if not isinstance(use_flagship_model, bool):
        raise ManifestError("spec.md use_flagship_model must be true or false.")
    hitl_required = metadata.get("hitl_required", False)
    if not isinstance(hitl_required, bool):
        raise ManifestError("spec.md hitl_required must be true or false.")
    service_criticality = _required_string(metadata, "service_criticality", "spec.md")
    data_sensitivity = _required_string(metadata, "data_sensitivity", "spec.md")
    security_access_level = _required_string(
        metadata, "security_access_level", "spec.md"
    )
    source_system = str(metadata.get("source_system") or "local").strip()
    risk_level = str(metadata.get("risk_level") or "low").strip()
    _enum_value(service_criticality, "service_criticality", {"low", "medium", "high", "critical"})
    _enum_value(data_sensitivity, "data_sensitivity", {"public", "internal", "confidential", "restricted"})
    _enum_value(security_access_level, "security_access_level", {"standard", "elevated", "privileged"})
    _enum_value(source_system, "source_system", {"local", "innohub", "databricks"})
    _enum_value(risk_level, "risk_level", {"low", "medium", "high"})
    target_users = metadata.get("target_users", [])
    if not isinstance(target_users, list) or not all(
        isinstance(item, str) and item.strip() for item in target_users
    ):
        raise ManifestError("spec.md target_users must be a string list.")
    success_criteria = metadata.get("success_criteria", {})
    if not isinstance(success_criteria, dict):
        raise ManifestError("spec.md success_criteria must be a mapping.")
    return AgentManifest(
        root=agent_root,
        agent_id=agent_id,
        name=_required_string(metadata, "name", "spec.md"),
        description=_required_string(metadata, "description", "spec.md"),
        schema_version=SPEC_SCHEMA_VERSION,
        agent_type=agent_type,
        service_criticality=service_criticality,
        data_sensitivity=data_sensitivity,
        security_access_level=security_access_level,
        required_uc_permissions=tuple(item.strip() for item in required_permissions),
        use_flagship_model=use_flagship_model,
        model=_required_string(metadata, "model", "spec.md"),
        owner=str(metadata.get("owner") or "").strip(),
        owner_group=str(metadata.get("owner_group") or "").strip(),
        source_system=source_system,
        target_users=tuple(item.strip() for item in target_users),
        cost_center=str(metadata.get("cost_center") or "").strip(),
        risk_level=risk_level,
        hitl_required=hitl_required,
        success_criteria=dict(success_criteria),
    )


def validate_agent(root: str | Path) -> AgentManifest:
    manifest = load_manifest(root)
    for name in ("README.md", "AGENTS.md", "spec.md"):
        path = manifest.root / name
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            raise ManifestError(f"Missing or empty Agent definition: {path}")

    skills = load_skills(manifest.root)
    if not skills:
        raise ManifestError(f"At least one Skill is required under {manifest.root / 'skills'}.")
    load_subagents(manifest.root, set(skills))
    _validate_source_tree(manifest.root)
    if not (manifest.root / "README.md").read_text(encoding="utf-8").lstrip().startswith("#"):
        raise ManifestError("README.md must start with a Markdown heading.")
    if not (manifest.root / "AGENTS.md").read_text(encoding="utf-8").lstrip().startswith("#"):
        raise ManifestError("AGENTS.md must start with a Markdown heading.")
    return manifest


def load_skills(root: str | Path) -> dict[str, SkillDefinition]:
    agent_root = Path(root).expanduser().resolve()
    skills_root = agent_root / "skills"
    if not skills_root.is_dir():
        return {}
    result: dict[str, SkillDefinition] = {}
    for skill_root in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        skill = validate_skill_directory(skill_root)
        if skill.name in result:
            raise ManifestError(f"Duplicate Skill name: {skill.name}")
        result[skill.name] = skill
    unsupported = sorted(path for path in skills_root.iterdir() if not path.is_dir())
    if unsupported:
        raise ManifestError(f"skills/ may contain only Skill folders: {unsupported[0]}")
    return result


def validate_skill_directory(root: str | Path) -> SkillDefinition:
    skill_root = Path(root).expanduser().resolve()
    entrypoint = skill_root / "SKILL.md"
    metadata, body = read_markdown_frontmatter(entrypoint, "SKILL.md")
    name = _required_string(metadata, "name", str(entrypoint))
    description = _required_string(metadata, "description", str(entrypoint))
    if len(name) > 64 or not AGENT_ID_RE.fullmatch(name) or "--" in name:
        raise ManifestError(f"Skill name must follow the Agent Skills naming rules: {entrypoint}")
    if name != skill_root.name:
        raise ManifestError(f"Skill name must match its folder '{skill_root.name}': {entrypoint}")
    if len(description) > 1024:
        raise ManifestError(f"Skill description must be at most 1024 characters: {entrypoint}")
    if not body.strip():
        raise ManifestError(f"Skill instructions are required after frontmatter: {entrypoint}")
    uc_mode = str(metadata.get("uc_mode") or "agent").strip()
    if uc_mode not in {"agent", "shared-reference"}:
        raise ManifestError(f"Skill uc_mode must be agent or shared-reference: {entrypoint}")
    uc_name = str(metadata.get("uc_name") or "").strip()
    if uc_mode == "shared-reference":
        parts = uc_name.split(".")
        if len(parts) != 3 or not all(parts):
            raise ManifestError(
                f"A shared-reference Skill requires uc_name=<catalog>.<schema>.<skill>: {entrypoint}"
            )
    elif uc_name:
        raise ManifestError(
            f"Agent-scoped Skills must not set uc_name; it is generated safely: {entrypoint}"
        )

    for path in sorted(skill_root.rglob("*")):
        if path.is_symlink():
            raise ManifestError(f"Symbolic links are not allowed in Skill bundles: {path}")
        if path.is_dir():
            if path != skill_root / "references":
                raise ManifestError(
                    f"1차 Skill은 references/ 외의 하위 폴더를 허용하지 않습니다: {path}"
                )
            continue
        relative = path.relative_to(skill_root)
        if relative == Path("SKILL.md"):
            continue
        if len(relative.parts) != 2 or relative.parts[0] != "references" or path.suffix != ".md":
            raise ManifestError(
                f"1차 Skill은 SKILL.md와 references/*.md만 허용합니다: {path}"
            )
    return SkillDefinition(name, description, skill_root, uc_mode, uc_name)


def _enum_value(value: str, key: str, allowed: set[str]) -> None:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ManifestError(f"spec.md {key} must be one of: {choices}.")


def load_subagents(
    root: str | Path, available_skills: set[str] | None = None
) -> dict[str, SubagentDefinition]:
    agent_root = Path(root).expanduser().resolve()
    agents_root = agent_root / "agents"
    if not agents_root.exists():
        return {}
    result: dict[str, SubagentDefinition] = {}
    for subagent_root in sorted(path for path in agents_root.iterdir() if path.is_dir()):
        definition = validate_subagent(subagent_root, available_skills)
        result[definition.name] = definition
    unsupported = sorted(path for path in agents_root.iterdir() if not path.is_dir())
    if unsupported:
        raise ManifestError(f"agents/ may contain only Sub-agent folders: {unsupported[0]}")
    return result


def validate_subagent(
    root: str | Path, available_skills: set[str] | None = None
) -> SubagentDefinition:
    subagent_root = Path(root).expanduser().resolve()
    path = subagent_root / "AGENTS.md"
    metadata, body = read_markdown_frontmatter(path, "Sub-agent AGENTS.md")
    name = _required_string(metadata, "name", str(path))
    if not AGENT_ID_RE.fullmatch(name) or "--" in name:
        raise ManifestError(f"Sub-agent name must use lower-case kebab-case: {path}")
    if name != subagent_root.name:
        raise ManifestError(f"Sub-agent name must match its folder '{subagent_root.name}': {path}")
    description = _required_string(metadata, "description", str(path))
    sandbox_mode = _required_string(metadata, "sandbox_mode", str(path))
    skills = metadata.get("skills", [])
    if not isinstance(skills, list) or not all(isinstance(item, str) for item in skills):
        raise ManifestError(f"Sub-agent skills must be a string list: {path}")
    if available_skills is not None:
        unknown = sorted(set(skills) - available_skills)
        if unknown:
            raise ManifestError(f"Sub-agent references unknown Skills: {', '.join(unknown)}")
    if not body.strip():
        raise ManifestError(f"Sub-agent instructions are empty: {path}")
    unsupported = [item for item in subagent_root.iterdir() if item.name != "AGENTS.md"]
    if unsupported:
        raise ManifestError(f"1차 Sub-agent 폴더에는 AGENTS.md만 허용합니다: {unsupported[0]}")
    return SubagentDefinition(
        name=name,
        description=description,
        sandbox_mode=sandbox_mode,
        skills=tuple(skills),
        instructions=body.strip(),
        root=subagent_root,
    )


def create_agent(agent_id: str, destination: str | Path | None = None) -> Path:
    if not AGENT_ID_RE.fullmatch(agent_id) or "--" in agent_id:
        raise ManifestError("agent_id must use lower-case kebab-case and be at most 64 characters.")
    root = Path(destination or agent_id).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ManifestError(f"Destination is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    name = agent_id.replace("-", " ").title()
    skill_root = root / "skills" / "request-structurer"
    reference_root = skill_root / "references"
    subagent_root = root / "agents" / "validator"
    reference_root.mkdir(parents=True)
    subagent_root.mkdir(parents=True)
    files = {
        root / "spec.md": SPEC_TEMPLATE.format(agent_id=agent_id, name=name),
        root / "AGENTS.md": AGENTS_TEMPLATE,
        root / "README.md": _agent_readme(agent_id),
        skill_root / "SKILL.md": EXAMPLE_SKILL,
        reference_root / "guidelines.md": EXAMPLE_REFERENCE,
        subagent_root / "AGENTS.md": EXAMPLE_SUBAGENT,
        root / ".gitignore": ".aops/\n.agents/\n.codex/\n",
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return root


def _agent_readme(agent_id: str) -> str:
    title = agent_id.replace("-", " ").title()
    return textwrap.dedent(
        f"""\
        # {title}

        이 폴더 하나가 Harness Agent의 개발·테스트·등록 단위입니다.

        ## 수정할 파일

        - `spec.md`: Agent ID, 설명, 모델과 운영 기준
        - `AGENTS.md`: Agent의 공통 행동과 실행 절차
        - `skills/*/SKILL.md`: 재사용 가능한 업무 절차
        - `skills/*/references/*.md`: Skill 참고자료
        - `agents/*/AGENTS.md`: 선택적인 Sub-agent 정의

        ## 실행

        ```bash
        aops validate
        codex
        ```

        ## 등록과 로드

        ```bash
        aops register . --version 1.0.0
        aops list
        aops load {agent_id} --version 1.0.0
        ```

        `.agents/`, `.codex/`와 `.aops/`는 `aops`가 생성하는 런타임 파일입니다.
        """
    )


def calculate_definition_checksums(root: str | Path) -> DefinitionChecksums:
    manifest = load_manifest(root)
    harness_paths = [
        manifest.root / "spec.md",
        manifest.root / "AGENTS.md",
        manifest.root / "README.md",
    ]
    agents_root = manifest.root / "agents"
    if agents_root.is_dir():
        harness_paths.extend(sorted(path for path in agents_root.rglob("*") if path.is_file()))
    harness = _hash_files(manifest.root, harness_paths)
    skills_root = manifest.root / "skills"
    skill_files = sorted(path for path in skills_root.rglob("*") if path.is_file())
    skills = {
        path.relative_to(manifest.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in skill_files
    }
    combined = hashlib.sha256(
        "\n".join(f"{name}:{checksum}" for name, checksum in skills.items()).encode("utf-8")
    ).hexdigest()
    return DefinitionChecksums(harness, skills, combined)


def read_release_metadata(root: str | Path) -> dict[str, Any] | None:
    path = Path(root).expanduser().resolve() / ".aops" / "release.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Invalid generated Release metadata {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"Generated Release metadata must be an object: {path}")
    return value


def read_markdown_frontmatter(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ManifestError(f"Missing {label}: {path}") from exc
    except OSError as exc:
        raise ManifestError(f"Cannot read {label} {path}: {exc}") from exc
    if not content.startswith("---\n"):
        raise ManifestError(f"{label} must start with YAML frontmatter: {path}")
    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ManifestError(f"{label} frontmatter is not closed: {path}")
    try:
        metadata = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid {label} frontmatter {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ManifestError(f"{label} frontmatter must be a YAML object: {path}")
    return metadata, parts[2]


def _validate_source_tree(root: Path) -> None:
    generated = {".aops", ".agents", ".codex", ".git", ".gitignore"}
    allowed_files = {"AGENTS.md", "README.md", "spec.md", ".gitignore"}
    for path in root.iterdir():
        if path.name in generated:
            continue
        if path.is_symlink():
            raise ManifestError(f"Symbolic links are not allowed in Agent source: {path}")
        if path.is_file() and path.name not in allowed_files:
            raise ManifestError(f"Agent 루트에는 표준 Markdown 파일만 허용합니다: {path}")
        if path.is_dir() and path.name not in {"skills", "agents"}:
            raise ManifestError(f"Agent 루트에는 skills/와 agents/만 허용합니다: {path}")


def _required_string(values: dict[str, Any], key: str, context: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{context} requires non-empty {key}.")
    return value.strip()


def _hash_files(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        if not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
