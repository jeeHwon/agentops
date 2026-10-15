---
agent_id: release-sample-agent
agent_name: Release Sample Agent
agent_version: "1.0.0"
build_type: skill
serving_type: apps
origin: databricks
owner: owner@example.com
owner_group: AI-CoE
service_criticality: 하
data_sensitivity: 내부
security_level: 일반
uc_permissions: []
success_criteria:
  correctness_min: 0.8
  relevance_min: 0.9
  safety_min: 1.0
  latency_p90_seconds: 10
flagship_model: false
model_service: system.ai.gpt-5-6-sol
---

# 에이전트 설계서 (SPEC)

## 1. 해결하려는 문제

문서의 핵심 내용과 후속 행동을 요약하고 결과를 검증합니다.

## 2. 사용자와 사용 시나리오

업무 담당자가 문서의 요약 및 검증을 요청합니다.

## 3. 사용하는 데이터와 도구

입력받은 문서를 사용하며 외부 데이터 접근은 요구하지 않습니다.

## 4. 성공 기준과 평가 방법

- 원문에 없는 사실을 추가하지 않습니다.
- 주요 결론과 후속 행동을 빠뜨리지 않습니다.
- 근거가 부족한 문장을 명시합니다.

## 5. 제약 사항과 가드레일

원문에 없는 개인정보나 기밀정보를 생성하지 않습니다.
