"""Events on the SSE streams.

Turn streams use AG-UI event names and camelCase fields (https://docs.ag-ui.com), so a front end
that already speaks AG-UI can read them as is. aops-only facts (handoff, skill use, guardrail
decisions, usage) travel as AG-UI `CUSTOM` events. Batch and validation progress streams use
their own small event sets.

In a turn stream `threadId` is the aops session id and `runId` is the turn id.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from .common import Usage


class RunStarted(BaseModel):
    type: Literal["RUN_STARTED"] = "RUN_STARTED"
    threadId: str = Field(examples=["9d321c9c133d484897ce14f979375f2f"])
    runId: str = Field(examples=["42"])


class RunFinished(BaseModel):
    type: Literal["RUN_FINISHED"] = "RUN_FINISHED"
    threadId: str
    runId: str


class RunError(BaseModel):
    type: Literal["RUN_ERROR"] = "RUN_ERROR"
    message: str = Field(examples=["codex 실행 파일을 찾지 못했습니다"])
    code: str | None = Field(None, examples=["harness_unavailable"])


class TextMessageStart(BaseModel):
    type: Literal["TEXT_MESSAGE_START"] = "TEXT_MESSAGE_START"
    messageId: str
    role: Literal["assistant"] = "assistant"


class TextMessageContent(BaseModel):
    type: Literal["TEXT_MESSAGE_CONTENT"] = "TEXT_MESSAGE_CONTENT"
    messageId: str
    delta: str = Field(examples=["지난 대화에서 정한 이름은 "])


class TextMessageEnd(BaseModel):
    type: Literal["TEXT_MESSAGE_END"] = "TEXT_MESSAGE_END"
    messageId: str


class ToolCallStart(BaseModel):
    type: Literal["TOOL_CALL_START"] = "TOOL_CALL_START"
    toolCallId: str
    toolCallName: str = Field(examples=["shell", "skill:pine-sql-style", "databricks.sql"])
    parentMessageId: str | None = None


class ToolCallArgs(BaseModel):
    type: Literal["TOOL_CALL_ARGS"] = "TOOL_CALL_ARGS"
    toolCallId: str
    delta: str = Field(examples=['{"command": "ls"}'])


class ToolCallEnd(BaseModel):
    type: Literal["TOOL_CALL_END"] = "TOOL_CALL_END"
    toolCallId: str


class ToolCallResult(BaseModel):
    type: Literal["TOOL_CALL_RESULT"] = "TOOL_CALL_RESULT"
    messageId: str
    toolCallId: str
    content: str


class StepStarted(BaseModel):
    type: Literal["STEP_STARTED"] = "STEP_STARTED"
    stepName: str = Field(examples=["handoff", "harness"])


class StepFinished(BaseModel):
    type: Literal["STEP_FINISHED"] = "STEP_FINISHED"
    stepName: str


class HandoffValue(BaseModel):
    """The turns this harness had not seen, written to a file and announced in the prompt."""

    turns: int = Field(examples=[2])
    file: str = Field(examples=[".aops/handoff/9d32-claude-after-3.md"])
    method: Literal["file_notice", "inject_items"] = "file_notice"


class SkillValue(BaseModel):
    name: str = Field(examples=["pine-sql-style"])
    detection: Literal["explicit", "inferred"] = Field(
        "explicit", description="Codex에는 스킬 사용 이벤트가 없어 `SKILL.md` 읽기로 추정한다"
    )


class GuardrailValue(BaseModel):
    layer: Literal["instructions", "rules", "gateway", "judge"]
    rule: str = Field(examples=["kr-pii"])
    action: Literal["log", "block"]
    detail: str | None = None


class AopsCustom(BaseModel):
    """AG-UI `CUSTOM` event carrying an aops fact. `name` tells which value follows."""

    type: Literal["CUSTOM"] = "CUSTOM"
    name: Literal["aops.handoff", "aops.skill", "aops.guardrail", "aops.usage"]
    value: Union[HandoffValue, SkillValue, GuardrailValue, Usage]


TurnEvent = Annotated[
    Union[
        RunStarted, RunFinished, RunError, StepStarted, StepFinished,
        TextMessageStart, TextMessageContent, TextMessageEnd,
        ToolCallStart, ToolCallArgs, ToolCallEnd, ToolCallResult, AopsCustom,
    ],
    Field(discriminator="type"),
]


class ProgressEvent(BaseModel):
    """Progress of a long job: batch cells, validation steps, evaluation scorers."""

    type: Literal["started", "progress", "item_finished", "finished", "failed"]
    at: datetime
    item: str | None = Field(None, examples=["q3 × codex-guard-off × 2", "evaluate"])
    status: str | None = Field(None, examples=["passed"])
    done: int | None = Field(None, examples=[7])
    total: int | None = Field(None, examples=[24])
    detail: dict[str, Any] | None = None
