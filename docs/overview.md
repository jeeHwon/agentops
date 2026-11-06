# aops 개요

[README](../README.md) · [개요](overview.md) · [명령어](commands.md) · [프로젝트 폴더](project.md) · [레지스트리·승인](registry.md)

## 에이전트 3종

세 종류 에이전트를 같은 검증(Lakeflow Job), 같은 레지스트리(`agent_type`), 같은 승인·배포·운영 흐름으로 관리합니다.

| 종류 | 누가 | 만드는 것 | 배포·운영 |
| --- | --- | --- | --- |
| 스킬 기반 | 누구나 | 지시 · `SKILL.md` | Unity Gateway Skills에 게시 → 각자 하네스가 받아 씀 |
| 코드 기반 | AI 개발자 | LangGraph · OpenAI Agents SDK | Agent Runtime (AgentKit) |
| 노코드 | 데이터 실무자 | Genie Agent | 대상 워크스페이스의 Genie Agent |

- 세 종류 모두 aops가 관리합니다. 스킬·코드 에이전트는 aops 프로젝트에서 만들고 하네스나 `agentbricks dev`로 실행합니다.
- 스킬 기반은 서버로 띄우지 않습니다. 운영 지정 때 Unity Gateway Skills에 게시하고, 사용자가 각자 하네스(내 PC 또는 Sandbox)에서 `ug skills add`로 받아 씁니다. Agent Runtime에서는 코드 에이전트가 돕니다.
- 노코드 에이전트는 aops가 Genie API로 새로 만들거나, Genie 화면에서 만든 것을 목록에서 골라 가져옵니다.
- local 에디션은 스킬 기반만 씁니다. 코드·노코드는 Databricks가 필요해 dbx 에디션부터 씁니다.
- omni 에디션은 Omnigent UI에서 일합니다. aops는 화면·버튼을 Omnigent UI에 임베딩하거나, Omnigent 코드(Apache-2.0)를 확장해 넣습니다.
- custom 에디션은 조직이 만든 웹·서비스(Custom Front)에서 일합니다. Custom Front는 aops 서버(Databricks App)의 API에 연결만 합니다.

## 개발부터 운영까지

1~4단계는 자유롭게 쓰고, 5단계 검증부터 관리자 정책을 검사합니다. (dbx 에디션 기준)

| 단계 | 하는 일 | Databricks 기능 | 명령 |
| --- | --- | --- | --- |
| 1. 만들기 | 프로젝트·개발 화면 | Agent Bricks, Genie, Unity Gateway | `init` `dev` |
| 2. 실험 | 구성별 비교 실행 | MLflow tracing, Sandbox | `run` |
| 3. 세션 | 하네스 간 이어가기 | Agent sessions·memory | `session` |
| 4. 평가 | 채점·가드레일 시험 | MLflow 평가, 서비스 정책 | `evaluate` |
| 5. 검증 | 관리자 정책 검사 | Lakeflow Jobs | `validate` |
| 6. 등록 | 버전 등록 | Unity Catalog | `register` |
| 7. 배포 | 승인 후 배포 | aops 서버(console 화면), Agent Runtime, Genie | `deploy` |
| 8. 운영 | 운영 지정·모니터링 | Unity Gateway Skills, system tables, telemetry | `promote` `monitor` |

단계별로 쓰는 기능:

- **만들기:** Agent Bricks CLI·AgentKit(`DurableAgentServer`)으로 배포와 같은 에이전트 서버를 로컬에서 띄웁니다. 모델은 Unity Gateway, 도구는 Databricks 제공 MCP·UC function·Genie를 씁니다.
- **실험:** 실행마다 MLflow trace를 Unity Catalog에 남기고, Databricks Sandbox에서 격리해 실행할 수 있습니다.
- **세션:** 세션과 메모리를 Agent sessions·memory(Lakebase)에 저장해 다른 PC에서도 이어 갑니다.
- **평가:** `mlflow.genai.evaluate`, UC 평가 데이터셋, Unity Gateway 서비스 정책(가드레일)을 씁니다.
- **검증:** 후보 소스를 UC Volume에 올리고 Lakeflow Job(서버리스)이 검사합니다. 결과는 MLflow와 Delta 테이블에 남습니다.
- **등록:** UC registered model 버전을 만들고, 소스와 스킬의 스냅샷·해시를 남깁니다.
- **배포:** aops 서버(Databricks App)의 console 화면에서 승인하면 배포 Job이 코드 에이전트는 Agent Runtime(Databricks Apps)의 시험 앱에, 노코드는 대상 워크스페이스의 시험 Genie Agent로 배포합니다. 운영 지정(`promote`) 때 같은 스냅샷이 운영 쪽에 배포됩니다.
- **운영:** 운영 지정(`promote`) 때 스킬 기반은 Unity Gateway Skills에 게시됩니다. system tables(사용량·비용·감사), Apps telemetry, 예산 API로 운영 현황을 봅니다.

Agent Bricks CLI, Unity Gateway Skills, Agent sessions·memory, Sandbox, 서비스 정책, Apps telemetry는 Beta입니다. 꺼져 있으면 그 기능만 local 구현을 씁니다.

## 원칙

- **세션은 aops가 갖습니다.** 하네스를 바꾸면 인계 파일과 기록 조회 도구로 맥락을 이어 붙입니다.
- **폴더 하나가 프로젝트입니다.** 터미널에서 `claude`·`codex`를 직접 실행해도 같은 설정이 적용됩니다.
- **개발은 자유, 등록부터 검사합니다.** 기록·가드레일은 언제든 켜고 끌 수 있습니다.
- **Databricks 기능을 다시 만들지 않습니다.** 서버·저장·작업 실행·배포는 Databricks 기능을 그대로 씁니다.
- **Databricks 밖에 기대지 않습니다.** Databricks 기능만으로 동작하고, 외부 결재·Git 서비스 같은 조직 시스템은 선택 확장으로 붙입니다.

## 사용자

| 페르소나 | 하는 일 |
| --- | --- |
| 누구나 | 스킬 기반 에이전트 (`SKILL.md`, UC Skills) |
| AI 개발자 | 코드 기반 에이전트 (LangGraph, OpenAI Agents SDK), dbx 이상 |
| 데이터 실무자 | 노코드 에이전트 (Genie Agent), dbx 이상 |
| 관리자 | 정책, 배포 승인, 운영 모니터링 |

모두 CLI·개발 화면으로 쓰거나, 화면 하나(Omnigent UI 또는 Custom Front)를 골라 aops 서버로 씁니다.

## 에디션

dbx는 local을 포함합니다. omni와 custom은 dbx 위에 화면을 붙이는 두 방식이고, 같은 API를 써서 함께 쓸 수 있습니다.

| | local | dbx | omni | custom |
| --- | --- | --- | --- | --- |
| 대상 | 개인 | Databricks 조직 | Omnigent 조직 | 자체 웹·서비스(Custom Front)를 쓰는 조직 |
| 에이전트 | 스킬 기반 | 스킬 · 코드 · 노코드 | dbx와 같음 | dbx와 같음 |
| 화면 | aops 개발 화면 | aops 개발 화면, console 화면 | Omnigent UI + aops 화면·버튼 | Custom Front → aops API |
| 실행 | 내 PC | 내 PC, Sandbox | Omnigent 세션 | Sandbox |
| 모델 | 하네스 설정 그대로 | Unity Gateway | Unity Gateway | Unity Gateway |
| 기록 | 로컬 MLflow | Databricks MLflow | dbx + Omnigent | dbx와 같음 |
| 검증·배포 | 로컬 검사만 | Job 검증, 승인, 배포 | dbx와 같음 | dbx와 같음 |

### custom 에디션

- **서버:** `aops serve`가 dbx 기능을 FastAPI 서버로 엽니다. 기본은 `aops admin setup`이 배포하는 aops 서버(Databricks App)이고, 조직 서버나 컨테이너에서도 띄울 수 있습니다. Custom Front는 이 앱의 `CAN USE` 권한으로 붙습니다.
- **API:** REST `/api/v1`과 실행 스트림 SSE입니다. 스트림은 AG-UI 이벤트 형식이라 AG-UI 클라이언트로 바로 받을 수 있습니다. OpenAPI 문서(`/api/v1/openapi.json`)로 클라이언트 코드를 만듭니다.
- **범위:** 프로젝트, 실행·세션(하네스 전환·인계), 비교·평가, 등록·배포·승인, 운영 조회까지 CLI와 같은 기능입니다.
- **인증:** Custom Front는 Databricks OAuth 토큰(사람은 U2M, 서비스는 M2M)을 `Authorization: Bearer`로 보냅니다. 앱의 `CAN USE` 권한이 필요하고, aops는 따로 계정을 두지 않습니다.
- **실행:** 하네스는 세션마다 Databricks Sandbox에서 돕니다. 승인 규칙(승인 그룹, 자기 승인 금지)은 서버가 검사하므로 어떤 화면에서 눌러도 같습니다.
