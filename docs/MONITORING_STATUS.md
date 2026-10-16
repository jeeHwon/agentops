# Skill-based Agent — 최종 검증 결과

2026-10-16 기준. **A**는 Codex Hook, **B**는 Omnigent 어댑터입니다. `됨`은 **명시한 조건에서 관측 또는 테스트됨**, `안됨`은 **동작하지 않거나 전수/운영 수준으로 증명되지 않음**을 뜻합니다. `안됨`을 토큰 0개나 모든 실행의 실패로 해석하지 않습니다. 실제 모델 Trace, 격리 실험의 재조회, 합성 OTLP/Hook, 로컬 회귀 테스트를 구분했습니다. 테스트용 환경과 기존 공유 Registry·scorer·권한은 변경하지 않았습니다.

## 됨

| 대상 | 확인 항목 | 검증 내용 |
| --- | --- | --- |
| A·B | 현행 `SPEC.md` 계약과 Release/Registry | 현재 Release의 필수 메타데이터·Skill/하네스 체크섬과 로드 확인. 별도 구형 Registry는 현행 파서와 호환되지 않으며 수정하지 않음. |
| A·B | **관측된** 성공 Turn의 사용량 | A 단일/하위 Agent 실제 실행과 B **일부** 부모/자식 실제 실행에서 입력·출력·합계가 로컬 실행 기록과 MLflow에서 일치. 관측 사례에 한하며 아래 B 누락 사례도 존재. 캐시 읽기 토큰은 입력에 이미 포함. |
| A·B | 결측과 명시적 0 구분 | 로컬 MLflow 테스트에서 부분 결측/미관측은 `partial`/`unknown` 및 사용량 `None`, 명시적인 출력 0은 0으로 보존. 기존 원격 Trace에는 `available=false`인데 MLflow 요약 사용량이 0인 사례도 있으므로 요약값만으로 실제 사용량 0이라고 판단하지 않음. |
| A | 부모→하위 Agent→Tool 한 Trace | 실제 explorer의 부모·자식 span 링크와 하위 Tool을 재조회. |
| B | 조건이 맞을 때 부모→하위 Agent 연결 | 기존 실제 위임 Trace에서 부모와 자식이 동일 MLflow Trace로 연결된 실행을 재조회. 자식 세션 ID와 수집 순서에 의존하므로 모든 위임의 연결을 뜻하지 않음. |
| A·B | Agent와 관측 Tool span | A/B 실제 실행에서 Agent와 Tool span 관측. **Tool 경계까지만** 포함하며 외부 서버 내부는 포함하지 않음. |
| A | 전송 완료 Trace 재조회 | 로컬 outbox가 `uploaded`로 기록한 Trace는 해당 표본 모두 원격에서 재조회됨. Hook에 애초에 오지 않은 호출/Turn의 전수를 증명하지는 않음. |
| B | 관리형 프로젝트 Tool 건수 대조 | 조사한 관리형 실행의 Tool 카드 수와 Tool span 수는 일치. 동일 호출 ID별 일치는 검증되지 않음. |
| B | Tool 오류와 Agent 복구의 구분 | 실제 실패 Tool `ERROR`, 복구된 Agent root `OK`를 별도 재조회. |
| A·B | Trace 처리시간·상태 | 실제 Trace에 `execution_time_ms`와 상태 관측. 개별 LLM 요청 지연/서비스 p90은 아님. |
| B | OTLP 배치의 재시작·순서 복구 | Tool 선도착 → Collector 재시작 → Agent root 후도착을 한 Trace로 복구하고 동일 배치 재전송 중복을 방지. A의 Hook 경로와 구별. |
| B | 미연결 Tool span의 **가시성** | `/health`의 `otel_spool.waiting_for_root`와 24시간 기준 `waiting_for_root_overdue`를 로컬 회귀 테스트와 격리 Collector에서 확인. 실제 미연결 span이 spool에 남았으며, 새 지표는 알림 발송/자동 삭제가 아님. |
| A·B | 본문 있는 합성 입력의 assessment | 격리된 자동 평가에서 비고객 합성 입력·출력에 대한 assessment 적재/조회. 업무 품질 합격의 근거는 아님. |

## 안됨

| 대상 | 확인 항목 | 근거 및 현재 경계 |
| --- | --- | --- |
| A·B | **모든** 모델 요청의 토큰 100% 수집 | 공급자가 생성한 요청별 ID와 모델 호출별 span의 독립적인 1:1 분모/분자 없음. 재시도·중단·Tool 내부 모델 호출까지 전수 대조 불가. 성공한 Turn의 합계가 존재해도 100%가 아님. |
| A | 모든 성공/재개 Turn에서 사용량 확보 | 실제 재개·중단은 별도 환경 모델 설정 오류로 미검증. 합성 Hook 수치를 실제 모델 검증으로 해석하지 않음. |
| B | 모든 성공/재개 Turn에서 사용량 확보 | 실제 재개 Turn의 사용량이 `None`이었고 **추가 재시험에서는 첫 Turn과 재개 Turn 모두** MLflow `usage=None`, `coverage=unknown`; 같은 세션의 로컬 Omnigent 사용량도 비어 있었음. 누락은 재개에만 한정되지 않음. |
| B | 누락 사용량 원인의 확정·복구 | Omnigent 0.17의 `run_turn`은 `thread/tokenUsage/updated`에서 사용량을 채우지만 `final_answer` 항목 완료나 `turn/completed`에서 즉시 반환함. 이벤트 선후관계가 가능한 원인이나 이번 실행의 **원시 Codex 이벤트 순서는 보존되지 않아 확정 불가**. 이전 Turn 값을 복사하거나 합성 수치를 만들지 않음. |
| A·B | 실제 Turn 중단 시 서버 취소·정확한 상태/사용량 | A는 실제 중단 미재현(합성 Hook의 `ERROR`/토큰 보존만 확인). B Ctrl-C 시 원격 root가 `OK`였고, 세션별 interrupt API 두 번은 해당 Turn이 이미 완료된 후 요청됨; HTTP `202 {"queued":false}`는 라우트 구현상 성공적인 실제 중단 증거가 아님. 실행 중 취소와 잔여 사용량은 입증하지 못함. |
| B | 모든 부모·자식의 동일 Trace 연결 | 실제 위임에서 연결된 경우와 자식이 별도 최상위 Trace가 된 경우가 모두 있음. 자식 ID가 있어도 부모가 먼저 확정되면 별도 저장되는 순서 문제를 로컬에서 재현. 자식 ID가 없는 경우도 있어 새 실행의 연결은 보장되지 않음. 별도 Trace의 부모·자식 토큰을 자동 합산하지 않음. |
| A | 모든 Tool 호출 및 오류 Turn의 Trace | 완료된 호스티드 WebSearch가 Hook/Tool span에 빠진 실제 사례가 있음. 종료 Hook이 없는 오류 Turn도 MLflow에 Trace가 생성되지 않았음. Codex 공식 Hook 문서는 호스티드 WebSearch가 Tool Hook을 거치지 않는다고 명시. |
| B | 관리형 실행 밖 Codex-native Tool 전수 | 조사한 프로젝트 밖 세션에서 실제 Bash 호출은 있었지만 해당 Tool span은 없었음. 관리형 프로젝트의 카드·span 건수 일치를 전체 Codex 세션 커버리지로 확대할 수 없음. |
| A | 실제 Tool 오류와 Agent 복구 구분 | 합성 Hook 오류는 확인했지만 실제 Tool 실패·복구 Turn의 원격 조회는 미검증. |
| A·B | 외부 MCP/Tool 서버 내부 호출 및 그 모델 사용량 | 외부 서버의 자체 계측과 trace context 전달 없음. 현재 Tool span은 경계 호출만 뜻함. |
| A·B | 모델 요청별 비용·실제 청구액 | 실제 Trace의 `cost=None`. 관리형 MLflow는 LiteLLM 가격 정보나 명시적 비용 속성이 필요할 수 있고, 추정 비용은 청구액과 별개. |
| A·B | CPU/메모리 OTel 시계열 메트릭 | A `/v1/metrics`는 저장/집계하지 않고 B는 `OTEL_METRICS_EXPORTER=none`. Trace 처리시간은 시계열 메트릭이 아님. |
| A·B | 원격 MLflow exactly-once | 로컬 ACK 직전 크래시 등으로 원격 성공 후 재시도가 일어나면 중복 가능. 로컬 spool 재전송 시험만으로 원격 전수를 보증하지 않음. |
| B | 미연결 span의 자동 알림·보존 만료 | `/health`의 개수·24시간 지표는 구현됨. 경보 전달, 수명 제한/자동 청소는 없음. 늦은 root 복구를 위해 현재 미연결 span은 보존되며 spool 용량 상한에 도달할 수 있음. |
| A·B | 기본 비수집 Trace의 본문 기반 품질 점수 | Prompt/응답 본문이 없으므로 관련성/완전성/안전성 점수를 실제 업무 품질로 해석할 수 없음. 기존 공유 실험 scorer는 `agent.turn`만 필터링하며 본문 가용성 검사가 없음; 승인 없이 정책 변경하지 않음. |
| A·B | `SPEC.md` 성공 기준의 운영 자동 승인 | 합성 평가를 실제 업무 데이터의 정확성·관련성·안전성 및 지연 p90을 통과한 Release 게이트로 해석할 근거 없음. |
| A·B | MLflow Trace의 UC 테이블 저장 | 확인한 실험의 `trace_location=None`. UC Agent/Skill Registry는 UC Trace 테이블과 별도. 기존 공유 실험의 저장소·권한 변경은 수행하지 않음. |
| A·B | 민감 Skill을 원문 비공개로 실행만 허용 | 현재 A/B는 Skill 원문을 로컬 실행 환경에 복사하므로 실행 사용자에게 원문을 강제로 숨길 수 없음. 고객의 별도 권한/격리 결정 전에는 지원하지 않음. |

검증: A `pytest -q`, B `PYTHONPATH=../agentops/src:src ../agentops/.venv/bin/pytest -q`, B UI `node tests/test_web_ui.mjs`. 실측 Trace ID·계정·원문·수치·workspace 주소는 공개 저장소에 기록하지 않았습니다. Unity Gateway 연결은 이번 범위에서 제외했습니다.

공식 구현/문서: [Codex Hook 범위](https://learn.chatgpt.com/docs/hooks), [MLflow 토큰·비용](https://mlflow.org/docs/latest/genai/tracing/token-usage-cost/), [MLflow 집계 구현](https://github.com/mlflow/mlflow/blob/master/mlflow/tracing/utils/__init__.py), [Omnigent Codex 이벤트 처리](https://github.com/omnigent-ai/omnigent/blob/main/omnigent/inner/codex_executor.py), [Codex App Server 토큰 이벤트 스키마](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/json/v2/ThreadTokenUsageUpdatedNotification.json), [Codex Python SDK Turn 처리](https://github.com/openai/codex/blob/main/sdk/python/src/openai_codex/_run.py), [Databricks UC Trace](https://docs.databricks.com/aws/en/mlflow3/genai/tracing/trace-unity-catalog).
