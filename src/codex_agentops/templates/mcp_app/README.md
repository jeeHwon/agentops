# Generated Harness Agent MCP App

이 폴더는 `aops deploy`가 검증된 Agent Release에서 생성한 배포물입니다.
직접 수정하지 말고 원본 Harness/Skill의 새 Release를 만든 뒤 다시 배포합니다.

- MCP endpoint: `/mcp`
- Tools: `health`, `agent_info`, `ask_agent`
- Authentication: Databricks Apps OAuth + Model Serving OBO
- Monitoring: MLflow Trace + Unity Catalog trace storage
