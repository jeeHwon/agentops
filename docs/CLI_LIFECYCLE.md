# 통합 aops 수명주기

포털·별도 API 서버 없이 Skill, Code, No-code를 한 Registry에서 관리합니다.

| 유형 | 생성 | 실행·배포 |
| --- | --- | --- |
| Skill | `aops init my-agent` | `aops run`으로 Codex 실행, UC Skills 게시 |
| Code | `aops init my-agent --type code --framework langgraph` | 공식 Agent Bricks CLI 위임 |
| No-code | `aops init my-agent --type no-code` | Genie Space 및 Conversation API |

공통 `SPEC.md`의 15개 필드는 유지합니다. Code와 Genie의 `aops.yaml`에는 framework, 보관할 `source_paths`, 유형별 원격 리소스만 둡니다. 공통 환경을 반복 작성하지 않습니다. 등록 버전은 SPEC의 `agent_version`과 같아야 합니다.

## 환경

세 유형 모두 환경당 최초 한 번 설정합니다. Profile은 사용자가 선택하며, 이후 SDK·네이티브 CLI에도 선택한 Profile을 명시적으로 전달합니다.

```bash
aops configure --profile <profile> --registry <catalog.schema> \
  --experiment /Shared/agentops --warehouse-id <warehouse-id>
aops registry-init
```

`configure`가 Workspace·Registry·Experiment·SQL Warehouse·본문 정책을 한 설정 파일에 저장합니다. 최초 대화형 실행은 Profile과 공통 값을 안내합니다. 이후에는 저장한 선택과 생략한 값을 유지하고 변경할 값만 옵션으로 지정합니다. Registry의 UC Schema와 필요한 권한은 사전에 준비합니다. `registry-init`은 관리자가 테이블·Artifact Volume을 준비하는 별도 작업입니다.

조회·등록·실행·배포는 같은 설정을 상속합니다. 명시한 CLI 옵션이 우선하고, 유형별 프로젝트 예외, 공통 설정 순으로 적용합니다. 다른 Workspace의 Profile에는 기존 공통 값을 상속하지 않으며, 같은 이름의 Profile이 다른 Workspace로 변경되면 중단합니다.

Codex Hook·OTel·Turn Scorer는 `aops configure --codex`로 선택적으로 활성화합니다. Code·Genie 설정이나 진단에 Codex는 필요하지 않습니다. `--no-codex`는 Hook 수집을 비활성화하지만 기존 전역 exporter를 삭제하지 않습니다.

이전 `use`와 프로젝트별 tracing 설정 명령은 제거했습니다. 과거 `registry.json`은 더 이상 읽지 않으므로 기존 사용자는 `aops configure`에서 의도한 환경을 다시 선택합니다. 이전의 완전한 `aops.yaml`은 계속 읽으며 기존 명시 값은 예외로 유지합니다. 공통 값을 상속하려면 중복 항목을 제거하고 새 버전으로 등록하십시오. 설정 전 기존 Outbox를 원래 환경에서 비워야 하며 과거 이벤트를 새 UC 저장소로 임의 이동하지 않습니다.

## 정의

Code framework는 `langgraph/openai`이며 공식 `agentbricks init`의 API 전용 템플릿을 사용합니다. aops는 서버나 채팅 UI를 다시 구현하지 않습니다. `agent.toml`, 실제 실행 코드와 `AGENTKIT_CONTRACT.md`의 네이티브 계약을 유지하십시오.

No-code 생성은 로컬 초안만 만듭니다. 실제 테이블·Warehouse·Genie Space를 임의로 만들지 않습니다. API로 내보낸 실제 `serialized_space` **JSON 객체**를 `genie.json`에 둡니다. 빈 초안은 검증·보관할 수 있지만 배포할 수 없습니다.

Genie의 최초 `aops.yaml`은 다음 정도면 충분합니다.

```yaml
schema_version: 1
framework: genie
source_paths:
  - genie.json
```

Code에는 framework와 네이티브 소스 경로가 들어갑니다. `runtime`은 연결·배포할 때 자동 기록하며 Workspace URL은 잘못된 환경에 연결하지 않기 위한 리소스 식별 정보입니다. `runtime.warehouse_id`는 Genie만 다른 Warehouse가 필요할 때 선택적으로 지정합니다. `tracing`도 필요한 필드만 예외로 작성할 수 있으며 생략한 필드는 공통 설정을 상속합니다. 상속 결과는 실제 UC 위치와 함께 검증하며 원격 Experiment를 자동 재연결하지 않습니다.

Code 소스는 `source_paths`에 명시한 경로만 보관합니다. `.env*`, 인증 파일, 개인 키, 가상환경, Git·런타임 상태·캐시·로컬 평가 디렉터리를 제외하고 심볼릭 링크·경로 탈출은 거부합니다. 이는 범용 Secret 탐지기가 아니므로 다른 소스 파일에 인증정보를 적지 마십시오. `대외비/개인정보` 또는 `강화` 등급 소스의 로컬 복사형 게시 제한은 세 유형에 동일하게 적용합니다.

## MLflow

공통 추적은 `configure`에서 준비한 MLflow Experiment와 Registry의 UC Schema를 사용합니다. Agent·버전·유형 태그로 구분하며 별도 설정 명령은 필요하지 않습니다. 기존 Experiment의 UC 위치가 다르면 중단합니다. 연결은 새 Trace에만 적용되며 과거 Trace를 이전하지 않습니다. Code는 `deploy`에서 상속한 Experiment를 공식 `tracing bind`에도 연결합니다. App 서비스 주체의 UC 권한과 실제 적재는 배포 후 확인해야 합니다.

Skill도 동일한 공통 UC 저장소를 사용합니다. 새 Hook 이벤트에 실제 적용된 바인딩을 고정해 보관합니다. 기존 설정의 legacy 저장소는 호환 조회·전송에만 유지하며, 신규 `configure`에서는 UC 바인딩을 준비합니다.

Code·Genie의 공통 추적은 **호출 경계**입니다. Code framework의 내부 Trace는 Agent Bricks가 관리하며 공통 경계 Trace와 자동 병합하지 않습니다. Genie가 공개하지 않는 reasoning·토큰·하위 호출은 수집했다고 주장하지 않습니다. 외부 리소스 호출을 로컬 소스 해시로 표시하는 것은 원격 배포 바이너리의 동일성 증명이 아닙니다. 운영자가 연결된 리소스의 외부 변경을 별도로 통제해야 합니다.

aops 공통 Trace의 본문은 기본 비수집입니다. 세 유형 모두 `aops configure --capture-content`로 공통 동의를 관리합니다. Code·Genie는 실행별 `--capture-content` 또는 `--no-content`로 덮어쓸 수 있습니다. 이는 Code의 네이티브 framework autolog 본문 정책을 바꾸지 않으므로 네이티브 설정·권한도 별도로 확인하십시오. 패턴 마스킹은 완전한 비식별화를 보장하지 않습니다.

## 연결 · 배포

```bash
# 기존 Code App: 실제 프로젝트의 API 경로와 요청 계약을 사용
aops link <app-name> --invocation-path <actual-api-path>
aops run --input request.json

# 기존 Genie 연결
aops link <space-id>
aops run --prompt "업무 질문"

# Code 생성/갱신
aops deploy --name <deployment-name> --apply

# 실제 genie.json으로 Genie 생성/갱신
aops deploy --apply
```

`link`는 접근을 확인하고 로컬 바인딩만 저장하며 원격 정의를 가져오거나 갱신하지 않습니다. 다른 workspace에 바인딩된 프로젝트는 거부합니다. `deploy`는 명시적 `--apply`가 필요하며 Agent Bricks가 선언된 store 등을 프로비저닝할 수 있습니다. Genie 갱신은 server etag로 충돌을 검사합니다.

Genie 배포 Warehouse는 `--warehouse-id`, `runtime.warehouse_id`, 공통 Warehouse 순으로 선택합니다. 공통 값만 사용했을 때 프로젝트에 Warehouse나 Trace 설정을 중복 저장하지 않습니다.

Code 요청 JSON은 네이티브 payload를 그대로 전달합니다. invocation ID, application session ID와 routing key를 혼동하거나 자동 변환하지 않습니다. HTTP 202는 완료가 아니므로 성공한 완료 Trace로 처리하지 않습니다. 비동기 polling·replay는 네이티브 프로토콜을 사용해야 합니다.

배포·연결을 마친 뒤 등록하십시오. 등록 시 상속까지 끝난 실제 프로젝트 환경을 불변 Release에 자동 보관합니다. 등록 소스나 Trace 환경과 달라진 로컬 실행은 `working`으로 기록하며 승인 증빙으로 사용할 수 없습니다. 과거 Release의 Trace 조회·평가·심사는 현재 기본값이 아닌 해당 Release의 바인딩을 사용합니다. 등록은 배포를 실행하지 않고 배포도 등록·운영 지정을 실행하지 않습니다.

## 등록 · 조회

```bash
aops validate
aops register --version <version>
aops list
aops load <agent-id> --version <version>
aops traces <agent-id> --version <version>
aops trace <agent-id> <trace-id> --version <version>
```

세 유형을 같은 목록에 표시하며 Code·Genie 등록에는 UC Skill 게시기를 사용하지 않습니다. load는 Artifact와 파일별 해시·구조·권한을 확인하며 Skill일 때만 Codex Runtime을 준비합니다. 리소스가 없는 초안은 Registry·Artifact Volume 권한으로 관리합니다. Trace 조회는 기본적으로 본문을 출력하지 않습니다.

## 평가 · 심사

평가 데이터는 실제 MLflow Trace 참조와 검증된 기대 정답의 배열입니다.

```json
[
  {"trace_id": "<actual-trace-id>", "expectations": {"expected_response": "검증된 정답"}}
]
```

```bash
aops evaluate <agent-id> --version <version> --data eval-traces.json \
  --judge-model databricks:/<judge-model> --capture-content

aops review <agent-id> --version <version> --run-id <run-id> --trace-id <trace-id> \
  --profile <reviewer-profile>
```

평가는 동의하에 입출력을 수집한 성공 Trace만 사용합니다. ID·버전·소스 체크섬을 검사하고 `mlflow.genai.evaluate`의 Correctness·RelevanceToQuery·Safety 결과와 실제 root span 지연 p90을 기록합니다. 판정 누락·judge 오류는 합격으로 처리하지 않습니다. 정답 출처와 표본 대표성은 사람이 검토해야 합니다.

Code·Genie 기본 p90은 호출 경계 지연이며 서비스 전체 SLO나 하위 LLM 지연이 아닙니다. 실제 LLM judge 호출은 비용이 발생할 수 있습니다.

심사는 Owner·게시자와 다른 인증 사용자여야 합니다. FINISHED Run, Experiment·Artifact hash·Trace span 및 SPEC 임계값을 검사합니다. 운영 지정 때 같은 증빙을 다시 검사합니다.

## 운영 선택

```bash
aops promote <agent-id> --version <version> --expected-version none
aops production <agent-id>
aops rollback <agent-id> --version <previous-version> --expected-version <current-version>
```

promote·rollback은 **카탈로그 포인터만** 바꾸며 실제 App 재배포·Genie 복원·트래픽 전환을 하지 않습니다. rollback은 과거 운영 선택 이력이 있는 버전만 허용합니다. `--expected-version`이 현재 포인터와 다르면 중단합니다. 최초 지정은 `none`입니다.

`agents.current_version`은 최근 등록 버전이며 별도 `agent_production`이 운영 선택을 보관합니다. 기존 Registry는 `registry-init`을 다시 실행해 추가 테이블을 준비합니다. 기존 다섯 테이블과 schema_version 2 Skill Release는 유지하며 포털 데이터를 가져오지 않습니다.

멀티테이블 트랜잭션이나 강제된 Delta 기본키는 없습니다. 최초 운영 지정은 단일 writer로 직렬화하고 UC MODIFY 권한을 최소화하십시오. CLI 정책은 직접 SQL/MLflow 변경을 차단하는 서버 보안 경계가 아닙니다. 등록 또는 감사 로그가 부분 실패하면 재시도 전 Registry·Artifact·이벤트를 점검해야 합니다.

## 검증 경계

로컬 회귀, mocked SDK/CLI 계약, 실제 로컬 MLflow Trace와 span 재조회까지 검증합니다. 신규 원격 App·Genie·UC tracing·LLM judge·Delta 운영 전환은 워크스페이스를 선택한 후 별도 검증해야 합니다. 과거 Skill 관측 실험과 신규 통합 기능의 운영 검증을 혼동하지 마십시오.
