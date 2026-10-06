from __future__ import annotations

import hashlib
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import yaml


AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


EXAMPLE_SKILL = """\
---
name: example-skill
description: 사용자 요청을 한 문장으로 요약하고 핵심 확인 사항과 다음 행동을 정리할 때 사용합니다.
---

# Example Skill

## 목적

사용자의 요청을 빠르게 구조화하여 바로 실행하거나 검토할 수 있는 형태로 정리합니다.

## 입력

- 사용자의 요청
- 사용자가 제공한 배경과 제약사항

## 수행 절차

1. 사용자의 요청을 한 문장으로 요약합니다.
2. 작업에 꼭 필요한 확인 사항만 식별합니다.
3. 실행할 다음 행동을 우선순위 순서로 정리합니다.

## 출력 형식

- 요청 요약
- 확인 사항
- 다음 행동

## 제약사항

- 제공되지 않은 사실은 추측하지 않습니다.
- 민감정보와 인증정보를 답변에 노출하지 않습니다.
"""


class ManifestError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentManifest:
    root: Path
    agent_id: str
    name: str
    description: str
    schema_version: int


@dataclass(frozen=True)
class DefinitionChecksums:
    harness: str
    skills: dict[str, str]
    combined_skills: str


def find_agent_root(start: str | Path) -> Path | None:
    path = Path(start).expanduser().resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / "agent.yaml").is_file():
            return candidate
    return None


def load_manifest(root: str | Path) -> AgentManifest:
    agent_root = Path(root).expanduser().resolve()
    target = agent_root / "agent.yaml"
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"Missing manifest: {target}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"Invalid manifest {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"Manifest {target} must be a YAML object.")
    schema_version = raw.get("schema_version")
    agent_id = str(raw.get("agent_id", "")).strip()
    name = str(raw.get("name", "")).strip()
    description = str(raw.get("description", "")).strip()
    if schema_version != 1:
        raise ManifestError("agent.yaml schema_version must be 1.")
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise ManifestError("agent_id must use lower-case kebab-case.")
    if not name:
        raise ManifestError("agent.yaml name is required.")
    if not description:
        raise ManifestError("agent.yaml description is required.")
    return AgentManifest(agent_root, agent_id, name, description, schema_version)


def validate_agent(root: str | Path) -> AgentManifest:
    manifest = load_manifest(root)
    for name in ("README.md", "spec.md", "AGENTS.md", "CLAUDE.md"):
        path = manifest.root / name
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            raise ManifestError(f"Missing or empty Agent definition: {path}")

    skills_root = manifest.root / ".agents" / "skills"
    skill_dirs = sorted(path for path in skills_root.glob("*") if path.is_dir())
    if not skill_dirs:
        raise ManifestError(f"At least one local Skill is required under {skills_root}.")
    for skill_dir in skill_dirs:
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            raise ManifestError(f"Missing Skill entrypoint: {skill_file}")
        _validate_skill(skill_file)
    release_path = manifest.root / "release.yaml"
    if release_path.exists():
        from .release import ReleaseError, load_release

        try:
            load_release(manifest.root, expected_agent_id=manifest.agent_id)
        except ReleaseError as exc:
            raise ManifestError(str(exc)) from exc
    return manifest


def create_agent(agent_id: str, destination: str | Path | None = None) -> Path:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise ManifestError("agent_id must use lower-case kebab-case.")
    root = Path(destination or agent_id).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ManifestError(f"Destination is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    name = agent_id.replace("-", " ").title()
    skill_dir = root / ".agents" / "skills" / "example-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    files: dict[Path, str] = {
        root / "agent.yaml": (
            "schema_version: 1\n"
            f"agent_id: {agent_id}\n"
            f"name: {name}\n"
            "description: 이 Agent가 해결하는 업무를 한 문장으로 작성하세요.\n"
        ),
        root / "README.md": _agent_readme(agent_id),
        root / "spec.md": _business_spec(),
        root / "AGENTS.md": (
            "# Agent Instructions\n\n"
            "## 공통 행동\n\n"
            "- `spec.md`의 업무 범위와 성공 기준을 따릅니다.\n"
            "- 사용자의 요청을 정확히 확인합니다.\n"
            "- 근거가 없는 내용은 추측하지 않습니다.\n"
            "- 필요한 경우에만 추가 정보를 질문합니다.\n\n"
            "## Skill 사용\n\n"
            "- 요청과 description이 일치하는 Skill만 사용합니다.\n"
            "- 여러 Skill이 관련되면 필요한 Skill만 순서대로 사용합니다.\n"
        ),
        root / "CLAUDE.md": (
            "# Compatibility Instructions\n\n"
            "이 파일은 다른 Harness Runtime과의 호환 지침이 필요한 경우 사용합니다.\n"
        ),
        skill_dir / "SKILL.md": EXAMPLE_SKILL,
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return root


def _agent_readme(agent_id: str) -> str:
    title = agent_id.replace("-", " ").title()
    return textwrap.dedent(
        f"""\
        # {title}

        이 폴더 하나가 로컬에서 개발하고 Registry에 버전으로 등록하는 Harness Agent입니다.
        사용자는 Markdown Harness와 Skill만 수정하고 기존 `codex` 명령으로 테스트합니다.

        ## 수정할 파일

        | 파일 | 용도 |
        |---|---|
        | `agent.yaml` | Agent 이름과 설명 |
        | `spec.md` | 목적, 범위, 입출력, 데이터 등급과 성공 기준 |
        | `AGENTS.md` | 모든 요청에 적용되는 공통 행동과 Skill 선택 원칙 |
        | `.agents/skills/*/SKILL.md` | 업무별 절차, 입력, 출력과 제약사항 |
        | `README.md` | 사용 예시와 운영 메모 |
        | `CLAUDE.md` | 선택적인 다른 Harness 호환 지침 |

        ## 개발과 검증

        ```bash
        codex
        aops validate
        ```

        새 Skill은 예제 폴더를 복사해 만듭니다. 폴더명과 `SKILL.md` frontmatter의
        `name`은 같은 kebab-case여야 합니다.

        ```bash
        cp -R .agents/skills/example-skill .agents/skills/customer-summary
        ```

        Registry에 등록하면 현재 폴더가 체크섬이 있는 불변 `tar.gz` 스냅샷이 됩니다.
        같은 Agent와 Version은 덮어쓰지 않으며, 수정본은 새 Version으로 등록합니다.

        ```bash
        aops register . --version 1.0.0 --profile <profile> --registry <catalog.schema>
        ```

        다른 Agent를 내려받을 때는 원격 버전을 명시합니다.

        ```bash
        aops load <agent-id> --version <version> --profile <profile> \\
          --registry <catalog.schema>
        ```

        Agent를 Databricks App이나 API로 공유하는 배포 과정은 별도 선택 단계이며,
        이 로컬 개발 폴더에는 AppKit 서버나 UI 코드를 포함하지 않습니다.
        """
    )


def _business_spec() -> str:
    return textwrap.dedent(
        """\
        ---
        agent_type: harness
        service_criticality: low
        data_sensitivity: internal
        security_access_level: standard
        required_uc_permissions: []
        use_flagship_model: false
        ---

        # Business Specification

        이 파일은 Agent의 업무 범위와 운영 기준을 정의합니다. 기본값을 실제 업무와 데이터에 맞게 수정하세요.

        ## 목적

        사용자의 업무 요청을 정의된 Skill 절차에 따라 처리하고 검토 가능한 결과를 반환합니다.

        ## 대상 사용자와 입력

        - 대상 사용자: 내부 업무 사용자
        - 필수 입력: 사용자의 업무 요청과 처리에 필요한 배경 정보
        - 허용하지 않는 입력: 인증정보, Secret 또는 승인되지 않은 민감정보 원문

        ## 출력

        - 요청에 대한 최종 결과
        - 판단 근거와 확인이 필요한 제한사항
        - 필요한 경우 사용자가 수행할 다음 행동

        ## 업무 범위

        - 포함: `.agents/skills`에 정의된 업무와 승인된 Tool 및 MCP를 사용하는 작업
        - 제외: 사용자에게 실제 권한이 없는 데이터 접근과 정의되지 않은 외부 시스템 변경

        ## 성공 기준

        - 사용자가 명시한 요청과 필수 출력 항목을 모두 충족합니다.
        - 근거가 없는 사실을 생성하지 않고 불확실한 내용은 명확히 표시합니다.
        - Secret과 개인정보를 출력하지 않습니다.
        - 정의된 출력 형식과 Skill 절차를 따릅니다.

        ## UC 필요 권한

        `required_uc_permissions`는 요구사항 선언이며 실제 권한을 부여하지 않습니다.
        UC 리소스를 사용하면 리소스, 권한과 목적을 명시합니다.

        ```yaml
        required_uc_permissions:
          - resource: catalog.schema.table
            privileges: [SELECT]
            purpose: 답변 생성에 필요한 기준 데이터를 조회합니다.
        ```

        ## Flagship 모델 사용 사유

        기본값은 `false`입니다. `true`로 변경하면 일반 모델로 충족하기 어려운 품질 기준을 작성합니다.
        """
    )


def _validate_skill(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"Cannot read Skill {path}: {exc}") from exc
    if not content.startswith("---\n"):
        raise ManifestError(f"Skill must start with YAML frontmatter: {path}")
    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ManifestError(f"Skill frontmatter is not closed: {path}")
    try:
        metadata = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid Skill frontmatter {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ManifestError(f"Skill frontmatter must be a YAML object: {path}")
    name = str(metadata.get("name", "")).strip()
    if not AGENT_ID_RE.fullmatch(name):
        raise ManifestError(f"Skill name must use lower-case kebab-case: {path}")
    if name != path.parent.name:
        raise ManifestError(f"Skill name must match its folder '{path.parent.name}': {path}")
    if not str(metadata.get("description", "")).strip():
        raise ManifestError(f"Skill description is required: {path}")
    if not parts[2].strip():
        raise ManifestError(f"Skill instructions are required after frontmatter: {path}")


def calculate_definition_checksums(root: str | Path) -> DefinitionChecksums:
    agent_root = Path(root).expanduser().resolve()
    harness_files = ["agent.yaml", "README.md", "spec.md", "AGENTS.md", "CLAUDE.md"]
    if (agent_root / "release.yaml").is_file():
        harness_files.append("release.yaml")
    harness_paths = [agent_root / name for name in harness_files]
    subagents_root = agent_root / "subagents"
    if subagents_root.is_dir():
        harness_paths.extend(sorted(path for path in subagents_root.rglob("*") if path.is_file()))
    harness = _hash_files(agent_root, harness_paths)
    skill_files = sorted(
        path for path in (agent_root / ".agents" / "skills").rglob("*") if path.is_file()
    )
    skills = {
        str(path.relative_to(agent_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in skill_files
    }
    combined = hashlib.sha256(
        "\n".join(f"{name}:{checksum}" for name, checksum in skills.items()).encode("utf-8")
    ).hexdigest()
    return DefinitionChecksums(harness, skills, combined)


def _hash_files(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()
