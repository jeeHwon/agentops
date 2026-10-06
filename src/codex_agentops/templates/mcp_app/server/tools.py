from __future__ import annotations

from .runtime import runtime
from .tracing import ask


def load_tools(mcp_server) -> None:
    @mcp_server.tool
    def health() -> dict:
        """MCP 서버와 고정 Agent Release가 정상적으로 로드되었는지 확인합니다."""
        return {
            "status": "healthy",
            "agent_id": runtime.agent_id,
            "release_version": runtime.release_version,
        }

    @mcp_server.tool
    def agent_info() -> dict:
        """현재 Agent Release와 검증된 UC Skill 버전 정보를 반환합니다."""
        return {
            "agent_id": runtime.agent_id,
            "release_version": runtime.release_version,
            "profiles": runtime.config["profiles"],
            "skills": runtime.skills,
            "subagents": runtime.config.get("subagents", []),
        }

    @mcp_server.tool
    async def ask_agent(query: str, session_id: str | None = None) -> dict:
        """고정된 Harness와 UC Skills를 적용해 사용자 질문에 답합니다.

        Args:
            query: Agent에 전달할 사용자 요청입니다.
            session_id: 여러 호출을 MLflow에서 같은 대화로 묶을 선택적 식별자입니다.
        """
        return await ask(query, session_id)
