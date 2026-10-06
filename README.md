# AgentOps

Markdown로 Harness Agent를 개발하고, Unity Catalog Skill과 Agent Release를 불변 버전으로
관리하며, 필요할 때 Databricks Apps의 OAuth MCP 서버로 배포하는 도구입니다. Agent
폴더에서는 기존 `codex` 명령을 그대로 사용합니다.

```text
Agent Registry 조회
        ↓
Agent Artifact 다운로드 및 해시 검증
        ↓
agent.yaml의 UC Skill 참조 확인
        ↓
사용자 권한으로 UC Skills 다운로드 및 해시 검증
        ↓
Agent 루트에서 codex 실행
```

- **Agent**는 개발, 테스트와 등록의 단위입니다.
- **Skill**은 재사용, 권한과 감사의 단위입니다.
- **Agent Release**는 Harness와 UC Skill 조합을 버전과 해시로 고정한 불변 계약입니다.

## 설치

Codex CLI, Databricks CLI, Python 3.11 이상과 `uv`가 필요합니다.

```bash
git clone https://github.com/jeeHwon/agentops.git
cd agentops
./install.sh
aops --version
```

Plugin 이름은 `codex-agentops`, CLI 명령은 `aops`입니다.

## Databricks 설정

사용할 Workspace Profile과 Agent Registry를 한 번 설정합니다.

```bash
databricks auth login \
  --host https://<workspace-host> \
  --profile <profile>

aops configure \
  --profile <profile> \
  --registry <catalog.schema> \
  --experiment /Shared/agentops
```

이후 명령은 저장된 Profile, Registry와 SQL Warehouse를 사용합니다. 다른 환경을 사용할
때만 해당 명령에 `--profile`, `--registry` 또는 `--warehouse-id`를 지정합니다.

## Agent 시작

새 Agent를 만듭니다.

```bash
aops init my-agent
cd my-agent
codex
```

Registry Agent를 사용하려면 목록을 보고 원하는 버전을 내려받습니다.

```bash
aops list
aops load <agent-id> --version <version>
cd <agent-id>
codex
```

`aops load`는 Agent Artifact와 UC Skills를 각각 검증하고 로컬 Agent 폴더를 완성합니다.
별도 서버를 실행하거나 다른 디렉터리로 이동할 필요가 없습니다.

## 표준 Agent 폴더

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

Agent 개발자는 다음 파일만 수정합니다.

| 파일 | 용도 |
|---|---|
| `AGENTS.md` | 목적, 입력·출력, 범위, 성공 기준과 공통 행동 |
| `.agents/skills/*/SKILL.md` | 업무별 절차, 출력 형식과 제약사항 |
| `.codex/agents/*.toml` | 선택적인 프로젝트 전용 Sub-agent 역할과 지침 |

`agent.yaml`, `.codex/config.toml`, `README.md`는 CLI 또는 상위 플랫폼이 관리합니다.

## Skill 만들기

Agent와 함께 관리하는 Skill의 공식 Codex 위치는 다음과 같습니다.

```text
<agent-root>/.agents/skills/<skill-name>/SKILL.md
```

새 Skill은 기본 템플릿을 복사해 만듭니다. 폴더명과 `SKILL.md`의 `name`은 같은
kebab-case여야 합니다.

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

| 범위 | Codex 위치 |
|---|---|
| Agent Repository | `$REPO_ROOT/.agents/skills` |
| 개인 공용 | `$HOME/.agents/skills` |
| 관리자 공용 | `/etc/codex/skills` |

공식 문서: [OpenAI Codex Skills](https://developers.openai.com/codex/skills)

## 검증과 Release 등록

```bash
aops validate
```

`validate`는 Harness, Skill, Sub-agent와 Release 참조가 표준 구조를 따르는지 확인합니다.
Release 관리자는 다음 순서로 UC Skill과 Agent Release를 등록합니다.

```bash
aops publish .
aops assemble .
aops register . --version <version>
```

- `publish`: 로컬 Skill의 해시를 검증하고 새 UC Skill 객체를 생성합니다.
- `assemble`: UC Skills를 다시 내려받아 배포용 Runtime을 검증합니다.
- `register`: Harness Artifact를 UC Volume에 저장하고 검색 메타데이터를 Delta Table에 기록합니다.

UC Skill이나 Agent Release는 기존 버전을 덮어쓰지 않습니다. 변경 사항은 새 Release로
등록합니다. 완성된 예제는 [`samples/release-agent`](samples/release-agent)를 참고합니다.

## Registry 저장 구조

| 저장소 | 저장 내용 |
|---|---|
| Delta Table `agent_versions` | Agent ID, Release 버전, Artifact 경로·해시, Skill 이름·체크섬과 UC Skill 참조 메타데이터 |
| UC Volume `agent_artifacts` | `agent.yaml`, `AGENTS.md`, `README.md`, `.codex/`를 포함한 불변 Harness Artifact |
| UC Skills | 실제 `SKILL.md`와 Skill 부속 파일 |

Volume Artifact에는 UC Skill 내용을 중복 저장하지 않습니다. `aops load`는 Delta Table에서
Agent를 찾고 Volume에서 Harness를 받은 다음, `agent.yaml`에 고정된 UC Skills를 현재
사용자 권한으로 내려받아 `.agents/skills`에 조립합니다.

## MCP App 배포

배포는 선택 단계입니다.

```bash
aops deploy . \
  --model-endpoint <serving-endpoint>
```

배포된 Databricks App은 Streamable HTTP MCP 서버로 다음 도구를 제공합니다.

| MCP Tool | 기능 |
|---|---|
| `health` | 서버와 Agent Release 상태 확인 |
| `agent_info` | Agent ID, Release와 UC Skill 버전·해시 확인 |
| `ask_agent` | 고정 Harness와 Skills를 적용해 Model Serving으로 답변 생성 |

`ask_agent`는 호출자의 Databricks OAuth 토큰을 전달하는 OBO 방식을 사용하며 각 호출을
MLflow Trace로 기록합니다. 원격 배포 없이 생성물만 검증하려면 `--build-only`를 사용합니다.

App 이름은 항상 `mcp-`로 시작합니다. Databricks는 이 이름 규칙의 Apps MCP 서버를
Unity Gateway와 AI Playground에서 자동으로 검색하므로 별도의 UC Connection이나 외부
MCP Service를 만들 필요가 없습니다. 배포가 끝나면 `aops`가 `app:<app-name>` 형식의
Unity Gateway 식별자를 출력합니다. 로컬 코딩 에이전트에 연결할 때는 다음처럼 사용합니다.

```bash
ug mcp add --names "app:<app-name>"
```

## 모니터링 확인

Codex Turn 종료 Hook과 OTel 이벤트는 Token, Tool 호출, Sub-agent와 응답 정보를 로컬
Outbox를 거쳐 MLflow로 전송합니다.

```bash
aops doctor --write-test-trace
aops status
aops flush
```

## Registry 최초 준비

관리자가 Registry마다 한 번 실행합니다.

```bash
aops registry-init
```

이 명령은 `<catalog>.<schema>.agent_versions` Delta Table과
`/Volumes/<catalog>/<schema>/agent_artifacts` Volume을 준비합니다.

## 개발 테스트

```bash
uv sync --dev
uv run pytest
```

## 라이선스

MIT
