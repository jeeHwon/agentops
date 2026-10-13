---
schema_version: 1
id: release-sample-agent
name: Release Sample Agent
description: 문서의 핵심 내용과 후속 행동을 요약하고 결과를 검증합니다.
agent_type: harness
owner: ""
owner_group: ""
source_system: local
target_users: []
cost_center: ""
service_criticality: low
data_sensitivity: internal
security_access_level: standard
risk_level: low
hitl_required: false
required_uc_permissions: []
use_flagship_model: false
model: system.ai.gpt-5-6-sol
success_criteria:
  quality: 원문에 없는 사실을 추가하지 않고 주요 결론과 후속 행동을 빠뜨리지 않습니다.
  latency_ms: null
  completion_rate: null
---

# Agent Specification

## 입력

- 요약할 문서 또는 텍스트
- 원하는 요약 길이와 대상 독자

## 출력

- 핵심 요약
- 결정 사항과 후속 행동
- 확인이 필요한 사실과 누락 항목
- 검증 결과

## 성공 기준

- 원문에 없는 사실을 추가하지 않습니다.
- 주요 결론과 후속 행동을 빠뜨리지 않습니다.
- 근거가 부족한 문장을 명시합니다.
