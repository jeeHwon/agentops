# Agent Instructions

- 작업을 시작할 때 `spec.md`를 읽고 정의된 범위와 성공 기준을 따릅니다.
- 먼저 `$release-summary` Skill로 원문을 요약합니다.
- 요약을 마친 뒤 `validator` Sub-agent에게 원문과 요약문의 검증을 맡기고 결과를 반영합니다.
- 원문에서 확인할 수 없는 내용은 사실처럼 작성하지 않습니다.
- 민감정보와 인증정보를 출력하지 않습니다.
