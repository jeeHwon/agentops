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
VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SPEC_SCHEMA_VERSION = 2
SKILL_BASED_AGENT_TYPE = "skill-based-agent"
SPEC_FIELDS = frozenset({
    "agent_id", "agent_name", "agent_version", "build_type", "serving_type",
    "origin", "owner", "owner_group", "service_criticality", "data_sensitivity",
    "security_level", "uc_permissions", "success_criteria", "flagship_model",
    "model_service",
})


class ManifestError(RuntimeError):
    pass


def is_os_metadata_file(path: Path) -> bool:
    return path.name == ".DS_Store" and path.is_file() and not path.is_symlink()


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[str, Any]:
    loader.flatten_mapping(node)
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ManifestError(f"Duplicate YAML field: {key}")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


@dataclass(frozen=True)
class AgentManifest:
    root: Path
    agent_id: str
    name: str
    description: str
    agent_version: str
    build_type: str
    serving_type: str
    origin: str
    service_criticality: str
    data_sensitivity: str
    security_level: str
    uc_permissions: tuple[dict[str, str], ...]
    flagship_model: bool
    model_service: str
    owner: str
    owner_group: str
    success_criteria: dict[str, Any]

    @property
    def schema_version(self) -> int:
        return SPEC_SCHEMA_VERSION

    @property
    def agent_type(self) -> str:
        return SKILL_BASED_AGENT_TYPE


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
agent_id: {agent_id}
agent_name: {name}
agent_version: "1.0.0"
build_type: skill
serving_type: apps
origin: databricks
owner: owner@example.com
owner_group: AI-CoE
service_criticality: 하
data_sensitivity: 내부
security_level: 일반
uc_permissions: []
success_criteria:
  correctness_min: 0.8
  relevance_min: 0.9
  safety_min: 1.0
  latency_p90_seconds: 10
flagship_model: false
model_service: system.ai.gpt-5-6-sol
---

# 에이전트 설계서 (SPEC)

## 1. 해결하려는 문제

사용자의 업무 요청을 정의된 Skill 절차로 처리합니다.

## 2. 사용자와 사용 시나리오

업무 담당자의 요청을 구조화하고 검증합니다.

## 3. 사용하는 데이터와 도구

필요한 데이터와 도구, UC 권한을 명시합니다.

## 4. 성공 기준과 평가 방법

성공 기준의 근거와 평가 데이터의 출처를 명시합니다.

## 5. 제약 사항과 가드레일

민감정보 처리 및 사람 승인 조건을 명시합니다.
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


def find_agent_root(start: str | Path) -> Path | None:
    path = Path(start).expanduser().resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / "AGENTS.md").is_file() and any(
            child.name == "SPEC.md" and child.is_file() for child in candidate.iterdir()
        ):
            return candidate
    return None


def load_manifest(root: str | Path) -> AgentManifest:
    agent_root = Path(root).expanduser().resolve()
    if not agent_root.is_dir():
        raise ManifestError(f"Agent source directory does not exist: {agent_root}")
    if not any(child.name == "SPEC.md" and child.is_file() for child in agent_root.iterdir()):
        raise ManifestError(f"Agent source requires the exact filename SPEC.md: {agent_root}")
    metadata, body = read_markdown_frontmatter(agent_root / "SPEC.md", "SPEC.md")
    if not body.strip():
        raise ManifestError(f"SPEC.md instructions are empty: {agent_root / 'SPEC.md'}")
    missing = SPEC_FIELDS - metadata.keys()
    unknown = metadata.keys() - SPEC_FIELDS
    if missing or unknown:
        raise ManifestError(
            f"SPEC.md fields mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}. "
            "Use the current SPEC.md template."
        )
    agent_id = _required_string(metadata, "agent_id", "SPEC.md")
    if not AGENT_ID_RE.fullmatch(agent_id) or "--" in agent_id:
        raise ManifestError("SPEC.md agent_id must use lower-case kebab-case and be at most 64 characters.")
    agent_version = _required_string(metadata, "agent_version", "SPEC.md")
    if not VERSION_RE.fullmatch(agent_version):
        raise ManifestError("SPEC.md agent_version must be major.minor.patch.")
    build_type = _required_string(metadata, "build_type", "SPEC.md")
    serving_type = _required_string(metadata, "serving_type", "SPEC.md")
    origin = _required_string(metadata, "origin", "SPEC.md")
    criticality = _required_string(metadata, "service_criticality", "SPEC.md")
    sensitivity = _required_string(metadata, "data_sensitivity", "SPEC.md")
    security_level = _required_string(metadata, "security_level", "SPEC.md")
    if build_type != "skill":
        raise ManifestError("SPEC.md build_type must be skill for Skill-based Agents.")
    _enum_value(serving_type, "serving_type", {"apps", "Apps", "SE", "se", "Job", "job"})
    _enum_value(origin, "origin", {"databricks", "innohub"})
    _enum_value(criticality, "service_criticality", {"상", "중", "하"})
    _enum_value(sensitivity, "data_sensitivity", {"공개", "내부", "대외비", "개인정보"})
    _enum_value(security_level, "security_level", {"일반", "강화"})
    permissions = metadata["uc_permissions"]
    if not isinstance(permissions, list) or any(
        not isinstance(item, dict)
        or set(item) != {"object", "privilege"}
        or not all(isinstance(item[key], str) and item[key].strip() for key in ("object", "privilege"))
        or len(item["object"].split(".")) != 3
        or not all(part.strip() for part in item["object"].split("."))
        for item in permissions
    ):
        raise ManifestError("SPEC.md uc_permissions must contain {object, privilege} entries with three-part objects.")
    flagship_model = metadata["flagship_model"]
    if not isinstance(flagship_model, bool):
        raise ManifestError("SPEC.md flagship_model must be true or false.")
    success_criteria = metadata["success_criteria"]
    expected_criteria = {"correctness_min", "relevance_min", "safety_min", "latency_p90_seconds"}
    if not isinstance(success_criteria, dict) or set(success_criteria) != expected_criteria:
        raise ManifestError("SPEC.md success_criteria must define correctness_min, relevance_min, safety_min, latency_p90_seconds.")
    for key in ("correctness_min", "relevance_min", "safety_min"):
        value = success_criteria[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ManifestError(f"SPEC.md success_criteria.{key} must be between 0 and 1.")
    latency = success_criteria["latency_p90_seconds"]
    if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not 0 < latency < float("inf"):
        raise ManifestError("SPEC.md success_criteria.latency_p90_seconds must be positive and finite.")
    owner = _required_string(metadata, "owner", "SPEC.md")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", owner):
        raise ManifestError("SPEC.md owner must be an email address.")
    return AgentManifest(
        root=agent_root,
        agent_id=agent_id,
        name=_required_string(metadata, "agent_name", "SPEC.md"),
        description=_spec_description(body),
        agent_version=agent_version,
        build_type=build_type,
        serving_type=serving_type,
        origin=origin,
        service_criticality=criticality,
        data_sensitivity=sensitivity,
        security_level=security_level,
        uc_permissions=tuple({key: item[key].strip() for key in ("object", "privilege")} for item in permissions),
        flagship_model=flagship_model,
        model_service=_required_string(metadata, "model_service", "SPEC.md"),
        owner=owner,
        owner_group=_required_string(metadata, "owner_group", "SPEC.md"),
        success_criteria=dict(success_criteria),
    )


def validate_agent(root: str | Path) -> AgentManifest:
    manifest = load_manifest(root)
    for name in ("README.md", "AGENTS.md", "SPEC.md"):
        path = manifest.root / name
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            raise ManifestError(f"Missing or empty Agent definition: {path}")

    skills = load_skills(manifest.root)
    if not skills:
        raise ManifestError(f"At least one Skill is required under {manifest.root / 'skills'}.")
    load_subagents(manifest.root, set(skills))
    _validate_source_tree(manifest.root)
    dataset = manifest.root / "eval" / "eval_dataset.json"
    if dataset.is_file():
        if dataset.stat().st_size > 10 * 1024 * 1024:
            raise ManifestError("eval/eval_dataset.json exceeds 10 MiB.")
        try:
            content = json.loads(dataset.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ManifestError(f"Invalid eval/eval_dataset.json: {exc}") from exc
        if not isinstance(content, (list, dict)):
            raise ManifestError("eval/eval_dataset.json must contain a JSON array or object.")
    if not (manifest.root / "README.md").read_text(encoding="utf-8").lstrip().startswith("#"):
        raise ManifestError("README.md must start with a Markdown heading.")
    if not (manifest.root / "AGENTS.md").read_text(encoding="utf-8").lstrip().startswith("#"):
        raise ManifestError("AGENTS.md must start with a Markdown heading.")
    return manifest


def require_source_distribution_allowed(data_sensitivity: str, security_level: str) -> None:
    if data_sensitivity not in {"공개", "내부"} or security_level != "일반":
        raise ManifestError(
            "대외비/개인정보 또는 강화 등급 Skill 원문은 이 로컬 복사형 실행·Registry 경로에 "
            "게시할 수 없습니다. 별도 서버 측 실행과 서비스 주체 권한이 필요합니다."
        )


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
    unsupported = sorted(path for path in skills_root.iterdir() if not path.is_dir() and not is_os_metadata_file(path))
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
        if is_os_metadata_file(path):
            continue
        if path.is_dir():
            if path != skill_root / "references":
                raise ManifestError(
                    f"Skill은 references/ 외의 하위 폴더를 허용하지 않습니다: {path}"
                )
            continue
        relative = path.relative_to(skill_root)
        if relative == Path("SKILL.md"):
            continue
        if len(relative.parts) != 2 or relative.parts[0] != "references" or path.suffix != ".md":
            raise ManifestError(
                f"Skill은 SKILL.md와 references/*.md만 허용합니다: {path}"
            )
    return SkillDefinition(name, description, skill_root, uc_mode, uc_name)


def _enum_value(value: str, key: str, allowed: set[str]) -> None:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ManifestError(f"SPEC.md {key} must be one of: {choices}.")


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
    unsupported = sorted(path for path in agents_root.iterdir() if not path.is_dir() and not is_os_metadata_file(path))
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
    unsupported = [item for item in subagent_root.iterdir() if item.name != "AGENTS.md" and not is_os_metadata_file(item)]
    if unsupported:
        raise ManifestError(f"Sub-agent 폴더에는 AGENTS.md만 허용합니다: {unsupported[0]}")
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
    reference_root.mkdir(parents=True)
    files = {
        root / "SPEC.md": SPEC_TEMPLATE.format(agent_id=agent_id, name=name),
        root / "AGENTS.md": AGENTS_TEMPLATE,
        root / "README.md": _agent_readme(agent_id),
        skill_root / "SKILL.md": EXAMPLE_SKILL,
        reference_root / "guidelines.md": EXAMPLE_REFERENCE,
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

        이 폴더 하나가 Skill-based Agent의 개발·테스트·등록 단위입니다.
        `request-structurer`는 입력·절차·출력 형식을 보여주는 기본 예시 Skill입니다.
        `SPEC.md`를 수정해도 Skill 내용이 자동으로 생성되지는 않습니다.

        ## 수정할 파일

        - `SPEC.md`: Agent ID, 설명, 모델과 운영 기준
        - `AGENTS.md`: Agent의 공통 행동과 실행 절차
        - `skills/*/SKILL.md`: 재사용 가능한 업무 절차
        - `skills/*/references/*.md`: Skill 참고자료
        - `agents/*/AGENTS.md`: 선택적인 Sub-agent 정의

        실제 업무에 맞게 `skills/request-structurer/`를 수정·복사·이름 변경하거나 새 Skill을 추가하세요.
        폴더명과 `SKILL.md`의 `name`을 일치시키고, `description`에는 사용 조건을 적습니다.
        기본 폴더에는 Sub-agent가 없습니다. 필요할 때만 `agents/<이름>/AGENTS.md`를 추가하세요.
        예를 들어 검증 담당자가 필요한 경우 `agents/validator/AGENTS.md`를 다음과 같이 만듭니다.
        `skills`에는 실제로 존재하는 Skill 폴더 이름을 적고, 루트 `AGENTS.md`에도 위임 조건을 작성하세요.

        ```markdown
        ---
        name: validator
        description: 결과의 누락과 요구사항 충족 여부를 독립적으로 확인할 때 사용합니다.
        sandbox_mode: read-only
        skills:
          - request-structurer
        ---

        # Validator

        결과의 누락과 오류를 확인하고 수정 방향을 제시합니다. 파일은 수정하지 않습니다.
        ```

        `sandbox_mode`는 부모 Codex 세션의 보안 설정을 강화하거나 재정의하지 않습니다.
        작성 후 `aops validate`를 실행하면 참조한 Skill을 확인하고 실행 구성을 재생성합니다.
        이전에 받은 폴더에 `agents/validator/`가 있다면 불필요할 때 삭제한 뒤 다시 검증하세요.
        이미 등록한 버전은 바뀌지 않으므로 수정한 내용을 게시하려면 SPEC.md와 `--version`을 함께 올립니다.

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

        `aops register`와 `aops list`는 Skill 원본 Registry를 사용합니다. 통합 포털의 개발 목록은
        별도 Delta Registry이므로 포털에서 **에이전트 등록 → Skill-based → Registry 릴리스 연결**로
        이미 게시한 ID·버전을 연결해야 나타납니다. 파일과 UC Skill을 다시 등록하는 것은 아닙니다.
        운영 목록은 이후 Gate 승인과 운영 지정이 필요합니다.

        `.agents/`, `.codex/`와 `.aops/`는 `aops`가 생성하는 런타임 파일입니다.
        """
    )


def calculate_definition_checksums(root: str | Path) -> DefinitionChecksums:
    manifest = load_manifest(root)
    harness_paths = [
        manifest.root / "SPEC.md",
        manifest.root / "AGENTS.md",
        manifest.root / "README.md",
    ]
    agents_root = manifest.root / "agents"
    if agents_root.is_dir():
        harness_paths.extend(sorted(path for path in agents_root.rglob("*") if path.is_file() and not is_os_metadata_file(path)))
    harness = _hash_files(manifest.root, harness_paths)
    skills_root = manifest.root / "skills"
    skill_files = sorted(path for path in skills_root.rglob("*") if path.is_file() and not is_os_metadata_file(path))
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
        metadata = yaml.load(parts[1], Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid {label} frontmatter {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ManifestError(f"{label} frontmatter must be a YAML object: {path}")
    return metadata, parts[2]


def _spec_description(body: str) -> str:
    description = ""
    for section in range(1, 6):
        match = re.search(
            rf"(?ms)^## {section}\. [^\n]+\n(.*?)(?=^## [1-5]\. |\Z)", body
        )
        if match is None:
            raise ManifestError(f"SPEC.md requires numbered section {section}.")
        content = next(
            (line.strip() for line in match.group(1).splitlines()
             if line.strip() and not line.lstrip().startswith("#")),
            "",
        )
        if not content:
            raise ManifestError(f"SPEC.md section {section} must not be empty.")
        if section == 1:
            description = content
    return description


def _validate_source_tree(root: Path) -> None:
    generated = {".aops", ".agents", ".codex", ".git", ".gitignore"}
    allowed_files = {"AGENTS.md", "README.md", "SPEC.md", ".gitignore"}
    for path in root.iterdir():
        if path.name in generated:
            continue
        if path.is_symlink():
            raise ManifestError(f"Symbolic links are not allowed in Agent source: {path}")
        if is_os_metadata_file(path):
            continue
        if path.is_file() and path.name not in allowed_files:
            raise ManifestError(f"Agent 루트에는 표준 Markdown 파일만 허용합니다: {path}")
        if path.is_dir() and path.name not in {"skills", "agents", "eval"}:
            raise ManifestError(f"Agent 루트에는 skills/, agents/와 eval/만 허용합니다: {path}")
        if path.is_dir() and path.name == "eval":
            if any(not is_os_metadata_file(item) and (item.is_symlink() or not item.is_file() or item.name != "eval_dataset.json")
                   for item in path.iterdir()):
                raise ManifestError(f"eval/에는 eval_dataset.json만 허용합니다: {path}")


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
