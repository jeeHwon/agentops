---
agent_type: harness
service_criticality: low
data_sensitivity: internal
security_access_level: standard
required_uc_permissions: []
use_flagship_model: false
---

# Business Specification

## 목적

사용자가 제공한 문서를 간결하게 요약하고, 요약 결과의 근거성과 완결성을 검증합니다.

## 입력

- 요약할 문서 또는 텍스트
- 원하는 요약 길이와 대상 독자

## 출력

- 핵심 내용 요약
- 확인이 필요한 사실과 누락 항목
- 검증 결과

## 성공 기준

- 원문에 없는 사실을 추가하지 않습니다.
- 주요 결론과 후속 행동을 빠뜨리지 않습니다.
- 검증 결과에서 근거가 부족한 문장을 명시합니다.
