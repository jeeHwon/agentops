# 레지스트리·승인 (dbx)

[README](../README.md) · [개요](overview.md) · [명령어](commands.md) · [프로젝트 폴더](project.md) · [레지스트리·승인](registry.md) · [API](api.md)

> **지금 동작하는 범위:** 스킬 기반. 정책은 `aops admin setup`의 옵션으로 정하고, 승인은 `aops gates`·`approve`·`reject`(aops 서버의 승인 API)로 합니다. console 화면, `aops admin policy`, 코드·노코드 경로는 설계입니다. 아래에서 **지금:**으로 표시한 줄이 현재 구현입니다.

## 관리자 정책

"조직에 배포하려면 지켜야 할 형식"입니다. Delta `policies`에 버전이 붙어 저장되고, validate Job·aops 서버·배포 Job이 같은 정책 버전을 읽습니다. 정책은 코드로만 검사합니다. 사람의 판단이 들어가는 곳은 승인 하나입니다.

| 항목 | 예 | 검사 시점 |
| --- | --- | --- |
| 필수 정보 | 소유 팀·담당자·데이터 등급·설명, 이름 규칙 | validate |
| 소스 | 등록된 버전, 금지 파일·비밀값 없음 | validate |
| 실행 구성 | 에이전트 서버 종류, 허용 하네스·SDK 버전 범위, 허용 Model service·MCP, 도구 권한 방식 | validate |
| 가드레일 | 데이터 등급별 필수 층·규칙·Gateway 정책, 레드팀 최소 통과율 | validate, 배포 Job |
| 평가 | 필수 평가 데이터셋, 최소 점수 | validate |
| 노코드 | Genie 정의 작성 규칙과 수준(오류·경고), 서비스 중요도별 벤치마크 정확도 기준 | validate |
| 운영 기록 | 배포 에이전트의 MLflow tracing·Apps telemetry | 배포 Job |
| 검증 방식 | `mandatory`(기본, 통과한 검사 필요)·`optional`(검사는 돌리고, 실패해도 결과를 남긴 채 승인자가 판단)·`exempt`(검사 없음) | deploy |
| 승인 | 승인 그룹, 필요한 승인 수, 자기 승인 금지, 승인 방식(`required` 기본·`optional`(SPEC 중요도 `low`이고 민감도 `public`·`internal`이면 자동, 아니면 승인자)·`auto`), 결재 채널(console 기본·외부 결재 확장) | deploy, console 화면 |
| 보안 등급 | 등급 목록, 등급별 공개 범위 | validate, promote |
| 재검증 | 정책이 바뀌면 이전 정책의 검사로 배포할 수 있는지 | deploy |

- 개발 단계의 기록·가드레일·평가 여부는 정책 항목이 될 수 없습니다. 정책은 등록·배포되는 결과물만 봅니다.
- 정책을 올리는 명령은 `aops admin policy set`입니다(설계).
- **지금:**
  - 정책은 `aops admin setup --validation-mode mandatory|optional|exempt --approval-mode required|optional|auto --approver-group <그룹>`으로 정합니다. 승인 수는 1명, 보안 등급은 `general`·`important`·`secret`입니다. 정책이 없으면 `mandatory`·`required`를 씁니다. 세 가지 검사 방식과 세 가지 승인 방식이 모두 위 표의 뜻대로 동작합니다.
  - 검사는 스킬 기반의 네 가지입니다: `format`(SPEC.md 필수 항목과 허용 값), `skills`(Agent Skills 형식, 이름 = 폴더 이름, description 1~1024자), `secrets`(토큰·키 패턴, `.env`·`*.pem`·`id_rsa`), `record`(볼륨의 zip이 등록 때 해시와 같은지). `init` 템플릿을 고치지 않은 채면(`my-skill`, 템플릿 description) 반려합니다.
  - 평가·가드레일·호환 검사, 보안 등급별 공개 범위, 재검증 규칙은 설계입니다.

## aops 서버

조직이 함께 쓰는 Databricks App 하나입니다. aops API(`/api/v1`)와 console 화면을 엽니다. `aops admin setup`이 배포하고, Omnigent UI와 Custom Front도 이 API에 붙습니다.

- **레지스트리:** 에이전트, 버전, 검사 결과, 배포 위치
- **배포 요청·승인:** 승인자에게만 [승인]·[반려] 버튼이 보입니다. 배포 승인·반려는 aops API의 승인 경로로만 하고, 기본 화면이 console 화면입니다. custom 에디션에서는 Custom Front가 같은 API를 부릅니다
- **모니터링:** `aops monitor`와 같은 데이터
- **배포 이력**
- 레지스트리 쓰기, Job 실행, UC Skill·권한 변경은 서버의 서비스 주체가 합니다. 사람은 요청만 하고, 레지스트리는 조회만 합니다.
- **지금:** API만 엽니다(경로 79개 중 14개 동작, [API](api.md)). console 화면과 모니터링은 아직 없어서, 승인자는 `aops gates`로 대기 요청을 보고 `aops approve`·`aops reject`로 결재합니다(같은 승인 API). 요청자 본인 승인·반려와 승인 그룹 밖의 결재는 서버가 막고, 요청자는 `aops deploy --cancel`로 취소합니다. 서버 코드는 `/Workspace/aops/server/<앱>`에 있고 워크스페이스 관리자만 고칠 수 있습니다.

## 권한과 가져오기

`promote`가 Databricks 권한을 주고, Databricks가 그 권한을 강제합니다. aops는 부여·회수 이력을 남깁니다.

| 종류 | `promote`가 주는 권한 | 가져오기 (`load`) |
| --- | --- | --- |
| 노코드 | 운영용 Genie Agent `CAN RUN` (공개 그룹은 배포 때 자동) | Genie Agent 링크 |
| 코드 | 운영 앱 `CAN USE` | 앱 링크 |
| 스킬 | UC Skill `READ_VOLUME` | 운영 버전을 내 PC 폴더로 |

스킬 기반은 시점마다 보이는 것이 다릅니다. (**지금 동작**)

| 시점 | 목록 (`list`) | 버전 zip | UC Skill | `load` |
| --- | --- | --- | --- | --- |
| `register` 뒤 | 조직 사용자 모두 | 서비스 주체만 (`load`로 담당자·담당 팀에게) | 아직 없음 | 담당자·담당 팀만 |
| `deploy` 뒤 | 같음 | 같음 | 처음 배포면 만들어지고(읽는 사람 없음), 늘 운영 버전 내용 | 담당자·담당 팀 |
| `promote` 뒤 | 같음 | 같음 | 대상자에게 `READ_VOLUME` | 대상자는 운영 버전만 |

- 대상 그룹은 계정 그룹이어야 합니다. 작업 공간 그룹에는 Unity Catalog 권한을 줄 수 없습니다.
- 버전 zip이 든 볼륨에는 운영 전 버전도 있어서 대상자에게 권한을 주지 않습니다. `load`는 aops 서버가 요청한 사람의 권한으로 UC Skill을 읽어 보고, 읽을 수 있으면 운영 버전 zip만 내줍니다. 그룹으로 받은 권한도 그대로 통합니다.
- 상위 객체의 권한은 아래로 내려옵니다. 레지스트리 스키마에는 사람에게 사용·조회 권한만 줍니다.

## 레지스트리 테이블

`[dbx] registry` 스키마의 Unity Catalog managed Delta 테이블 13개와 뷰 1개입니다. 정의는 [`bundles/registry/tables.sql`](../bundles/registry/tables.sql)에 있고, `aops admin setup`이 만듭니다. 모든 테이블·컬럼에 한글 설명이 있으며 허용 값은 CHECK 제약으로 막습니다. 쓰기는 aops 서비스 주체만 하고, 사람에게는 조회 권한만 줍니다.

| 테이블 | 내용 | 쓰는 곳 |
| --- | --- | --- |
| `policies` | 관리자 정책과 버전 | `aops admin setup` |
| `agents` | 등록된 에이전트와 SPEC 정보 (담당자·담당 팀·중요도·민감도). 다시 등록하면 새 SPEC 값으로 바뀌고, 담당자는 SPEC에 적혀 있을 때만 바뀜 | `register` (노코드는 배포 Job) |
| `agent_versions` | 저장된 버전, 버전 zip 경로·해시·크기, 상태(`registered`·`production`·`retired`) | `register`, 배포 Job |
| `agent_version_skills` | 버전마다 들어 있는 스킬과 UC Skill 이름(첫 배포 때 만들어짐)·해시 | `register` |
| `genie_agents` | 노코드: 개발용·운영용 Genie Agent와 에이전트 폴더 | 노코드 경로 (설계) |
| `validation_runs` | 검사 결과 (정책 버전, 검사별 결과, 실패 이유) | validate Job |
| `eval_results` | 평가 점수 | 평가 (설계) |
| `release_gates` | 배포 요청과 판정 (`validating` → `pending_approval` → `approved`·`rejected`·`canceled`). 승인 대기 이유는 `decision_reason` | `deploy`, aops 서버 |
| `approvals` | 승인·반려 이력 (추가만). 자동 승인은 `policy:auto` | aops 서버 |
| `deployments` | 배포 결과 (`running`·`succeeded`·`failed`) | aops 서버, 배포 Job |
| `agent_production` | 에이전트별 운영 버전과 이전 버전 | 배포 Job |
| `agent_events` | 감사 이력: `register`·`gate_requested`·`validate`·`gate_approved`·`gate_rejected`·`gate_canceled`·`deploy`·`rollback`·`promote`·`load` | 각 단계 |
| `rate_cards` | 모델별 단가 (비용 계산용) | 관리자 (설계) |
| `agent_status` (뷰) | 에이전트별 최신 버전, 운영 버전, 상태, 담당 (`aops list`가 읽음) | – |

- 여러 테이블을 함께 바꾸는 곳은 한 트랜잭션(`BEGIN ATOMIC … END`)으로 묶어, 모두 반영되거나 하나도 반영되지 않습니다. 레지스트리 테이블은 이를 위해 catalog-managed commit을 켜고 만듭니다(`delta.feature.catalogManaged`).
  - `register`: 에이전트 행, 버전, 스킬, 감사 기록. 실패하면 먼저 올린 버전 zip도 지웁니다.
  - 배포 Job의 운영 반영: 버전 상태(`production`·`retired`), 운영 포인터(`agent_production`), `agents.lifecycle`, 배포 결과, 감사 기록. 운영 버전이 세 곳에 있어도 서로 어긋나지 않습니다.
  - 배포 요청의 상태 변경은 조건부 `UPDATE`(앞 상태일 때만 바꿈)로 옮기고, 두 곳이 동시에 쓰다 부딪히면 다시 읽습니다. 사람의 승인·반려 이력은 상태를 옮기기 전에 먼저 남깁니다(필요한 승인 수를 셉니다).
