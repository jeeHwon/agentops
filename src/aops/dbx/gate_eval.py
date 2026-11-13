"""The Gate's `evaluate` step: how well the agent answers, not how it is written.

- skill: each question of `evals/questions.yaml` is answered by a model that gets the agent's instructions and
  skills (what a harness loads), then a judge model compares the answer with `expected` or `guideline`.
- no-code: the Genie space's own benchmarks through the Genie evaluation API, and regressions against the
  results of the production version (questions that were GOOD there and are BAD now).
- code: not run yet. The version has to be served to be asked; until then the step is reported as skipped.

Accuracy minimums follow the SPEC's service criticality. All values are policy defaults (`evaluate`).
Runs in the evaluate Job as the aops service principal.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from .. import package
from ..evals import read_questions, usable

DEFAULTS: dict[str, Any] = {
    "accuracy_min": {"high": 0.9, "medium": 0.85, "low": 0.8},
    "max_regressions": 0,
    "model": "databricks-claude-sonnet-4-6",        # answers the skill agent's questions
    "judge_model": "databricks-claude-sonnet-4-6",  # judges them
    "max_questions": 20,
    "timeout_sec": 2400,                            # Genie benchmark wait
}
JUDGE = """너는 에이전트 답변을 판정한다. 아래 질문에 대한 답변이 정답(또는 판정 기준)을 충족하면 GOOD, 아니면 BAD다.
숫자·사실이 정답과 다르거나, 판정 기준을 어기면 BAD다. 표현이 달라도 내용이 같으면 GOOD이다.
JSON 한 줄로만 답한다: {{"verdict": "GOOD" 또는 "BAD", "reason": "한 문장 이유"}}

질문: {question}
정답: {expected}
판정 기준: {guideline}
답변:
{answer}"""


class EvaluationError(Exception):
    """The evaluation could not run (model or Genie API failure, cancelled, timed out): the run is an error."""


def settings(policy: dict[str, Any]) -> dict[str, Any]:
    given = policy.get("evaluate") or {}
    return {**DEFAULTS, **given, "accuracy_min": {**DEFAULTS["accuracy_min"], **(given.get("accuracy_min") or {})}}


def threshold(config: dict[str, Any], spec: dict[str, str]) -> tuple[float, str]:
    crit = spec.get("service_criticality") if spec.get("service_criticality") in config["accuracy_min"] else "medium"
    return float(config["accuracy_min"][crit]), crit


def skill_prompt(files: dict[str, bytes]) -> str:
    """The agent as a harness would load it: its instructions, then each skill (name, description, body)."""
    parts = [files.get("agent/instructions.md", b"").decode("utf-8", "replace").strip()]
    for rel in sorted(files):
        if rel.startswith("agent/skills/") and rel.endswith("/SKILL.md"):
            text = files[rel].decode("utf-8", "replace")
            front = package.front_matter(text)
            body = re.sub(r"^﻿?---\n.*?\n---\n", "", text, count=1, flags=re.S).strip()
            parts.append(f"## 스킬 {front.get('name') or rel.split('/')[2]}\n언제 쓰나: {front.get('description') or ''}\n\n{body}")
    return "\n\n".join(p for p in parts if p) + "\n\n질문에 맞는 스킬이 있으면 그 규칙을 따라 한국어로 답한다."


def chat(w: Any, endpoint: str, system: str, user: str, max_tokens: int = 1500) -> str:
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

    try:
        response = w.serving_endpoints.query(name=endpoint, max_tokens=max_tokens, temperature=0.0, messages=[
            ChatMessage(role=ChatMessageRole.SYSTEM, content=system), ChatMessage(role=ChatMessageRole.USER, content=user)])
    except Exception as error:
        raise EvaluationError(f"모델 {endpoint}를 부르지 못했습니다: {type(error).__name__}: {str(error)[:200]}") from error
    content = response.choices[0].message.content if response.choices else ""
    if isinstance(content, list):  # some endpoints return content parts
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "").strip()


def parse_verdict(text: str) -> tuple[str, str]:
    match = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        data = {}
    verdict = str(data.get("verdict") or "").upper()
    if verdict not in ("GOOD", "BAD"):
        verdict = "GOOD" if re.search(r"\bGOOD\b", text) and not re.search(r"\bBAD\b", text) else "BAD"
    return verdict, str(data.get("reason") or text[:200])


def evaluate_skill(w: Any, files: dict[str, bytes], spec: dict[str, str], policy: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = settings(policy)
    raw = files.get("evals/questions.yaml")
    questions = usable(read_questions(raw.decode("utf-8", "replace"))) if raw else []
    if not questions:
        return {"name": "evaluate", "status": "failed",
                "detail": "평가셋이 없습니다: evals/questions.yaml에 질문과 expected(정답)나 guideline(판정 기준)을 적으세요 "
                          "(템플릿 질문은 세지 않습니다)"}, []
    questions = questions[: int(config["max_questions"])]
    system = skill_prompt(files)
    results = []
    for q in questions:
        answer = chat(w, config["model"], system, q["text"])
        verdict, reason = parse_verdict(chat(w, config["judge_model"], "판정만 한다.", JUDGE.format(
            question=q["text"], expected=q.get("expected") or "(없음)", guideline=q.get("guideline") or "(없음)", answer=answer), 300))
        results.append({"question_id": q["id"], "question": q["text"], "assessment": verdict, "reasons": [reason],
                        "expected": q.get("expected") or q.get("guideline"), "generated": answer})
    good = sum(r["assessment"] == "GOOD" for r in results)
    accuracy = good / len(results)
    minimum, crit = threshold(config, spec)
    ok = accuracy >= minimum
    bad = [r["question_id"] for r in results if r["assessment"] != "GOOD"]
    detail = (f"정확도 {accuracy:.0%} (기준 {minimum:.0%}, 중요도 {crit}) · {len(results)}문항 중 GOOD {good}"
              + (f" · BAD {', '.join(bad)}" if bad else "") + f" · 답 {config['model']}, 판정 {config['judge_model']}")
    return {"name": "evaluate", "status": "passed" if ok else "failed", "detail": detail,
            "metrics": {"accuracy": round(accuracy, 3), "threshold": minimum, "questions": float(len(results))}}, results


DONE, ENDED = "DONE", ("DONE", "EVALUATION_FAILED", "EVALUATION_CANCELLED", "EVALUATION_TIMEOUT")


def evaluate_genie(w: Any, space_id: str, spec: dict[str, str], policy: dict[str, Any], baseline: dict[str, str] | None = None,
                   sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the space's benchmarks and judge accuracy and regressions. `baseline`: question id → production assessment."""
    config = settings(policy)
    try:
        run = w.genie.genie_create_eval_run(space_id)
    except Exception as error:
        raise EvaluationError(f"Genie 벤치마크 평가를 시작하지 못했습니다: {type(error).__name__}: {str(error)[:200]}") from error
    run_id = run.eval_run_id
    deadline = clock() + float(config["timeout_sec"])
    while True:
        state = w.genie.genie_get_eval_run(space_id, run_id)
        status = state.eval_run_status.value if state.eval_run_status else "NOT_STARTED"
        if status in ENDED:
            break
        if clock() > deadline:
            raise EvaluationError(f"Genie 벤치마크 평가가 {int(config['timeout_sec'])}초 안에 끝나지 않았습니다 (eval run {run_id})")
        sleep(5)
    if status in ("EVALUATION_CANCELLED", "EVALUATION_TIMEOUT"):
        raise EvaluationError(f"Genie 벤치마크 평가 {status} (eval run {run_id}). 다시 요청하세요")
    results = []
    token = None
    while True:
        page = w.genie.genie_list_eval_results(space_id, run_id, page_token=token)
        for item in page.eval_results or []:
            detail = w.genie.genie_get_eval_result_details(space_id, run_id, item.result_id)
            assessment = detail.assessment.value if detail.assessment else "NEEDS_REVIEW"
            results.append({"question_id": item.benchmark_question_id, "question": item.question, "assessment": assessment,
                            "reasons": [r.value for r in (detail.assessment_reasons or [])],
                            "expected": (detail.expected_response or [None])[0].response if detail.expected_response else item.benchmark_answer,
                            "generated": (detail.actual_response or [None])[0].response if detail.actual_response else None})
        token = page.next_page_token
        if not token:
            break
    judged = [r for r in results if r["assessment"] in ("GOOD", "BAD")]
    good = sum(r["assessment"] == "GOOD" for r in judged)
    accuracy = good / len(judged) if judged else 0.0
    minimum, crit = threshold(config, spec)
    regressions = sorted(r["question_id"] for r in results
                         if (baseline or {}).get(r["question_id"]) == "GOOD" and r["assessment"] == "BAD")
    review = len(results) - len(judged)
    ok = status == DONE and bool(judged) and accuracy >= minimum and len(regressions) <= int(config["max_regressions"])
    detail = (f"벤치마크 {len(results)}문항: 정확도 {accuracy:.0%} (기준 {minimum:.0%}, 중요도 {crit}), 검토 필요 {review}, "
              f"회귀 {len(regressions)}" + (f" ({', '.join(regressions[:5])})" if regressions else "")
              + (" · 첫 배포라 회귀 비교 없음" if baseline is None else "" if baseline else " · 운영 버전의 평가 기록이 없어 회귀 비교 없음")
              + ("" if status == DONE else f" · 평가 상태 {status}")
              + ("" if judged else " · 판정된 문항이 없습니다 (정답 SQL이 있는 벤치마크가 필요합니다)"))
    return {"name": "evaluate", "status": "passed" if ok else "failed", "detail": detail,
            "metrics": {"accuracy": round(accuracy, 3), "threshold": minimum, "questions": float(len(results)),
                        "regressions": float(len(regressions)), "needs_review": float(review)},
            "eval_run_id": run_id}, results


def skipped_for_code() -> dict[str, Any]:
    return {"name": "evaluate", "status": "skipped",
            "detail": "코드 기반의 평가셋 실행은 다음 단계입니다 (새 버전을 띄워 물어볼 곳이 필요). 지금은 형식·프로젝트·비밀값을 검사합니다"}
