# aops 개요

[README](../README.md) · [개요](overview.md) · [명령어](commands.md) · [프로젝트 폴더](project.md) · [레지스트리·승인](registry.md) · [API](api.md)

> **지금 동작하는 범위:** 스킬 기반 에이전트의 dbx 흐름(`init` → `run`·`session` → `register` → `deploy` → `list` → `promote` → `load`)입니다. 코드 기반(Agent Bricks)은 등록·검사·배포·권한·가져오기가 들어갔고 운영 실측 전입니다. 노코드는 만들기·점검·배포 요청·운영용 Genie 반영·권한·가져오기가 들어갔고 벤치마크 평가와 운영 실측 전입니다. 나머지는 설계이고, 표의 **지금** 열과 [명령어](commands.md)에 구분해 두었습니다.

## 에이전트 3종

세 종류 에이전트를 같은 레지스트리(`build_type`), 같은 검사(Lakeflow Job), 같은 승인·배포·운영 흐름으로 관리합니다.

| 종류 | 누가 | 만드는 곳 | 배포(`deploy`) | 쓰는 방법 (`promote` 뒤) |
| --- | --- | --- | --- | --- |
| 스킬 기반 | 누구나 | 내 PC의 에이전트 폴더: 지시 · `SKILL.md` | Unity Gateway Skills의 내용을 운영 버전으로 | `aops load`로 가져와 각자 하네스에서 씀 |
| 코드 기반 | AI 개발자 | 워크스페이스(Genie Code) 또는 내 PC: Agent Bricks (LangGraph · OpenAI Agents SDK) | Agent Runtime(Databricks Apps)의 운영 앱 | 앱 링크 |
| 노코드 | 데이터 실무자 | Genie 화면: aops가 만든 에이전트 폴더의 Genie Agent | 같은 워크스페이스의 운영용 Genie Agent | Genie Agent 링크 |

- 액션은 유형마다 같은 이름 8개입니다: `init`·`list`·`load`·`register`·`validate`·`evaluate`·`deploy`·`promote`.
- 스킬 기반은 서버로 띄우지 않습니다. 실행은 늘 사용자의 하네스(내 PC 또는 Sandbox)입니다. Agent Runtime에서는 코드 에이전트가 돕니다.
- 노코드는 Genie 화면에서 만들고 고칩니다. aops는 배포 요청 때 정의를 스냅샷으로 떠서 검사하고, 그 스냅샷으로 운영용을 만듭니다.
- local 에디션은 스킬 기반만 씁니다. 코드·노코드는 Databricks가 필요해 dbx 에디션부터 씁니다.
- omni 에디션은 Omnigent UI에서 일합니다. aops는 화면·버튼을 Omnigent UI에 임베딩하거나, Omnigent 코드(Apache-2.0)를 확장해 넣습니다.
- custom 에디션은 조직이 만든 웹·서비스(Custom Front)에서 일합니다. Custom Front는 aops 서버(Databricks App)의 API에 연결만 합니다.

## 개발부터 운영까지

1~5단계는 자유롭게 쓰고, 7단계 배포 요청부터 관리자 정책을 검사합니다. (dbx 에디션 기준)

| 단계 | 하는 일 | Databricks 기능 | 명령 | 지금 (스킬 기반) |
| --- | --- | --- | --- | --- |
| 1. 만들기 | 새 에이전트·개발 화면 | Agent Bricks, Genie, Unity Gateway | `init` `dev` | `init` 동작 (스킬 템플릿) |
| 2. 실험 | 구성별 비교 실행 | MLflow tracing, Sandbox | `run` | 질문 하나 실행, 턴마다 MLflow trace |
| 3. 세션 | 하네스 간 이어가기 | Agent sessions·memory | `session` | 하네스 전환·인계 (세션은 내 PC) |
| 4. 평가 | 채점·가드레일 시험 | MLflow 평가, 서비스 정책 | `evaluate` | 설계 |
| 5. 등록 | 버전 저장 | Unity Catalog (Volume, 모델) | `register` | 동작 (Volume zip, UC Skill) |
| 6. 검사 | 관리자 정책 자가 점검 | Lakeflow Jobs | `validate` | 동작 (검사 4가지) |
| 7. 배포 | 배포 요청 → 승인 → 운영 반영 | aops 서버(console 화면), Agent Runtime, Genie, Unity Gateway Skills | `deploy` | 동작 (승인: 정책 자동 또는 `aops approve`) |
| 8. 운영 | 권한 부여·가져오기·모니터링 | Unity Catalog 권한, system tables, telemetry | `promote` `load` `monitor` | `promote`·`load`·`list` 동작, `monitor` 설계 |

단계별로 쓰는 기능:

- **만들기:** Agent Bricks CLI·AgentKit(`DurableAgentServer`)으로 배포와 같은 에이전트 서버를 로컬에서 띄웁니다. 모델은 Unity Gateway, 도구는 Databricks 제공 MCP·UC function·Genie를 씁니다.
- **실험:** 실행마다 MLflow trace를 Unity Catalog에 남기고, Databricks Sandbox에서 격리해 실행할 수 있습니다. **지금:** `aops run`은 내 PC의 하네스로 돌고, trace는 내 MLflow 실험(`/Users/<나>/aops/<이름>`)에 턴마다 하나 남습니다(쓴 도구·스킬, 인계, 모델, 토큰, MLflow가 계산한 비용).
- **세션:** 세션과 메모리를 Agent sessions·memory(Lakebase)에 저장해 다른 PC에서도 이어 갑니다.
- **평가:** `mlflow.genai.evaluate`, UC 평가 데이터셋, Unity Gateway 서비스 정책(가드레일)을 씁니다.
- **등록:** 에이전트마다 UC Volume을 하나 두고 버전을 zip으로 한 번만 씁니다. 코드 에이전트는 UC registered model 버전도 만듭니다.
- **검사:** Lakeflow Job(서버리스)이 등록된 버전을 관리자 정책으로 검사합니다. 노코드는 Genie 벤치마크로 정확도와 회귀를 봅니다. 결과는 MLflow와 Delta 테이블에 남습니다. **지금:** validate Job(`aops-validate`)이 스킬 기반을 네 가지(형식·스킬·비밀값·기록), 코드 기반을 형식·프로젝트·비밀값·기록, 노코드 스냅샷을 형식·정의 규칙·비밀값·무결성(stale)·기록으로 검사하고 `validation_runs`와 Job 출력에 남깁니다.
- **배포:** 배포 요청은 검사 결과와 승인(console 화면)을 거칩니다. 그 뒤 배포 Job이 코드 에이전트는 운영 앱에, 노코드는 운영용 Genie Agent에, 스킬 기반은 Unity Gateway Skills의 내용에 반영합니다. 되돌리기는 이전 버전을 다시 배포합니다.
- **운영:** `promote`가 대상자에게 Databricks 권한을 줍니다. system tables(사용량·비용·감사·쿼리), Apps telemetry, 예산 API로 운영 현황을 봅니다.

Agent Bricks CLI, Unity Gateway Skills, Genie 벤치마크 API, Agent sessions·memory, Sandbox, 서비스 정책, Apps telemetry는 Beta입니다. 꺼져 있으면 그 기능만 local 구현을 쓰거나 건너뜁니다.

## 원칙

- **세션은 aops가 갖습니다.** 하네스를 바꾸면 인계 파일과 짧은 안내로 맥락을 이어 붙입니다.
- **폴더 하나가 프로젝트입니다.** 터미널에서 `claude`·`codex`를 직접 실행해도 같은 설정이 적용됩니다.
- **개발은 자유, 배포부터 강제합니다.** 기록·가드레일은 언제든 켜고 끌 수 있고, `register`는 버전을 저장할 뿐 막지 않습니다. 관리자 정책은 배포 요청 때 검사합니다.
- **Databricks 기능을 다시 만들지 않습니다.** 서버·저장·작업 실행·배포·권한은 Databricks 기능을 그대로 씁니다.
- **Databricks 밖에 기대지 않습니다.** Databricks 기능만으로 동작하고, 외부 결재·Git 서비스 같은 조직 시스템은 선택 확장으로 붙입니다. 워크스페이스마다 다른 점은 aops가 확인해 맞춥니다.

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
| 검사·배포 | 로컬 검사만 | Job 검사, 승인, 배포 | dbx와 같음 | dbx와 같음 |

### aops 서버

- dbx 이상은 조직에 aops 서버(Databricks App) 하나를 둡니다. 관리자가 `aops admin setup`으로 한 번 배포하고, 사용자는 CLI만 설치합니다.
- 레지스트리 기록, Job 실행, 권한 변경은 서버가 서비스 주체로 합니다. 그래서 사람이 기록을 고치거나 자기 요청을 승인할 수 없습니다. 승인 화면(console)도 이 서버에 있습니다. **지금:** 서버 주소의 console 화면에서 목록·등록·검사·배포·되돌리기·승인·반려·취소·권한·가져오기·정책을 합니다([console](console.md)). CLI의 `aops gates`·`approve`·`reject`도 같은 API입니다.
- 개발(`init`·`run`·`session`·`evaluate` 등)은 서버 없이 됩니다. 등록·검사·배포·권한 부여와 스킬 `load`는 서버를 거칩니다.

### custom 에디션

- **서버:** `aops serve`가 dbx 기능을 FastAPI 서버로 엽니다. 기본은 `aops admin setup`이 배포하는 aops 서버(Databricks App)이고, 조직 서버나 컨테이너에서도 띄울 수 있습니다. Custom Front는 이 앱의 `CAN USE` 권한으로 붙습니다.
- **API:** REST `/api/v1`과 실행 스트림 SSE입니다. 스트림은 AG-UI 이벤트 형식이라 AG-UI 클라이언트로 바로 받을 수 있습니다. OpenAPI 문서(`/api/v1/openapi.json`)로 클라이언트 코드를 만듭니다.
- **범위:** 프로젝트, 실행·세션(하네스 전환·인계), 비교·평가, 찾기·가져오기, 등록·배포·승인·권한, 운영 조회까지 CLI와 같은 기능입니다.
- **인증:** Custom Front는 Databricks OAuth 토큰(사람은 U2M, 서비스는 M2M)을 `Authorization: Bearer`로 보냅니다. 앱의 `CAN USE` 권한이 필요하고, aops는 따로 계정을 두지 않습니다.
- **실행:** 하네스는 세션마다 Databricks Sandbox에서 돕니다. 승인 규칙(승인 그룹, 자기 승인 금지)은 서버가 검사하므로 어떤 화면에서 눌러도 같습니다. 오래 걸리는 일은 시작만 하고 진행 스트림으로 받으며, 스트림이 끊기면 이어 받습니다.
