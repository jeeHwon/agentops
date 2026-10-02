# Codex AgentOps

Codex AgentOps는 일반 Codex CLI에서 실행되는 하네스 에이전트를 Turn 단위 MLflow Trace로 기록하는 범용 AgentOps 도구입니다. 사용자는 Agent의 지침과 Skill만 편집하며, 설치된 Plugin이 Trace, Token, 마스킹, 재시도와 백그라운드 품질 평가를 담당합니다.

## 주요 기능

- `AGENTS.md`, `spec.md`, 로컬 `SKILL.md` 기반 표준 하네스 에이전트 폴더 생성
- 별도의 Chat Wrapper 없이 기존 `codex` 명령 그대로 사용
- 완료된 Codex Turn마다 MLflow Root Trace 생성
- 지원되는 Tool, MCP, 하위 Agent를 부모·자식 Span으로 기록
- 입력, 출력, 캐시 입력, 캐시 생성, 추론 및 전체 Token의 정확한 집계
- `RelevanceToQuery`, `Safety`, `Completeness` 백그라운드 품질 평가
- Secret·개인정보 마스킹, 본문 15,000자 제한 및 SQLite Outbox 기반 재전송
- 모니터링 장애가 Codex 응답을 막지 않는 비동기 처리

## 사전 요구사항

- macOS 또는 Linux
- Python 3.11 이상
- Plugin과 Hook을 지원하는 [Codex CLI](https://learn.chatgpt.com/docs/hooks)
- 인증된 Profile이 하나 이상 있는 [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/)
- [`uv`](https://docs.astral.sh/uv/)
- 인증된 사용자가 접근할 수 있는 Databricks SQL Warehouse
- MLflow Experiment를 조회하고 생성할 수 있는 Databricks Workspace 권한

## 설치

```bash
git clone https://github.com/jeeHwon/agentops.git
cd agentops
./install.sh
```

설치 스크립트는 짧은 `aops` CLI와 호환용 `codex-agentops` 별칭을 설치하고 현재 저장소를 로컬 Codex Plugin Marketplace로 등록합니다.

## 설정

```bash
aops configure \
  --profile <databricks-profile> \
  --experiment /Shared/codex-agentops
```

특정 SQL Warehouse를 사용하려면 `--warehouse-id <id>`를 추가합니다. 생략하면 접근 가능한 첫 번째 Warehouse를 선택합니다. `--profile`을 생략하면 Profile 목록을 표시하고 사용자가 직접 선택하게 하며 임의로 자동 선택하지 않습니다.

설정 과정에서는 다음을 실제로 검증합니다.

- Databricks 인증
- SQL Warehouse 접근
- MLflow Experiment 조회 또는 생성
- Test Trace 기록 및 재조회
- Codex 설정 파일 구문
- 백그라운드 품질 Scorer 등록

기본 Experiment는 `/Shared/codex-agentops`입니다. Experiment가 없으면 `aops configure`가 생성하고, 이미 있으면 그대로 재사용합니다. Prompt, 응답 및 지원되는 Tool 본문을 저장하지 않으려면 `--no-content`를 사용합니다.

## Agent 생성과 테스트

```bash
aops init my-agent
cd my-agent
aops validate
codex
```

Codex에서 최초 한 번 `/hooks`를 열어 `codex-agentops` Hook을 검토하고 신뢰합니다. 이후에는 일반 `codex` 명령을 그대로 사용합니다. 각 `Stop` 또는 `Interrupt`에서 현재 Turn을 닫고 MLflow 업로드를 시작하므로 세션을 종료할 필요가 없습니다.

생성되는 폴더에는 사용자가 편집할 Agent 정의만 포함됩니다.

```text
my-agent/
├── agent.yaml
├── README.md
├── spec.md
├── AGENTS.md
├── CLAUDE.md
└── .agents/skills/
    └── example-skill/
        └── SKILL.md
```

`spec.md`, `AGENTS.md`, `SKILL.md`를 수정하여 업무 로직을 정의합니다. Runtime, 서버와 모니터링 코드는 설치된 공통 패키지와 Plugin에 유지됩니다.

## 구조 검증과 진단

`validate`는 Agent Manifest와 로컬 Skill의 최소 구조를 정적으로 검사합니다.

```bash
aops validate [agent-folder]
```

검사 항목은 다음과 같습니다.

- `agent.yaml`의 존재, YAML 형식, Schema Version, Agent ID와 이름
- `.agents/skills` 아래 최소 한 개의 Skill 존재
- 각 Skill 폴더의 `SKILL.md` 존재
- Skill frontmatter의 이름, 설명과 폴더명 일치
- 비어 있지 않은 Skill 본문

`doctor`는 설치, Plugin, Hook, Databricks 인증, MLflow, OTel과 로컬 Outbox 상태를 확인합니다.

```bash
aops doctor --write-test-trace
aops status
aops flush
```

## MLflow Trace 구조

완료된 사용자 Turn은 `agent.turn` Root Trace가 됩니다. 지원되는 하위 Agent는 `AGENT` Span으로 기록되고, 해당 Agent가 실행한 Tool과 MCP는 그 아래의 `TOOL` Span으로 연결됩니다.

Root Token에는 Root Turn과 완료된 하위 Agent의 Token이 합산됩니다. 각 하위 Agent Span에는 해당 Agent의 개별 Token도 기록됩니다.

Prompt, 응답과 지원되는 Tool 본문은 안정적인 Hook Payload에서만 읽습니다. Transcript에서 본문을 재구성하지 않으며, 정확한 Token 보강을 위해 버전이 명시된 `token_usage_record`만 제한적으로 읽습니다.

로컬 Collector가 Codex OTel 로그를 받으면 `response.completed`에서 Token을, `codex.tool_result`에서 실제 Tool 실행시간과 성공 여부를 수집합니다. 기존 외부 OTel Exporter가 있으면 설정을 변경하지 않으며, 동일 이벤트가 Codex AgentOps에도 전달되지 않는 경우 정확한 Tool 실행시간은 기록되지 않습니다. `doctor`가 이 상태를 경고합니다.

Hosted `WebSearch`와 같이 로컬 Function Tool Hook 경로를 사용하지 않는 Tool은 Tool Span으로 기록되지 않습니다. 자세한 내용은 공식 [Codex Hooks 문서](https://learn.chatgpt.com/docs/hooks)와 [Codex OTel 설정 문서](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry)를 참고하세요.

## 2차 구현 범위

2차에서는 대규모 운영을 위해 Unity Catalog 기반 Trace 저장소를 선택적으로 지원합니다.

- `aops configure`에서 Trace를 저장할 UC Catalog와 Schema를 명시할 수 있어야 합니다.
- 지원되는 Workspace에서는 MLflow Experiment를 UC Schema와 연결해야 합니다.
- 연결 과정에서 `mlflow_experiment_trace_otel_spans`, `mlflow_experiment_trace_otel_logs`, `mlflow_experiment_trace_otel_metrics` 테이블을 생성하고 검증해야 합니다.
- 설정 전에 UC Trace 저장 기능의 Cloud, Region 및 Preview 지원 여부를 확인해야 합니다.
- 사용자 또는 Service Principal의 `USE CATALOG`, `USE SCHEMA`, `SELECT`, `MODIFY` 권한을 검증해야 합니다.
- UC 연결을 사용하지 않는 환경에서는 기존 Workspace 관리형 Trace 저장소를 계속 사용할 수 있어야 합니다.
- 기존 Experiment를 UC Schema에 연결하면 이전 Workspace 관리형 Trace가 화면에서 숨겨질 수 있음을 설정 전에 안내해야 합니다.
- 운영 환경에서는 Trace와 LLM Scorer의 샘플링 비율을 분리하여 설정할 수 있어야 합니다.
- 5,000명 규모를 가정한 동시 업로드, Outbox 적체, Scorer 처리 지연과 비용 부하 테스트를 수행해야 합니다.

## 개인정보와 보안

본문 저장은 기본으로 활성화되며 `configure --no-content`로 비활성화할 수 있습니다. 저장 전 일반적인 Secret, 이메일, 전화번호와 식별번호 패턴을 마스킹하고 각 본문을 15,000자로 제한합니다. 규제 대상 데이터나 고도로 민감한 데이터를 사용하기 전에 조직의 보안 기준에 맞게 정책을 검토하세요.

로컬 설정과 Outbox는 운영체제의 XDG Config 및 Data 디렉터리에 저장됩니다. `CODEX_AGENTOPS_CONFIG_DIR`, `CODEX_AGENTOPS_DATA_DIR` 환경변수로 경로를 변경할 수 있습니다.

## 업데이트와 제거

```bash
./scripts/update.sh
./scripts/uninstall.sh
```

제거 시 CLI, Plugin, Marketplace 등록과 관리되는 OTel 설정 블록을 삭제합니다. 사용자 설정과 전송 대기 중인 로컬 Telemetry 데이터는 보존합니다.

## 개발 및 테스트

```bash
uv sync --dev
uv run pytest
```

## 라이선스

MIT
