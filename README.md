# AgentOps

Markdown으로 Harness Agent를 만들고, Unity Catalog에 불변 Release로 등록하며, 기존 `codex`에서 Turn 단위로 MLflow Trace와 평가 결과를 남기는 표준 구현입니다.

```text
Agent 폴더 작성
  → UC Skills 게시
  → Release Artifact를 UC Volume에 저장
  → Release 메타데이터를 Delta Registry에 등록
  → Agent 목록 조회·로드
  → Codex 또는 Omnigent에서 실행
  → 매 Turn MLflow Trace와 Scorer 결과 확인
```

Agent 자체를 MCP 서버로 배포하는 기능은 포함하지 않습니다. Web UI나 사내 플랫폼은 `AgentOpsService` Python API를 사용하고, 터미널 사용자는 `aops` CLI를 사용합니다.

## 설치

Codex CLI, Databricks CLI, Python 3.11 이상과 `uv`가 필요합니다.

```bash
git clone https://github.com/jeeHwon/agentops.git
cd agentops
./install.sh
aops --version
```

`install.sh`는 로컬 Python 도구와 `codex-agentops` Plugin을 Git 저장소에서 설치합니다. Marketplace 게시 과정은 없습니다.

## 최초 설정

Databricks CLI Profile은 사용자가 명시적으로 선택합니다.

```bash
databricks auth login \
  --host https://<workspace-host> \
  --profile <profile>

aops configure \
  --profile <profile> \
  --registry <catalog.schema> \
  --experiment /Shared/agentops
```

`configure`는 다음 작업을 한 번에 수행합니다.

1. 선택한 Profile과 SQL Warehouse 접근을 확인합니다.
2. MLflow Experiment가 없으면 생성하고 테스트 Trace를 기록합니다.
3. `RelevanceToQuery`, `Safety`, `Completeness` Scorer를 모든 `agent.turn` Trace에 100% 비동기로 적용합니다.
4. Codex OTel을 로컬 Collector에 연결해 정확한 Token과 Tool 실행시간을 보완합니다.
5. Profile, Registry, Experiment와 Warehouse를 사용자 설정에 저장합니다.

관리자는 Registry마다 한 번 초기화합니다.

```bash
aops registry-init
```

## 새 Agent 개발

```bash
aops init my-agent
cd my-agent
aops validate
codex
```

`aops init`이 만드는 공통 소스는 다음과 같습니다.

```text
my-agent/
├── AGENTS.md
├── README.md
├── spec.md
├── skills/
│   └── request-structurer/
│       ├── SKILL.md
│       └── references/
│           └── guidelines.md
└── agents/
    └── validator/
        └── AGENTS.md
```

사용자가 수정하는 파일은 위 Markdown 파일뿐입니다.

| 파일 | 작성 내용 |
|---|---|
| `spec.md` | Agent ID, Owner, 중요도, 민감도, 위험등급, 필요 권한, 모델과 구조화된 성공 기준 |
| `AGENTS.md` | Agent의 행동 원칙, 실행 순서, Skill과 Sub-agent 사용 규칙 |
| `README.md` | Agent 사용 방법과 예시 |
| `skills/<name>/SKILL.md` | 하나의 재사용 가능한 업무 능력과 수행 절차 |
| `skills/<name>/references/*.md` | Skill이 필요할 때 읽는 세부 기준과 참고자료 |
| `agents/<name>/AGENTS.md` | Sub-agent 역할, 위임 조건, 사용할 Skill과 제약사항 |

`.agents/`, `.codex/`, `.aops/`는 `aops`가 실행환경에 맞게 생성하므로 직접 수정하지 않습니다.

## Skill 작성 규칙

`skills/` 바로 아래 폴더 하나가 UC Skill 하나가 됩니다.

```text
skills/customer-summary/
├── SKILL.md
└── references/
    └── checklist.md
```

```markdown
---
name: customer-summary
description: 고객 자료에서 핵심 사실과 후속 행동을 요약해야 할 때 사용합니다.
---

# Customer Summary

1. 핵심 사실을 확인합니다.
2. 결정 사항과 후속 행동을 구분합니다.
```

- `name`과 `description`은 필수입니다.
- `name`은 폴더명과 같아야 하며 소문자, 숫자와 하이픈만 사용합니다.
- 1차 Harness 범위에서는 `SKILL.md`와 `references/*.md`만 허용합니다.
- 실행 코드, 바이너리와 심볼릭 링크는 허용하지 않습니다.
- 기본 Skill은 Agent 전용 UC 이름 `<agent-id>-<skill-name>`으로 게시되므로 다른 Agent의 같은 이름과 충돌하지 않습니다.

기존 조직 공용 UC Skill을 덮어쓰지 않고 참조하려면 `SKILL.md` frontmatter에 다음을 명시합니다.

```yaml
---
name: customer-summary
description: 고객 자료를 정해진 형식으로 요약할 때 사용합니다.
uc_mode: shared-reference
uc_name: <catalog>.<schema>.<existing-skill>
---
```

`shared-reference`는 기존 UC Skill의 접근권한과 존재만 확인하며 내용을 갱신하지 않습니다. Release Artifact에는 등록 시점의 로컬 Skill 스냅샷과 SHA가 함께 고정됩니다.

## Agent 등록과 로드

개발을 마치면 한 명령으로 UC Skills와 Agent Release를 등록합니다.

```bash
aops validate
aops register . --version 1.0.0
```

`register`는 구조 검증, UC Skill 생성 또는 갱신, Release manifest 생성, Artifact 압축·해시, UC Volume 업로드와 Delta Registry 등록을 순서대로 수행합니다. 같은 Agent ID와 버전은 덮어쓰지 않습니다.

```bash
aops list
aops list --all-versions
aops load <agent-id> --version <version>
cd <agent-id>
codex
```

`load`는 다음을 검증한 뒤 로컬 폴더와 Codex Runtime을 만듭니다.

1. UC Volume Artifact 전체 SHA-256
2. Artifact 내부 파일별 SHA-256
3. Registry 메타데이터와 Agent ID·버전
4. 현재 사용자의 각 UC Skill 접근 권한

`aops list`도 기본적으로 현재 사용자가 UC Skill 권한을 가진 Agent만 표시합니다. 관리자가 전체 행을 점검할 때만 `aops list --include-unavailable`을 사용합니다.

Release Artifact에는 등록 시점의 Harness, Skill과 Sub-agent 전체 스냅샷이 들어갑니다. 공통 UC Skill이 나중에 바뀌어도 기존 Release 내용은 바뀌지 않습니다.

## Registry 저장 구조

| 저장소 | 저장 내용 |
|---|---|
| Delta Table `agents` | Agent 현재 이름·Owner·중요도·민감도·위험등급·수명주기와 활성 버전 |
| Delta Table `agent_versions` | 불변 버전별 spec snapshot·모델·Artifact·해시·평가 연결·Release manifest |
| Delta Table `agent_version_skills` | Release를 구성하는 UC Skill 이름·해시·게시 방식 |
| Delta Table `release_gates` | 평가·보안·모니터링 Gate와 승인 증거 |
| Delta Table `agent_events` | publish·activate·rollback·deprecate·retire 이력 |
| UC Volume `agent_artifacts` | Harness, Skill, Sub-agent와 생성된 Release manifest를 포함한 불변 압축파일 |
| UC Skills | Skill 검색, 권한, 감사와 조직 공용 재사용에 쓰는 현재 Skill 객체 |

`aops registry-init`은 위 Registry 테이블과 Artifact Volume을 멱등하게 준비합니다.

## Codex 실행과 MLflow

Agent 루트에서 평소와 같이 `codex`를 실행합니다. Plugin Hook은 응답이 끝나는 `Stop` 이벤트에서 Turn을 완성하고, Codex OTel과 transcript 보정 결과를 로컬 Outbox에서 결합해 백그라운드로 MLflow에 전송합니다.

```text
UserPromptSubmit → 사용자 입력 저장
PostToolUse      → Tool 입력·출력 저장
SubagentStart/Stop → Sub-agent 계층과 결과 저장
Codex OTel       → 정확한 Token·Tool 시간 저장
Stop             → agent.turn 완성 및 즉시 업로드
SessionEnd       → 누락 Token 사후 보정과 재전송
```

각 `agent.turn`에는 Agent ID·Release, 입력·출력 원문, 성공 여부, 지연시간, 모델, Token, Tool, Sub-agent와 Skill 체크섬을 기록합니다. 1차 검증 범위에서는 샘플링과 마스킹을 적용하지 않습니다.

```bash
aops doctor --write-test-trace
aops status
aops flush
```

## Web UI와 자체 플랫폼 연동

CLI와 동일한 코어 로직은 `codex_agentops.service.AgentOpsService`로 제공합니다.

```python
from codex_agentops.service import AgentOpsService

service = AgentOpsService()
agents = service.list_agents()
path, release = service.load_agent("my-agent", version="1.0.0", target="./workspace")
```

플랫폼은 사용자별 작업 디렉터리를 만든 뒤 이 API로 Agent를 로드하고 해당 디렉터리에서 `codex` 세션을 실행하면 됩니다.

운영 Web 환경에서는 최종 사용자에게 공용 Artifact Volume 직접 읽기 권한을 주지 않고, 사용자 자격증명 또는 OBO로 UC Skill 권한을 먼저 확인하는 Loader Backend를 통해 Release를 전달해야 합니다.

Omnigent 실행은 별도 `agentops-omnigent` 어댑터를 사용합니다. 두 방식은 같은 Agent 소스, Registry, Release와 MLflow `agent.turn` 계약을 공유합니다.

## 예제와 테스트

두 Skill과 한 Sub-agent가 있는 예제는 [`samples/release-agent`](samples/release-agent)입니다.

```bash
uv sync --dev
uv run pytest
```

## 라이선스

MIT
