# aops API

[README](../README.md) · [개요](overview.md) · [명령어](commands.md) · [프로젝트 폴더](project.md) · [레지스트리·승인](registry.md) · [API](api.md)

`aops serve`가 여는 HTTP API입니다. Custom Front(custom), Omnigent UI, 개발 화면, console 화면이 모두 이 API를 씁니다. dbx 에디션의 명령과 1:1로 맞습니다. 서버를 띄우면 `/api/v1/docs`에서 요청·응답 형식을 보고 바로 호출해 볼 수 있습니다.

> 이 문서는 `scripts/api_docs.py`가 코드에서 만듭니다. 손으로 고치지 마세요.

## 규칙

- **경로:** 모두 `/api/v1` 아래입니다. OpenAPI 문서는 `/api/v1/openapi.json`입니다.
- **인증:** Databricks OAuth 토큰을 `Authorization: Bearer`로 보냅니다. 사람은 U2M, 서비스는 서비스 주체 M2M입니다.
- **오류:** 2xx가 아니면 본문은 `Problem`(RFC 9457)입니다. 화면은 `code`로 분기하고, `command`는 같은 일을 하는 CLI 명령입니다.
- **목록:** `Page`로 옵니다. `next_cursor`를 다음 요청의 `cursor`로 넘깁니다.
- **오래 걸리는 일:** 검증·비교 실행·평가는 `202`와 상태를 돌려주고, `…/events`(SSE)로 진행을 흘립니다.
- **실행 스트림:** 턴 실행 스트림은 AG-UI 이벤트입니다(`threadId`=세션, `runId`=턴). aops만의 정보는 `CUSTOM` 이벤트(`aops.handoff`, `aops.skill`, `aops.guardrail`, `aops.usage`)로 옵니다.
- **값:** 상태·종류 같은 열거형은 영문 코드입니다. 한글 표시는 화면이 붙입니다.
- **지금 상태:** 요청·응답 형식만 정해져 있고, 기능은 아직 `501`을 돌려줍니다.

## 명령 ↔ API

### 개발

| 명령 | API |
| --- | --- |
| `aops init` | `POST /api/v1/projects` |
| `aops init --type no-code` | `GET /api/v1/genie/spaces` |
| `aops dev` | 없음: 내 PC에 개발 화면을 여는 명령이다. 그 화면이 이 API를 쓴다 |
| `aops run "<질문>"` | `POST /api/v1/sessions/{session_id}/turns/stream` |
| `aops run "<질문>" --json` | `POST /api/v1/sessions/{session_id}/turns` |
| `aops run <질문 파일>` | `POST /api/v1/projects/{project_id}/batches` |
| `aops evaluate` | `POST /api/v1/projects/{project_id}/evaluations` |
| `aops session list` | `GET /api/v1/projects/{project_id}/sessions` |
| `aops session new` | `POST /api/v1/projects/{project_id}/sessions` |
| `aops session show` | `GET /api/v1/sessions/{session_id}` |
| `aops session switch --harness` | `POST /api/v1/sessions/{session_id}/switch` |
| `aops session import` | `POST /api/v1/projects/{project_id}/sessions/import` |
| `aops session export` | `GET /api/v1/sessions/{session_id}/export` |
| `aops memory list\|search` | `GET /api/v1/projects/{project_id}/memory` |
| `aops memory accept` | `POST /api/v1/projects/{project_id}/memory/{memory_id}/accept` |
| `aops memory forget` | `DELETE /api/v1/projects/{project_id}/memory/{memory_id}` |
| `aops add` | `POST /api/v1/projects/{project_id}/components` |
| `aops remove` | `DELETE /api/v1/projects/{project_id}/components/{kind}/{name}` |

### 등록·배포

| 명령 | API |
| --- | --- |
| `aops configure` | `POST /api/v1/projects/{project_id}/configure` |
| `aops validate` | `POST /api/v1/projects/{project_id}/validations` |
| `aops register` | `POST /api/v1/projects/{project_id}/registrations` |
| `aops deploy` | `POST /api/v1/projects/{project_id}/deploy-requests` |
| `aops promote` | `POST /api/v1/agents/{agent_id}/promote` |

### 운영·관리

| 명령 | API |
| --- | --- |
| `aops monitor` | `GET /api/v1/monitor/usage`<br>`GET /api/v1/monitor/budgets`<br>`GET /api/v1/monitor/costs`<br>`GET /api/v1/monitor/agents`<br>`GET /api/v1/monitor/quality`<br>`GET /api/v1/monitor/guardrails` |
| `aops monitor dashboard` | `POST /api/v1/monitor/dashboard` |
| `aops monitor --web` | 없음: console 화면을 브라우저로 여는 일이다 |
| `aops doctor` | `GET /api/v1/doctor` |
| `aops doctor --fix` | `POST /api/v1/doctor/fix` |
| `aops harness list` | `GET /api/v1/harnesses` |
| `aops harness use` | `PUT /api/v1/projects/{project_id}/harness` |
| `aops harness install` | 없음: 실행 파일을 그 PC에 설치하는 일이다. custom에서는 Sandbox 이미지로 관리한다 |
| `aops harness upgrade` | 없음: 실행 파일을 그 PC에서 올리는 일이다. custom에서는 Sandbox 이미지로 관리한다 |
| `aops admin setup` | `GET /api/v1/admin/setup` |
| `aops admin setup --apply` | `POST /api/v1/admin/setup` |
| `aops admin policy show` | `GET /api/v1/admin/policies`<br>`GET /api/v1/admin/policies/current` |
| `aops admin policy set` | `POST /api/v1/admin/policies` |
| `aops admin policy diff` | `GET /api/v1/admin/policies/diff` |
| `aops serve` | 없음: 이 API 서버를 띄우는 명령이다 |

## 화면·API 전용

명령은 없고 화면에서 쓰는 API입니다.

| API | 하는 일 | 화면 |
| --- | --- | --- |
| `GET /api/v1/projects` | 프로젝트 목록 | 프로젝트 선택 |
| `GET /api/v1/projects/{project_id}` | 프로젝트 보기 |  |
| `GET /api/v1/projects/{project_id}/config` | 설정 읽기 (aops.toml) | 개발 화면 설정 |
| `PATCH /api/v1/projects/{project_id}/config` | 설정 바꾸기 (가드레일·하네스·모델 스위치) | 개발 화면 스위치 |
| `GET /api/v1/projects/{project_id}/components` | 스킬·도구·MCP 목록 |  |
| `POST /api/v1/sessions/{session_id}/turns/{turn_id}/cancel` | 실행 중인 턴 멈추기 | 개발 화면 Chat [멈춤] |
| `GET /api/v1/sessions/{session_id}/turns/{turn_id}/tools` | 턴의 도구·스킬 사용 | 개발 화면 사용 칩 |
| `GET /api/v1/sessions/{session_id}/handoffs` | 인계 기록 | 개발 화면 세션 탭 |
| `GET /api/v1/projects/{project_id}/batches` | 비교 실행 목록 | 개발 화면 Batch |
| `GET /api/v1/batches/{batch_id}` | 비교 결과 (칸별) | 개발 화면 Batch |
| `GET /api/v1/batches/{batch_id}/events` | 비교 진행 (SSE) | 개발 화면 Batch 진행 표시 |
| `POST /api/v1/batches/{batch_id}/cancel` | 비교 실행 멈추기 | 개발 화면 Batch [멈춤] |
| `GET /api/v1/batches/{batch_id}/usage` | 질문별 스킬·도구 사용률 | 개발 화면 Batch 히트맵 |
| `GET /api/v1/projects/{project_id}/evaluations` | 평가 목록 | 개발 화면 Evaluate |
| `GET /api/v1/evaluations/{evaluation_id}` | 평가 결과 (점수표·실패 사례) | 개발 화면 Evaluate |
| `GET /api/v1/validations/{validation_run_id}` | 검증 결과 (단계별) | 검증 진행 화면 |
| `GET /api/v1/validations/{validation_run_id}/events` | 검증 진행 (SSE) | 검증 진행 화면 |
| `GET /api/v1/release-gates` | 배포 심사 목록 | console 배포 요청·승인 |
| `GET /api/v1/release-gates/{gate_id}` | 배포 심사 보기 | console 배포 요청·승인 |
| `POST /api/v1/release-gates/{gate_id}/approve` | 승인 | console [승인] |
| `POST /api/v1/release-gates/{gate_id}/reject` | 반려 | console [반려] |
| `POST /api/v1/release-gates/{gate_id}/cancel` | 요청 취소 (요청자) | console 배포 요청 |
| `POST /api/v1/approvals/external` | 외부 결재 결과 받기 (확장용) |  |
| `GET /api/v1/agents` | 에이전트 목록 (레지스트리) | console 레지스트리 |
| `GET /api/v1/agents/{agent_id}` | 에이전트 보기 | console 레지스트리 |
| `GET /api/v1/agents/{agent_id}/versions` | 버전 목록 | console 레지스트리 |
| `GET /api/v1/agents/{agent_id}/versions/{version}` | 버전 보기 (스킬 포함) | console 레지스트리 |
| `GET /api/v1/agents/{agent_id}/validation-runs` | 검증 이력 | console 레지스트리 |
| `GET /api/v1/agents/{agent_id}/deployments` | 배포 이력 | console 운영 지정 이력 |
| `GET /api/v1/deployments/{deployment_id}` | 배포 보기 |  |
| `GET /api/v1/agents/{agent_id}/production` | 운영 버전 | console 운영 지정 이력 |
| `GET /api/v1/agents/{agent_id}/events` | 수명주기 이력 (감사 로그) | console 운영 지정 이력 |
| `GET /api/v1/admin/rate-cards` | 모델 단가표 | console 관리자 · 단가표 |
| `PUT /api/v1/admin/rate-cards` | 모델 단가표 바꾸기 | console 관리자 · 단가표 |
| `GET /api/v1/meta/health` | 서버 상태 |  |
| `GET /api/v1/meta/version` | 버전 |  |
| `GET /api/v1/meta/me` | 내 계정·역할 | 모든 화면 (권한별 버튼 표시) |
| `GET /api/v1/meta/commands` | 명령 ↔ API 대응표 |  |
