"""Monitoring: `aops monitor` (usage, budgets, costs, running agents and MCP, quality, guardrails).

Every response carries `as_of`, the time of the newest source row, because the sources lag
(usage up to 1 hour, billing up to 12 hours).
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..common import ALL, DBX, Link, command, todo

router = APIRouter(tags=["모니터링"])

Scope = Literal["me", "all"]


class UsageRow(BaseModel):
    key: str = Field(examples=["price-helper", "databricks-claude-sonnet-4-6", "dev@example.com"])
    requests: int = Field(examples=[312])
    input_tokens: int = Field(examples=[1_204_000])
    output_tokens: int = Field(examples=[98_000])
    mcp_calls: int = Field(0, examples=[41])


class UsageReport(BaseModel):
    scope: Scope
    group_by: Literal["user", "agent", "model", "harness"]
    since: datetime
    as_of: datetime
    rows: list[UsageRow]


class BudgetStatus(BaseModel):
    budget: str = Field(examples=["team-ai-monthly"])
    spent_usd: float = Field(examples=[412.5])
    limit_usd: float = Field(examples=[1000.0])
    ratio: float = Field(examples=[0.41])
    blocking: bool = Field(description="차단 기준이 걸린 Gateway 예산인지")
    as_of: datetime


class CostRow(BaseModel):
    key: str = Field(examples=["agent-bricks-price-helper"])
    basis: Literal["actual", "estimated"] = Field(description="actual: 청구 기록, estimated: 토큰 × 단가표")
    usd: float = Field(examples=[37.2])


class CostReport(BaseModel):
    scope: Scope
    since: datetime
    as_of: datetime
    rows: list[CostRow]


class RunningAgent(BaseModel):
    agent_id: str = Field(examples=["price-helper"])
    version: str = Field(examples=["1.2.0"])
    app_name: str = Field(examples=["agent-bricks-price-helper"])
    app_state: str = Field(examples=["RUNNING"])
    active_runs: int | None = Field(None, description="aops 상태 경로(/aops/status)가 있는 에이전트만")
    recent_mcp: list[str] = Field(default_factory=list, examples=[["system.ai.dbsql", "databricks.genie"]])
    links: list[Link] = Field(default_factory=list)


class QualityRow(BaseModel):
    agent_id: str
    version: str
    scorer: str = Field(examples=["correctness"])
    value: float = Field(examples=[0.88])
    samples: int = Field(examples=[120])


class GuardrailRow(BaseModel):
    agent_id: str | None = None
    rule: str = Field(examples=["kr-pii"])
    layer: Literal["instructions", "rules", "gateway", "judge"]
    blocked: int = Field(examples=[3])
    logged: int = Field(examples=[11])


class DashboardCreate(BaseModel):
    name: str = Field("aops 운영 현황", examples=["aops 운영 현황"])


@router.get("/monitor/usage", summary="사용량", **command("aops monitor", ALL, screen="console Monitor"))
def usage(scope: Scope = "me", group_by: Literal["user", "agent", "model", "harness"] = "agent", since: datetime | None = None) -> UsageReport:
    raise todo("aops monitor")


@router.get("/monitor/budgets", summary="예산 소진율", **command("aops monitor", DBX, screen="console Monitor"))
def budgets(scope: Scope = "me") -> list[BudgetStatus]:
    raise todo("aops monitor")


@router.get("/monitor/costs", summary="비용 (실제·추정)", **command("aops monitor", DBX, screen="console Monitor"))
def costs(scope: Scope = "me", basis: Literal["actual", "estimated", "both"] = "both", since: datetime | None = None) -> CostReport:
    raise todo("aops monitor")


@router.get("/monitor/agents", summary="운영 중인 에이전트와 MCP", **command("aops monitor", DBX, screen="console Monitor"))
def running_agents(scope: Scope = "all") -> list[RunningAgent]:
    raise todo("aops monitor")


@router.get("/monitor/quality", summary="품질 점수 (배포된 에이전트)", **command("aops monitor", DBX, screen="console Monitor"))
def quality(agent_id: str | None = None) -> list[QualityRow]:
    raise todo("aops monitor")


@router.get("/monitor/guardrails", summary="가드레일 차단 집계", **command("aops monitor", ALL, screen="console Monitor"))
def guardrails(scope: Scope = "me", since: datetime | None = None) -> list[GuardrailRow]:
    raise todo("aops monitor")


@router.post("/monitor/dashboard", status_code=201, summary="AI/BI 대시보드 만들기", **command("aops monitor dashboard", DBX))
def create_dashboard(body: DashboardCreate) -> Link:
    raise todo("aops monitor dashboard")
