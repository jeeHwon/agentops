# Skill-based Agent: 토큰 및 MLflow 모니터링 검증표

이 문서는 Codex Hook(A)과 Omnigent(B)의 **현재 구현**을 설명합니다. 판정은 **됨 / 안됨** 두 가지뿐입니다. `됨`은 표에 적힌 조건에서 실제로 확인했다는 뜻이고, `안됨`은 구현되지 않았거나 전수 보증에 필요한 독립 근거가 없다는 뜻입니다. `안됨`을 실제 토큰 0개, 실패율 100%, 또는 기능 장애의 증거로 해석하지 마십시오.

검증 범위: 테스트 workspace의 현행 `SPEC.md` Release와 Registry, A/B 실제 모델 실행 Trace의 읽기 전용 재조회, 무해한 합성 Trace, 로컬 단위·MLflow file-store 통합 테스트, 합성 입력의 별도 자동 평가. Workspace 주소, 사용자 계정, Trace ID, 실측 토큰 수·지연시간, 원문, 고객 데이터는 이 저장소에 포함하지 않습니다. 테스트 날짜: 2026-10-15.

## 실제로 확인한 범위

| 실행 | MLflow 사용량 확인 | Trace 구성 |
| --- | --- | --- |
| A 단일 Turn | 관측된 입력·출력·합계가 실행 기록과 일치 | Agent와 Tool span 관측 |
| A 부모 + 하위 Agent | 관측된 입력·출력·합계가 실행 기록과 일치 | 같은 Trace의 부모·자식 링크와 하위 Tool span 관측 |
| B 부모 | 관측된 입력·출력·합계가 로컬 세션 기록과 일치 | Agent와 Tool span 관측 |
| B 하위 Agent | 관측된 입력·출력·합계가 로컬 세션 기록과 일치 | 부모와 별도 최상위 Agent Trace, 하위 Tool span 관측 |

재조회한 실제 Trace는 모두 상태 `OK`이며 Trace 단위 `execution_time_ms`가 존재하지만, `trace.info.cost`는 `None`이었습니다. 관측된 세션/Turn 사용량을 모델 요청 전수 또는 청구액으로 해석하지 않습니다. B 부모·자식 세션의 합계를 **하나의 Trace 토큰**으로 보도하지 않습니다. A/B 실제 Trace에서 요청별 신뢰 가능한 모델 호출 ID는 확인되지 않았습니다. 과거 B 실측의 `coverage=observed`는 요청별 전수를 의미하지 않으며, 현재 코드는 LLM span이 없으면 `unverified`로 저장합니다. 무해한 합성 B Trace의 원격 재조회에서 Tool span, `agentops.subagent_span_coverage=unverified`, `agentops.token_usage.coverage=unverified`, `agentops.evaluation_inputs_available=false`, `cost=None`을 확인했습니다. 합성 Trace를 실제 모델 호출로 간주하지 않습니다.

## 기능별 최종 판정

| 기능 | A | B | 판정 근거 / 경계 |
| --- | --- | --- | --- |
| `SPEC.md` 계약·Release/Registry 일치 | **됨** | **됨** | 원본 템플릿의 필수 키, 아카이브의 `SPEC.md`·Skill·SHA-256·Registry 버전 및 모델을 읽기 전용 확인. 과거 스키마의 별도 Agent가 자동 전환된 것은 아님. |
| 성공한 Turn의 입력·출력·캐시 사용량 | **됨** | **됨** | 위 실제 Trace와 A Turn/B 세션 사용량 합계 대조. 캐시 읽기는 이미 입력에 포함. 모델 개별 호출별 정확도를 뜻하지 않음. |
| 토큰 일부 결측을 실제 0으로 오인하지 않기 | **됨** | **됨** | 로컬 MLflow 통합 테스트에서 입력/출력 누락 시 `token_usage=None` 및 partial/unknown 표시; 명시적인 출력 0은 실제 0으로 처리. |
| 전체 모델 요청의 토큰 **100%** 대조 | **안됨** | **안됨** | 요청마다 공급자 생성 ID를 Trace span에 연결한 독립 분모/분자가 없음. 도구 내부 모델 호출, 중단·재시도·export 지연도 포함해 보증 불가. |
| 동일 Trace의 하위 Agent/Tool 계층 | **됨** | **안됨** | A 실제 explorer와 그 하위 Tool의 부모 링크 확인. B 실제 validator는 Tool은 보이나 부모와 **별도 최상위 Trace**; 원문 비수집 상태에서 자식 session ID가 OTel 비본문 속성에 없음. |
| 관측된 하위 Tool 호출의 span | **됨** | **됨** | A/B 실제 Tool span 재조회. 원격 Tool 서버의 내부 호출까지 자동 계측한다는 뜻은 아님. |
| 원격 MCP·Tool 내부 호출/모델 사용량 | **안됨** | **안됨** | Hook/Omnigent가 내보낸 경계 이후는 별도 서버 계측 및 trace context 전달이 필요. |
| Trace 단위 처리시간·상태 조회 | **됨** | **됨** | 실제 Trace의 `execution_time_ms`와 `TraceStatus.OK` 확인. 개별 LLM 지연이나 90백분위 지표가 아님. |
| 모델 호출별 비용과 청구액 | **안됨** | **안됨** | 네 실제 Trace의 `cost=None`; 모델명만으로 USD 청구액을 만들지 않음. |
| 기본 비수집 시 본문 기반 품질점수의 **유효성** | **안됨** | **안됨** | Prompt·응답이 없으므로 관련성/완전성/안전성 점수를 업무 품질로 해석 불가. |
| 안전한 합성 평가의 비동기 assessment 적재 | **됨** | **됨** | 격리 실험에서 입력·출력이 있는 합성 Trace에 scorer assessment 적재 및 조회. 업무 품질 합격을 뜻하지 않음. |
| 현재 선택된 공유 실험의 **본문 가용성 필터** | **안됨** | **안됨** | 읽기 전용 재조회에서 세 scorer가 `agent.turn`만 필터링해 본문 없는 Trace도 대상에 포함. 현재 코드는 기존 공유 scorer 정책을 자동 변경하지 않고 충돌 오류를 반환함. 필터 변경은 별도 승인 필요. |
| CPU/메모리 등 OTel 시계열 지표 | **안됨** | **안됨** | A의 `/v1/metrics`는 현재 계측 저장을 하지 않음. B는 `OTEL_METRICS_EXPORTER=none`. Trace의 `execution_time_ms`와 별도. |
| OTLP 배치 재시도 및 로컬 재시작 복구 | **됨** | **됨** | 로컬 SQLite spool 재기동·내보내기 일시 실패 재시도·중복 수신 테스트 확인. 배치 ACK가 원격 Trace 적재 완료를 뜻하지는 않음. |
| 원격 MLflow Trace exactly-once | **안됨** | **안됨** | 원격 적재 성공 후 로컬 완료 표시 전 강제 종료 시 재전송/중복 가능. |
| 실제 사용자 중단·도구 오류·장기 세션까지의 전수 운영 검증 | **안됨** | **안됨** | 로컬 입력/실패 분기 테스트와 제한된 실제 성공 실행만 확인. 해당 실패 조건의 전체 실사용 시나리오는 재현·대조하지 않음. |

**A/B 성공 Turn에 `token_usage`가 존재하는 것은 100% 커버리지 검증과 다른 질문입니다.** 자체 사용량 출처가 둘 이상이어도 둘이 동시에 놓친 모델 요청은 알 수 없습니다. MLflow의 부모 span 사용량과 자식 span 사용량을 중복 더하지 말고, 캐시 토큰을 입력 합계에 다시 더하지 마십시오. 직접 OTLP 수집 경로로 전환하면 캐시·0 토큰 처리를 버전별로 재검증해야 합니다.

## `안됨`의 확인·해결 조건

1. **전수 토큰:** 제공자 생성 요청 ID를 LLM span에 연결한 뒤, 동일 기간·모델·사용자 범위에서 실제 요청 목록과 1:1 대조합니다. 재시도/취소/독립 도구 호출을 분모에 포함하고 누락 수를 공개합니다. 독립 분모가 확보되지 않는 동안 100%라고 표시하지 않습니다.
2. **B 하위 Agent:** `sys_session_send`가 자식 session ID를 민감한 prompt/tool 본문과 분리된 OTel 속성으로 내보내는지 검증하고, 실제 실행 후 부모→자식→Tool 링크를 다시 조회합니다. 현재 원시 이벤트에는 이 ID가 없어 기본 비수집 모드에서 복구할 수 없습니다.
3. **Tool 내부·비용·시계열:** 도구 서버 계측과 context 전달, 비용 산정 출처/가격 정책, OTel metrics exporter 및 저장 대상이 마련되기 전까지 기능 불가로 표시합니다.
4. **공유 실험 scorer:** 현재 `agent.turn` 전체를 보는 정책을 본문 있는 Trace만 평가하도록 줄이면 기존의 태그 없는 Trace는 Safety 등에서 빠집니다. 실험 소유자가 미태그 데이터의 보존·별도 평가 방안을 검토하고 명시적으로 승인하기 전에는 필터를 바꾸지 않습니다. 승인과 상관없이 기본 비수집 Trace의 기존 품질 assessment는 신뢰하지 않습니다.
5. **운영 실패 시나리오:** 사용자 중단, Tool 실패, 지연된 OTLP, 재시작을 각각 실제 실행하고 독립 요청 로그와 MLflow Trace를 비교합니다. 실패 Trace가 생성됐다는 사실만으로 토큰 전수 수집을 증명할 수 없습니다.

민감 Skill의 “실행만 허용, 원문은 숨김”은 로컬에 원문을 복사하는 A/B 구조에서 강제할 수 없습니다. 해당 사용 사례는 고객 의사결정 전까지 지원하지 않으며, 이 작업에는 신규 실행/서빙 구현이 포함되지 않습니다. 이 문서에는 Unity Gateway 연결 작업이 포함되지 않습니다.

검증 명령: A `pytest -q`, B `PYTHONPATH=../agentops/src:src ../agentops/.venv/bin/pytest -q`, B UI `node tests/test_web_ui.mjs`. 외부 읽기 시험은 사용자가 명시적으로 선택한 Databricks profile·Registry·실험을 사용해야 합니다. 공유 실험 scorer/Registry GRANT 및 기존 테이블은 이 문서 작성 중 변경하지 않았습니다.

공식 참고: [MLflow 토큰·비용](https://mlflow.org/docs/latest/genai/tracing/token-usage-cost/), [MLflow 집계 구현](https://github.com/mlflow/mlflow/blob/master/mlflow/tracing/utils/__init__.py), [MLflow 직접 OTLP 캐시/0 값 이슈](https://github.com/mlflow/mlflow/issues/22745), [OpenTelemetry GenAI 속성](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/), [Databricks 자동 scorer](https://docs.databricks.com/aws/en/mlflow3/genai/eval-monitor/production-monitoring).
