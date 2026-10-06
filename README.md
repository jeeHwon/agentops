# AgentOps

Markdown로 Agent를 개발하고, UC Skill 버전을 고정하고, Databricks Registry에 Agent
Release를 등록한 뒤 OAuth MCP App으로 배포하는 도구입니다. Agent를 내려받은 뒤 기존
`codex` 명령을 그대로 사용할 수 있습니다.

```text
Agent Release Manifest
        ↓
UC Skills 다운로드 및 SHA-256 검증
        ↓
AGENTS.md / skills / subagents / config.yaml 생성
        ↓
로컬 Harness에서 개발하거나 Databricks Apps MCP로 배포
```

- **Agent**는 개발·테스트·등록 단위입니다.
- **Skill**은 권한·재사용·감사 단위입니다.
- **`release.yaml`**은 Agent가 사용할 UC Skill의 이름, 버전과 해시를 고정합니다.

공통 Skill이 변경되어도 등록된 Agent에는 자동 반영하지 않습니다. 변경된 Skill은 새
이름과 버전으로 게시하고, 새 Agent Release에서 명시적으로 선택합니다.

## 1. 설치

Codex CLI, Databricks CLI, Python 3.11 이상과 `uv`가 필요합니다.

```bash
git clone https://github.com/jeeHwon/agentops.git
cd agentops
./install.sh
aops --version
```

## 2. Databricks 로그인

사용할 Workspace와 Profile을 직접 지정합니다.

```bash
databricks auth login \
  --host https://<workspace-host> \
  --profile <profile>

aops configure \
  --profile <profile> \
  --registry <catalog.schema>
```

`configure`에서 사용자가 선택한 Profile, Agent Registry와 기본 SQL Warehouse를 로컬
설정에 저장합니다. 이후 명령은 저장값을 사용하며 Workspace Profile을 임의로 선택하지
않습니다. 다른 환경을 일시적으로 사용할 때만 `--profile`, `--registry`,
`--warehouse-id`로 저장값을 덮어씁니다.

## 3. Agent 시작

새 Agent를 만듭니다.

```bash
aops init my-agent
cd my-agent
```

Registry Agent로 시작하려면 목록을 조회하고 특정 버전을 내려받습니다.

```bash
aops list

aops load <agent-id> \
  --version <version>
```

`--version`을 생략하면 최근 버전을 내려받습니다. `release.yaml`이 포함된 Agent는 UC
Skill을 검증하고 `.runtime/`까지 자동 생성합니다. 다운로드만 하려면
`--no-assemble`을 추가합니다.

## 4. Agent 개발

Agent 폴더에서 기존 Codex를 실행합니다.

```bash
codex
```

| 파일 | 용도 |
|---|---|
| `agent.yaml` | Agent 식별자, 이름과 설명 |
| `spec.md` | 목적, 범위, 입력·출력과 성공 기준 |
| `AGENTS.md` | 항상 적용할 역할과 행동 원칙 |
| `.agents/skills/*/SKILL.md` | 업무별 절차와 출력 형식 |
| `subagents/*.md` | 하위 Agent별 역할과 검증 절차 |
| `release.yaml` | Agent Release와 UC Skill 버전·해시 고정 |
| `CLAUDE.md` | 다른 Harness와의 선택적 호환 지침 |

새 Skill은 예제 폴더를 복사합니다. 폴더명과 `SKILL.md` frontmatter의 `name`은 같아야
합니다.

```bash
cp -R .agents/skills/example-skill .agents/skills/customer-summary
```

```yaml
---
name: customer-summary
description: 고객 정보를 요약해야 할 때 사용합니다.
---
```

## 5. UC Skill을 고정한 Release

실제 형식은 [`samples/release-agent`](samples/release-agent)를 참고합니다.

```yaml
schema_version: 1
agent_id: release-sample-agent
release_version: 1.0.0

profiles:
  model: standard-v1
  harness: omnigent-v1
  mcp_tools: readonly-v1

skills:
  - alias: release-summary
    uc_name: poc_catalog.agentops_test.release-summary-v1-0-0
    version: 1.0.0
    sha256: <Skill 폴더 전체의 SHA-256>
    source: .agents/skills/release-summary-v1-0-0

subagents:
  - id: validator
    instructions: subagents/validator.md
    skills: [release-validation]
```

Skill을 UC에 게시합니다.

```bash
aops publish .
```

`publish`는 다음 순서로 동작합니다.

1. 로컬 Skill 전체의 SHA-256이 `release.yaml`과 같은지 확인합니다.
2. 같은 이름의 UC Skill이 이미 있는지 모든 Skill을 먼저 확인합니다.
3. 새 UC Skill 객체를 만들고 파일을 업로드한 뒤 finalize합니다.
4. 기존 UC Skill은 수정하거나 덮어쓰지 않습니다.

Skill을 내려받아 실행 폴더를 조립합니다.

```bash
aops assemble .
```

기본 출력은 `.runtime/`입니다.

```text
.runtime/
├── AGENTS.md
├── spec.md
├── CLAUDE.md
├── config.yaml
├── skills/<alias>/SKILL.md
├── subagents/<id>.md
└── .agents/skills/<alias>/SKILL.md
```

각 UC Skill은 다운로드 직후 SHA-256을 검증합니다. 하나라도 다르면 기존 Runtime을
건드리지 않고 실패합니다. `config.yaml`에는 model·harness·MCP profile, subagent 구성,
UC Skill 버전과 내려받은 Markdown 경로가 자동으로 기록됩니다.

```bash
cd .runtime
codex
```

현재 단계에서 `profiles` 값은 Delta에 관리할 profile ID를 고정합니다. Omnigent와
FastMCP 실행기는 이 `config.yaml`을 공통 입력으로 사용하도록 연결합니다.

## 6. 검증

```bash
aops validate
```

다음을 확인합니다.

- `agent.yaml`과 필수 Markdown 파일
- Skill 이름, 폴더명, 설명과 본문
- `release.yaml`의 Agent ID, UC Skill 버전·해시와 subagent 참조

## 7. 새 버전 등록

```bash
aops register . \
  --version 1.0.0
```

등록된 버전은 수정하거나 덮어쓸 수 없습니다. `release.yaml`이 있으면 `--version`은
`release_version`과 같아야 합니다. Agent나 Skill을 변경하면 Skill 버전과 Agent
Release 버전을 올린 뒤 새로 등록합니다.

## 8. MCP App 배포

고정된 Agent Release를 Databricks Apps의 Streamable HTTP MCP 서버로 배포합니다.

```bash
aops deploy . \
  --model-endpoint <serving-endpoint>
```

저장된 Profile과 SQL Warehouse를 사용합니다. 기본 App
이름은 `mcp-<agent-id>`, 기본 MLflow Experiment는
`/Shared/agentops/<agent-id>`입니다. UC Trace 저장 위치는 `release.yaml`의 UC Skills가
공통으로 사용하는 `<catalog>.<schema>`에서 자동 추론합니다. 필요할 때만 다음 값을
명시합니다.

```bash
aops deploy . \
  --model-endpoint <serving-endpoint> \
  --app-name mcp-my-agent \
  --experiment /Shared/agentops/my-agent \
  --trace-schema <catalog.schema>
```

원격 App을 만들지 않고 생성물까지만 확인하려면 `--build-only`를 추가합니다. 생성물은
기본적으로 `.aops/apps/<app-name>/`에 만들어지며 직접 수정하지 않습니다.

`deploy`는 다음 순서로 동작합니다.

1. `release.yaml`을 읽고 UC Skills를 다시 다운로드합니다.
2. 각 Skill의 SHA-256을 고정된 값과 비교합니다.
3. `AGENTS.md`, `spec.md`, 개별 `SKILL.md`, subagent Markdown을 분리된 파일로 유지한 Runtime을 만듭니다.
4. FastAPI와 FastMCP 기반의 stateless Streamable HTTP 서버를 생성합니다.
5. Model Serving endpoint와 MLflow Experiment를 App 리소스로 연결합니다.
6. Databricks Bundle을 검증하고 App을 배포·시작합니다.
7. App 서비스 주체에 Experiment 전용 UC Trace 테이블의 최소 권한을 부여합니다.

App 서비스 주체에 부여하는 UC 권한은 다음으로 한정합니다.

- Trace catalog의 `USE CATALOG`
- Trace schema의 `USE SCHEMA`
- 해당 Experiment가 만든 `otel_spans`, `otel_logs`, `otel_metrics`, `otel_annotations` 테이블의 `SELECT`, `MODIFY`

Registry 테이블, Agent Artifact Volume과 업무 데이터 권한은 이 과정에서 부여하지
않습니다.

배포된 MCP 서버는 세 도구를 제공합니다.

| MCP Tool | 기능 |
|---|---|
| `health` | 서버와 Agent Release 상태 확인 |
| `agent_info` | Agent ID, Release, Harness profile, UC Skill 버전·해시 확인 |
| `ask_agent` | 고정 Harness와 Skills를 적용해 Model Serving으로 답변 생성 |

`ask_agent`는 호출자의 Databricks OAuth 토큰을 전달하는 OBO 방식입니다. 호출자는 App
접근 권한과 Model Serving endpoint의 `CAN_QUERY` 권한이 있어야 합니다. PAT가 아니라
OAuth를 지원하는 MCP Client에서 배포 결과의 URL을 등록합니다.

```text
https://<databricks-app-url>/mcp
```

각 `ask_agent` 호출은 MLflow에 다음 정보를 남깁니다.

- 사용자 입력과 Agent 응답
- 입력, 출력, 전체 Token
- 전체 지연시간과 Model Serving LLM span
- 성공, 실패와 오류
- Agent ID, Release Version, Harness·Model·MCP profile
- UC Skill 이름, 버전과 SHA-256
- 호출 사용자와 선택적 `session_id`

## Registry 관리자용 최초 설정

일반 사용자는 실행하지 않습니다. 관리자가 한 번만 실행합니다.

```bash
aops registry-init
```

```text
<catalog>.<schema>.agent_versions
└── Agent 버전, 소유자, 설명, Skill 목록과 체크섬을 저장하는 Delta Table

/Volumes/<catalog>/<schema>/agent_artifacts
└── Agent 버전별 불변 tar.gz 파일을 저장하는 UC Volume
```

## Codex 개발 과정 모니터링

```bash
aops configure \
  --profile <profile> \
  --registry <catalog.schema> \
  --experiment /Shared/codex-agentops
```

Agent 폴더에서 `codex`를 실행한 뒤 최초 한 번 `/hooks`에서 AgentOps Hook을 확인합니다.
각 Turn이 끝날 때 MLflow Trace, Token, Tool과 하위 Agent 정보가 비동기로 기록됩니다.

```bash
aops doctor --write-test-trace
aops status
aops flush
```

## 전체 사용 순서

```text
설치와 로그인
  → aops list와 aops load 또는 aops init
  → codex로 Harness와 Skill 개발
  → aops validate
  → aops publish로 불변 UC Skill 게시
  → aops assemble로 Runtime 검증
  → aops register로 새 Agent Release 등록
  → aops deploy로 Databricks Apps MCP 배포
  → OAuth MCP Client에서 /mcp 연결
  → MLflow에서 Trace와 Token 확인
```

배포는 선택 단계입니다. 로컬 개발과 Registry 등록은 App 없이도 사용할 수 있습니다.

## 개발 테스트

```bash
uv sync --dev
uv run pytest
```

## 라이선스

MIT
