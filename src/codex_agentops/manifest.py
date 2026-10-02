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
    if schema_version != 1:
        raise ManifestError("agent.yaml schema_version must be 1.")
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise ManifestError("agent_id must use lower-case kebab-case.")
    if not name:
        raise ManifestError("agent.yaml name is required.")
    return AgentManifest(agent_root, agent_id, name, schema_version)


def validate_agent(root: str | Path) -> AgentManifest:
    manifest = load_manifest(root)
    skills_root = manifest.root / ".agents" / "skills"
    skill_dirs = sorted(path for path in skills_root.glob("*") if path.is_dir())
    if not skill_dirs:
        raise ManifestError(f"At least one local Skill is required under {skills_root}.")
    for skill_dir in skill_dirs:
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            raise ManifestError(f"Missing Skill entrypoint: {skill_file}")
        _validate_skill(skill_file)
    return manifest


def create_agent(agent_id: str, destination: str | Path | None = None) -> Path:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise ManifestError("agent_id must use lower-case kebab-case.")
    root = Path(destination or agent_id).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ManifestError(f"Destination is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    skill_dir = root / ".agents" / "skills" / "example-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    files: dict[Path, str] = {
        root / "agent.yaml": (
            "schema_version: 1\n"
            f"agent_id: {agent_id}\n"
            f"name: {agent_id.replace('-', ' ').title()}\n"
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
            "- 요청이 `example-skill`의 설명과 일치하면 해당 Skill을 사용합니다.\n"
            "- 새 Skill을 추가하면 사용 조건과 우선순위를 이 절에 작성합니다.\n"
        ),
        root / "CLAUDE.md": (
            "# Compatibility Instructions\n\n"
            "이 파일은 다른 Harness Runtime과의 호환 지침을 위해 유지합니다.\n"
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

        이 폴더는 Codex AgentOps 표준 Harness Agent입니다. 공통 실행·모니터링 코드는 Plugin이 담당하므로 이 폴더에서는 업무 정의와 Skill만 편집합니다.

        ## 작성 순서

        1. **`spec.md`에서 Agent의 업무와 운영 기준을 명시합니다.**
           - `agent_type`은 1차에서 `harness`를 유지합니다.
           - `service_criticality`에는 장애나 오답이 업무에 미치는 영향을 `low`, `medium`, `high` 중 하나로 기록합니다.
           - `data_sensitivity`에는 처리할 데이터의 최고 민감도를 `public`, `internal`, `confidential`, `restricted` 중 하나로 기록합니다.
           - `security_access_level`에는 필요한 통제 수준을 `standard`, `elevated`, `strict` 중 하나로 기록합니다.
           - `required_uc_permissions`에는 필요한 UC 리소스의 전체 이름과 권한을 기록합니다. 사용하지 않으면 기본값 `[]`를 유지합니다.
           - `use_flagship_model`에는 Flagship 모델이 반드시 필요하면 `true`, 일반 모델로 충분하면 `false`를 기록합니다.
           - 본문의 목적, 대상 사용자, 입력, 출력, 업무 범위와 성공 기준을 실제 업무에 맞게 수정합니다.
           - 기본값은 로컬 검증용 시작값이며 실제 보안 승인이나 UC 권한을 부여하지 않습니다.

        2. **`AGENTS.md`에서 모든 요청에 공통으로 적용할 Harness를 작성합니다.**
           - Agent의 역할과 답변 원칙, 금지사항, 불확실할 때의 행동을 작성합니다.
           - 어떤 요청에 어떤 Skill을 사용할지와 Skill 간 우선순위를 명시합니다.
           - 업무별 상세 절차는 이 파일에 반복해서 쓰지 않고 각 `SKILL.md`에 둡니다.

        3. **`.agents/skills/<skill-name>/SKILL.md`에서 업무 절차를 작성합니다.**
           - frontmatter의 `name`은 Skill 폴더명과 같은 kebab-case로 작성합니다.
           - `description`에는 Skill의 기능과 사용 시점을 한 문장으로 명시합니다.
           - 본문에는 입력, 단계별 수행 절차, 출력 형식과 제약사항을 작성합니다.
           - 처음에는 `example-skill`을 수정하고, 업무가 분리되어야 할 때만 새 Skill 폴더를 추가합니다.

        4. **`README.md`에는 사용 예시와 운영 메모를 남깁니다.**
           - 사용자가 입력할 대표 질문과 기대 결과를 예시로 작성합니다.
           - 알려진 제약과 사용하면 안 되는 상황, 담당자 메모를 추가합니다.

        5. **`CLAUDE.md`는 Claude Runtime 호환이 필요할 때만 수정합니다.**
           - Codex 전용 Agent라면 기본 안내문을 유지합니다.

        `agent.yaml`의 `agent_id`는 시스템 식별자이므로 생성 후 임의로 변경하지 않습니다.

        ## 검증하고 사용하기

        1. Agent 폴더에서 구조와 Skill을 검증합니다.

           ```bash
           codex-agentops validate
           ```

        2. 일반 Codex를 실행합니다.

           ```bash
           codex
           ```

        3. 최초 한 번 `/hooks`에서 `codex-agentops` Hook을 검토하고 신뢰합니다.
        4. 일반 업무 요청으로 전체 Agent 동작을 확인하고, 필요하면 `$example-skill`처럼 Skill을 명시해 개별 절차를 확인합니다.
        5. MLflow의 `agent.turn` Trace에서 입력, 최종 응답, 하위 Agent와 Tool Span, 정확한 Token을 확인합니다.
        6. `RelevanceToQuery`, `Safety`, `Completeness` Feedback을 확인합니다. 이 평가는 백그라운드에서 실행되므로 Trace보다 늦게 표시될 수 있습니다.
        7. 결과를 기준으로 `spec.md`, `AGENTS.md`, `SKILL.md`를 수정하고 같은 절차로 다시 테스트합니다.

        각 Turn은 종료 즉시 업로드되므로 Codex 세션을 종료할 필요가 없습니다. 전송 실패 상태는 `codex-agentops status`로 확인하고 `codex-agentops flush`로 다시 전송할 수 있습니다.

        ## 파일별 역할

        | 파일 | 수정 내용 |
        |---|---|
        | `spec.md` | Agent 유형, 중요도, 민감도, 접근 등급, UC 필요 권한, 모델 정책, 목적, 입력, 출력, 범위와 성공 기준 |
        | `AGENTS.md` | 모든 요청에 적용할 공통 행동과 Skill 선택 규칙 |
        | `.agents/skills/<skill-name>/SKILL.md` | 특정 업무의 절차, 출력 형식, 제약사항 |
        | `README.md` | 이 Agent의 사용 예시와 운영 메모 |
        | `CLAUDE.md` | Claude Runtime도 사용할 때 필요한 호환 지침 |
        | `agent.yaml` | Agent 식별자이며 생성 후 `agent_id`를 임의로 바꾸지 않음 |

        Python 서버나 모니터링 코드를 이 폴더에 추가할 필요가 없습니다.

        ## Skill 만들기

        처음에는 `.agents/skills/example-skill/SKILL.md`를 직접 수정합니다. Skill을 추가하려면 예제 폴더를 복사하고 폴더명과 frontmatter의 `name`을 같은 kebab-case 이름으로 변경합니다.

        ```bash
        cp -R .agents/skills/example-skill .agents/skills/customer-summary
        ```

        `description`에는 Skill의 기능과 사용 시점을 한 문장으로 작성합니다. Codex는 이 설명을 보고 어떤 Skill을 사용할지 결정합니다.

        ### Skill 템플릿

        ```markdown
        ---
        name: customer-summary
        description: 고객 상담 내용을 요약하고 후속 조치를 정리할 때 사용합니다.
        ---

        # Customer Summary

        ## 목적
        이 Skill이 해결하는 업무를 작성합니다.

        ## 입력
        - 필요한 입력과 전제조건을 작성합니다.

        ## 수행 절차
        1. 입력을 확인합니다.
        2. 업무 처리 절차를 순서대로 작성합니다.
        3. 결과를 지정된 형식으로 반환합니다.

        ## 출력 형식
        - 결과에 반드시 포함할 항목을 작성합니다.

        ## 제약사항
        - 금지사항, 보안 규칙, 데이터 사용 범위를 작성합니다.
        ```

        복사 후 다음 세 곳을 수정합니다.

        1. 폴더명: `.agents/skills/customer-summary`
        2. `SKILL.md`의 `name`과 `description`
        3. `AGENTS.md`의 Skill 사용 조건과 우선순위

        ## 기본 구조

        ```text
        {agent_id}/
        ├── agent.yaml
        ├── README.md
        ├── spec.md
        ├── AGENTS.md
        ├── CLAUDE.md
        └── .agents/skills/
            └── example-skill/
                └── SKILL.md
        ```
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

        이 파일은 Agent의 업무 범위와 운영 기준을 정의합니다. 위 값은 로컬 검증을 위한 기본값이므로 실제 업무와 데이터에 맞게 수정하세요.

        ## 분류 기준

        | 항목 | 기본값 | 선택 기준 |
        |---|---|---|
        | Agent 유형 | `harness` | 1차에서는 `harness`를 유지하고 향후 `code`, `no-code` 유형에서 변경합니다. |
        | 서비스 중요도 | `low` | 장애나 오답의 영향에 따라 `low`, `medium`, `high` 중 하나를 선택합니다. |
        | 데이터 민감도 | `internal` | 처리 가능한 최고 민감도를 `public`, `internal`, `confidential`, `restricted` 중 하나로 선택합니다. |
        | 보안·접근제어 등급 | `standard` | 필요한 통제 수준에 따라 `standard`, `elevated`, `strict` 중 하나를 선택합니다. |
        | UC 필요 권한 | `[]` | UC 리소스를 사용하지 않으면 유지하고, 사용하면 아래 형식으로 리소스와 권한을 추가합니다. |
        | Flagship 모델 사용 | `false` | 복잡한 추론 등으로 Flagship 모델이 반드시 필요할 때만 `true`로 변경하고 사유를 작성합니다. |

        분류값은 요구사항 선언이며 실제 UC 권한이나 보안 승인을 부여하지 않습니다. Runtime은 로그인한 사용자 또는 배포 시 OBO 주체의 실제 권한을 별도로 검증해야 합니다.

        UC 리소스가 필요한 경우 `required_uc_permissions`를 다음과 같이 수정합니다.

        ```yaml
        required_uc_permissions:
          - resource: catalog.schema.table
            privileges: [SELECT]
            purpose: 답변 생성에 필요한 기준 데이터를 조회합니다.
        ```

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
        - `Safety` 평가를 통과하고 Secret과 개인정보를 출력하지 않습니다.
        - 정의된 출력 형식과 Skill 절차를 따릅니다.
        - 업무별 정량 기준: 실제 업무에 맞는 목표값을 여기에 작성하세요.

        ## Flagship 모델 사용 사유

        기본값은 `false`입니다. `true`로 변경하는 경우 일반 모델로 충족하기 어려운 품질 기준과 필요한 사용 범위를 작성하세요.
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
    harness_files = ["README.md", "spec.md", "AGENTS.md", "CLAUDE.md"]
    harness = _hash_files(agent_root, [agent_root / name for name in harness_files])
    skill_files = sorted((agent_root / ".agents" / "skills").glob("*/SKILL.md"))
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
