"""The agent's evaluation set: `evals/questions.yaml`.

A small, fixed shape (no YAML library needed):

    questions:
      - id: q1
        text: 단가 3,000원짜리 50개 사면 합계가 얼마야?
        expected: 할인 후 공급가액 135,000원, 부가세 13,500원, 합계 148,500원
        guideline: 표로 답하고 적용한 할인율을 적는다

`expected` (the answer that counts as correct) and `guideline` (how to judge) are optional; a question
needs at least one of them for the Gate's `evaluate` step to judge it.
"""

from __future__ import annotations

TEMPLATE_TEXT = "여기에 질문을 적습니다"
FIELDS = ("id", "text", "expected", "guideline")


def _value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value.split(" #", 1)[0].strip()


def read_questions(text: str) -> list[dict[str, str]]:
    """The questions, in file order. Lines it does not understand are skipped."""
    questions: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "questions:":
            continue
        if stripped.startswith("- "):
            current = {}
            questions.append(current)
            stripped = stripped[2:].strip()
        if current is None or ":" not in stripped:
            continue
        key, raw = stripped.split(":", 1)
        if key.strip() in FIELDS:
            current[key.strip()] = _value(raw)
    out = []
    for i, q in enumerate(questions, 1):
        if q.get("text"):
            out.append({**q, "id": q.get("id") or f"q{i}"})
    return out


def usable(questions: list[dict[str, str]]) -> list[dict[str, str]]:
    """Questions the Gate can judge: not the template line, and with an expected answer or a guideline."""
    return [q for q in questions if q["text"] != TEMPLATE_TEXT and (q.get("expected") or q.get("guideline"))]
