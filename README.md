# AgentOps

Markdown로 Harness Agent를 개발하고, Unity Catalog Skill과 Agent Release를 불변 버전으로
관리하며, Databricks Apps의 OAuth MCP 서버로 배포하는 도구입니다. Agent 폴더에서 기존
`codex` 명령을 그대로 사용합니다.

```text
Agent Registry에서 Release 선택
        ↓
Agent Artifact 다운로드
        ↓
UC Skills 다운로드 및 SHA-256 검증
        ↓
Agent 루트의 .agents/skills 동기화
        ↓
codex로 즉시 실행·수정·검증
        ↓
새 Release 등록 또는 MCP App 배포(선택)
```

- **Agent**는 개발, 테스트와 등록의 단위입니다.
- **Skill**은 재사용 가능한 업무 절차이자 권한과 감사의 단위입니다.
- **Agent Release**는 Harness, Skill 조합, UC Skill 버전과 해시를 고정한 불변 스냅샷입니다.
- 공통 Skill이 바뀌어도 기존 Agent Release에는 자동 반영하지 않습니다.

## 1. 설치

Codex CLI, Databricks CLI, Python 3.11 이상과 `uv`가 필요합니다.

```bash
git clone https://github.com/jeeHwon/agentops.git
cd agentops
./install.sh
aops --version
```

`install.sh`는 로컬에 `aops` CLI와 Codex AgentOps Plugin을 설치합니다. Marketplace에
공개하거나 원격 Workspace를 변경하지 않습니다.

## 2. Databricks 설정

사용할 Workspace Profile은 사용자가 직접 선택합니다.

```bash
databricks auth login \
  --host https://<workspace-host> \
  --profile <profile>

aops configure \
  --profile <profile> \
  --registry <catalog.schema> \
  --experiment /Shared/agentops
```

`configure`는 선택한 Profile, Registry, SQL Warehouse와 MLflow Experiment를 로컬 설정에
저장합니다. 이후 `list`, `load`, `publish`, `register`, `deploy`는 이 기본값을 사용합니다.
다른 환경을 일시적으로 사용할 때만 해당 명령에 `--profile`, `--registry` 또는
`--warehouse-id`를 지정합니다.

## 3. Agent 시작

새 Agent를 만들려면 다음 명령을 실행합니다.

```bash
aops init my-agent
cd my-agent
codex
```

등록된 Agent로 시작하려면 목록을 보고 원하는 버전을 내려받습니다.

```bash
aops list
aops load <agent-id> --version <version>
cd <agent-id>
codex
```

`aops load`는 다음 작업을 자동으로 수행합니다.

1. Registry의 불변 Agent Artifact를 내려받아 해시를 검증합니다.
2. `agent.yaml`에 고정된 UC Skills를 현재 사용자 권한으로 내려받습니다.
3. 각 Skill의 SHA-256을 검증합니다.
4. 검증된 Skill을 Agent 루트의 `.agents/skills`에 원자적으로 동기화합니다.

따라서 `.runtime`으로 이동하거나 별도 Python 서버를 실행할 필요 없이 Agent 루트에서
바로 `codex`를 실행합니다.

## 4. 표준 Agent 폴더

```text
my-agent/
├── AGENTS.md
├── agent.yaml
├── README.md
├── .agents/
│   └── skills/
│       └── <skill-name>/
│           ├── SKILL.md
│           └── references, scripts, assets ...
└── .codex/
    ├── config.toml
    └── agents/
        └── <subagent-name>.toml
```

| 파일 | 담당 | 용도 |
|---|---|---|
| `AGENTS.md` | Agent 개발자 | 목적, 입력·출력, 범위, 성공 기준과 공통 행동을 정의합니다. |
| `.agents/skills/*/SKILL.md` | Agent 개발자 | 업무별 절차, 출력 형식과 제약사항을 정의합니다. |
| `.codex/agents/*.toml` | Agent 개발자 | 필요한 경우 프로젝트 전용 Sub-agent를 정의합니다. |
| `agent.yaml` | CLI 또는 플랫폼 | Agent 식별정보와 불변 Release 계약을 관리합니다. |
| `.codex/config.toml` | CLI 또는 플랫폼 | Codex의 프로젝트 실행 설정을 관리합니다. |
| `README.md` | CLI 또는 플랫폼 | Agent 사용법과 운영 메모를 제공합니다. |

`CLAUDE.md`, `spec.md`, `release.yaml`은 표준 구조에서 사용하지 않습니다. 기존
`spec.md`의 목적, 범위, 성공 기준과 거버넌스 항목은 `AGENTS.md`에 들어가고, 기존
`release.yaml`의 내용은 `agent.yaml`의 `release` 영역에 들어갑니다.

## 5. Skill 저장 위치

이 저장소의 Agent Skill은 다음 공식 Repository 위치에 저장합니다.

```text
<agent-root>/.agents/skills/<skill-name>/SKILL.md
```

Codex는 현재 디렉터리부터 Git Repository 루트까지의 `.agents/skills`를 탐색합니다.
Agent Release와 함께 Skill 버전을 고정해야 하므로 이 프로젝트에서는 Repository 위치를
사용합니다.

| 범위 | 공식 위치 | 용도 |
|---|---|---|
| Repository | `$REPO_ROOT/.agents/skills` | Agent와 함께 버전을 고정하고 공유하는 Skill |
| User | `$HOME/.agents/skills` | 여러 Repository에서 개인이 공통으로 사용하는 Skill |
| Admin | `/etc/codex/skills` | 조직이 머신 또는 컨테이너에 공통 제공하는 Skill |

공식 문서: [OpenAI Codex Skills](https://developers.openai.com/codex/skills)

새 Skill은 기존 템플릿을 복사해 만듭니다. 폴더명과 `SKILL.md` frontmatter의 `name`은
같은 kebab-case여야 합니다.

```bash
cp -R .agents/skills/example-skill .agents/skills/customer-summary
```

```yaml
---
name: customer-summary
description: 고객 정보를 요약해야 할 때 사용합니다.
---

# Customer Summary

## 입력

- 고객 정보

## 수행 절차

1. 핵심 사실을 확인합니다.
2. 결정 사항과 다음 행동을 구분합니다.

## 출력 형식

- 핵심 요약
- 다음 행동
```

Codex에서 `/skills`를 실행하면 발견된 Skill을 확인할 수 있습니다.

## 6. Harness와 Sub-agent 개발

Codex는 시작할 때 Agent 루트의 `AGENTS.md`를 자동으로 읽습니다. 목적, 대상 사용자와
입력, 출력, 업무 범위, 성공 기준과 공통 실행 절차를 이 파일에 작성합니다.

Sub-agent가 필요한 경우 `.codex/agents/<name>.toml`에 정의합니다.

```toml
name = "validator"
description = "결과의 근거와 누락을 독립적으로 검증할 때 사용합니다."
sandbox_mode = "read-only"
developer_instructions = """
AGENTS.md의 성공 기준에 따라 결과를 검증합니다.
문제와 수정 방향만 반환하고 파일은 수정하지 않습니다.
"""
```

Codex의 프로젝트 전용 Sub-agent 공식 위치는 `.codex/agents`입니다.
[OpenAI Codex Subagents](https://developers.openai.com/codex/multi-agent)에서 필드와 동작을
확인할 수 있습니다.

## 7. 검증

```bash
aops validate
```

다음을 확인합니다.

- `agent.yaml`, `README.md`, `AGENTS.md`의 필수 구조
- `AGENTS.md`의 거버넌스 frontmatter와 필수 항목
- Skill 폴더명, 이름, 설명과 본문
- `.codex/config.toml`과 Sub-agent TOML
- Release의 Agent ID, UC Skill 버전·해시와 Sub-agent 참조

## 8. 단일 Release 계약

Release 정보는 별도 `release.yaml`을 만들지 않고 `agent.yaml`에 통합합니다.

```yaml
schema_version: 2

agent:
  id: release-sample-agent
  name: Release Sample Agent
  description: 문서를 요약하고 결과를 검증하는 Agent입니다.

release:
  version: 3.0.0
  profiles:
    model: standard-v1
    harness: codex-v1
    mcp_tools: readonly-v1
  skills:
    - alias: release-summary
      uc_name: <catalog>.<schema>.release-summary-v3-0-0
      version: 3.0.0
      sha256: <Skill 폴더 전체의 SHA-256>
      source: .agents/skills/release-summary
  subagents:
    - id: validator
      instructions: .codex/agents/validator.toml
      skills: [release-summary]
```

`agent.yaml`은 상위 플랫폼이나 Release 관리 절차가 생성하는 시스템 파일을
전제로 합니다. CLI만 사용하는 관리자는 [`samples/release-agent`](samples/release-agent)를
복사해 값과 버전을 관리할 수 있습니다. Agent 개발자는 Harness와 Skill 내용에 집중합니다.

## 9. UC Skill 게시와 Agent 등록

```bash
aops publish .
aops assemble .
aops register . --version 3.0.0
```

`publish`는 로컬 Skill의 해시가 `agent.yaml`과 같은지 확인한 뒤 새 UC Skill 객체를
생성합니다. 이미 존재하는 UC Skill은 수정하거나 덮어쓰지 않습니다.

`assemble`은 UC Skills를 다시 내려받아 해시를 검증하고 배포용 Runtime을
`.aops/runtime`에 생성합니다. 이 Runtime은 배포 검증용 생성물이므로 로컬 Codex 개발에는
사용하지 않습니다.

`register`는 Agent 폴더를 체크섬이 있는 `tar.gz` Artifact로 만들어 UC Volume에 저장하고
메타데이터를 Delta Table에 기록합니다. 같은 Agent ID와 버전은 덮어쓸 수 없습니다.

## 10. MCP App 배포

배포는 선택 단계입니다.

```bash
aops deploy . \
  --model-endpoint <serving-endpoint>
```

기본 App 이름은 `mcp-<agent-id>`, 기본 MLflow Experiment는
`/Shared/agentops/<agent-id>`입니다. 필요할 때만 값을 덮어씁니다.

```bash
aops deploy . \
  --model-endpoint <serving-endpoint> \
  --app-name mcp-my-agent \
  --experiment /Shared/agentops/my-agent \
  --trace-schema <catalog.schema>
```

`--build-only`를 추가하면 원격 App을 만들지 않고 `.aops/apps/<app-name>` 생성물까지만
검증합니다. 배포된 Databricks App은 Streamable HTTP MCP 서버로 다음 도구를 제공합니다.

| MCP Tool | 기능 |
|---|---|
| `health` | 서버와 Agent Release 상태 확인 |
| `agent_info` | Agent ID, Release, Harness profile, UC Skill 버전·해시 확인 |
| `ask_agent` | 고정 Harness와 Skills를 적용해 Model Serving으로 답변 생성 |

`ask_agent`는 호출자의 Databricks OAuth 토큰을 전달하는 OBO 방식입니다. 각 호출은
MLflow에 입력, 응답, Token, 지연시간, 성공·실패, Agent Release와 Skill 버전 정보를
기록합니다.

## 11. Codex 개발 과정 모니터링

설치된 Plugin의 Turn 종료 Hook과 Codex OTel 이벤트를 사용합니다. Hook은 Turn 완료를
알리고, OTel 이벤트는 정확한 Token, Tool 호출과 하위 Agent 정보를 보완합니다. 전송은
로컬 Outbox를 거쳐 백그라운드에서 MLflow로 처리됩니다.

```bash
aops doctor --write-test-trace
aops status
aops flush
```

Agent 폴더에서 처음 `codex`를 실행한 뒤 `/hooks`에서 AgentOps Hook이 활성화되었는지
확인합니다.

## 12. Registry 관리자용 최초 설정

일반 Agent 개발자는 실행하지 않습니다. 관리자가 Registry마다 한 번 실행합니다.

```bash
aops registry-init
```

```text
<catalog>.<schema>.agent_versions
└── Agent 버전, 소유자, 설명, Skill 목록과 체크섬을 저장하는 Delta Table

/Volumes/<catalog>/<schema>/agent_artifacts
└── Agent 버전별 불변 tar.gz Artifact를 저장하는 UC Volume
```

## 개발 테스트

```bash
uv sync --dev
uv run pytest
```

## 라이선스

MIT
