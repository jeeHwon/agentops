"""No-code (Genie Agent) definition checks: the Gate's `format` step for a Genie space snapshot.

The rules are the existing no-code Gate's (model A), on the field paths measured from real
`serialized_space` payloads (version 2): `data_sources.tables[]` with `column_configs[]`,
`instructions.{text_instructions, example_question_sqls, sql_snippets, join_specs, sql_functions}`,
top-level `benchmarks.questions[]`. Levels and values are policy defaults an admin can change.
Pure functions: no Databricks calls, so the server can run the same check as the Job.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

PII_PATTERNS = ("card", "ssn", "jumin", "resident", "phone", "mobile", "email", "address", "passport", "birth",
                "주민", "연락처", "전화", "휴대", "이메일", "주소", "여권", "계좌", "생년")
SECTIONS = ("## PURPOSE", "## CONSTRAINTS", "Instructions you must follow when providing summaries")

# id: level (error | warn | off), values, and where to fix it in the Genie screen
RULES: dict[str, dict[str, Any]] = {
    "sources.count": {"level": "error", "min": 1, "max": 30, "fix": "Data → 테이블 추가·정리 (Genie 한도 30개)"},
    "columns.pii_hidden": {"level": "error", "fix": "Data → 테이블 → 컬럼 숨김(Hide)"},
    "snippets.qualified": {"level": "error", "fix": "Instructions → SQL Expressions를 테이블.컬럼 형태로"},
    "examples.count": {"level": "error", "min": 5, "fix": "Instructions → SQL Queries에 예제 질문·SQL 추가"},
    "text.single": {"level": "error", "max": 1, "fix": "Instructions → Text는 하나로 합치기"},
    "benchmarks.min": {"level": "error", "min": 5, "fix": "Benchmarks → Add benchmark"},
    "snippets.limit": {"level": "error", "max_snippets": 200, "max_chars": 25000, "fix": "Instructions → 스니펫·텍스트 줄이기"},
    "sources.focused": {"level": "warn", "max": 5, "fix": "Data → 꼭 필요한 테이블만"},
    "sources.description": {"level": "warn", "fix": "Data → 테이블 설명"},
    "columns.entity_matching_scope": {"level": "warn", "fix": "Data → 컬럼 설정 → ID형 컬럼의 값 매칭 끄기"},
    "columns.synonyms": {"level": "warn", "fix": "Data → 컬럼 설정 → 동의어"},
    "joins.defined": {"level": "warn", "fix": "Instructions → Joins"},
    "text.length": {"level": "warn", "max": 2000, "fix": "Instructions → Text 2,000자 이하로"},
    "text.sections": {"level": "warn", "fix": "Instructions → Text 표준 템플릿(PURPOSE·CONSTRAINTS·요약 지침)"},
    "sources.allowed": {"level": "off", "catalogs": [], "fix": "Data → 허용된 카탈로그·스키마의 테이블만"},
}


def definition_hash(space: dict[str, Any]) -> str:
    """The snapshot's identity: sorted, compact JSON (the same rule as the existing no-code Gate)."""
    return hashlib.sha256(json.dumps(space, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return "".join(value) if isinstance(value, list) else str(value or "")


def _is_pii(name: str) -> bool:
    lowered = name.lower()
    return any(p in lowered for p in PII_PATTERNS)


def _is_id(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(("id", "_no", "_key")) or "uuid" in lowered or "hash" in lowered


def lint(space: dict[str, Any], rules: dict[str, dict[str, Any]] | None = None,
         pii_columns: set[str] | None = None) -> dict[str, Any]:
    """Check a definition. `pii_columns` (catalog.schema.table.column) come from UC tags when the caller has them.

    Returns {"score", "errors", "results": [{id, level, passed, message, fix}]}. Rules set to `off` or that
    do not apply (e.g. joins with one table) are left out of the score.
    """
    rules = {k: {**v, **((rules or {}).get(k) or {})} for k, v in RULES.items()}
    sources = space.get("data_sources") or {}
    tables = sources.get("tables") or []
    metric_views = sources.get("metric_views") or []
    instructions = space.get("instructions") or {}
    texts = instructions.get("text_instructions") or []
    text = "\n".join(_text(t.get("content")) for t in texts)
    examples = instructions.get("example_question_sqls") or []
    snippets = [s for kind in ("measures", "filters", "expressions") for s in ((instructions.get("sql_snippets") or {}).get(kind) or [])]
    joins = instructions.get("join_specs") or []
    benchmarks = (space.get("benchmarks") or {}).get("questions") or []
    columns = [(t.get("identifier", ""), c) for t in tables for c in (t.get("column_configs") or [])]
    count = len(tables) + len(metric_views)

    checks: list[tuple[str, bool | None, str]] = []  # (rule, passed or None = not applicable, message)
    r = rules["sources.count"]
    checks.append(("sources.count", r["min"] <= count <= r["max"], f"데이터 소스 {count}개 (허용 {r['min']}~{r['max']})"))

    tagged = {c.lower() for c in (pii_columns or set())}
    shown = [f"{t}.{c.get('column_name')}" for t, c in columns
             if (_is_pii(str(c.get("column_name", ""))) or f"{t}.{c.get('column_name')}".lower() in tagged) and not c.get("exclude")]
    configured = {f"{t}.{c.get('column_name')}".lower() for t, c in columns}
    shown += sorted(c for c in tagged if c not in configured and any(c.startswith(f"{t.get('identifier', '').lower()}.") for t in tables))
    checks.append(("columns.pii_hidden", not shown, "개인정보 컬럼이 모두 숨겨져 있습니다" if not shown else f"숨김 필요: {', '.join(shown[:5])}"))

    unqualified = [s.get("id", "?") for s in snippets if not re.search(r"\b\w+\.\w+\b", _text(s.get("sql")))]
    checks.append(("snippets.qualified", None if not snippets else not unqualified,
                   "SQL 표현식이 테이블.컬럼 형태입니다" if not unqualified else f"테이블.컬럼 형태가 아닌 표현식 {len(unqualified)}개"))
    checks.append(("examples.count", len(examples) >= rules["examples.count"]["min"], f"예제 SQL {len(examples)}개 (최소 {rules['examples.count']['min']})"))
    checks.append(("text.single", len(texts) <= rules["text.single"]["max"], f"Text Instruction {len(texts)}개"))
    checks.append(("benchmarks.min", len(benchmarks) >= rules["benchmarks.min"]["min"], f"벤치마크 {len(benchmarks)}개 (최소 {rules['benchmarks.min']['min']})"))
    r = rules["snippets.limit"]
    over = len(snippets) > r["max_snippets"] or len(text) > r["max_chars"]
    checks.append(("snippets.limit", not over, f"스니펫 {len(snippets)}개, 텍스트 {len(text):,}자 (한도 {r['max_snippets']}개·{r['max_chars']:,}자)"))
    checks.append(("sources.focused", count <= rules["sources.focused"]["max"], f"데이터 소스 {count}개 (권장 {rules['sources.focused']['max']}개 이하)"))
    bare = [t.get("identifier", "?") for t in tables if not _text(t.get("description")).strip()]
    checks.append(("sources.description", not bare, "모든 테이블에 설명이 있습니다" if not bare else f"설명 없음: {', '.join(bare[:5])}"))
    ids = [(t, c) for t, c in columns if _is_id(str(c.get("column_name", "")))]
    matched = [f"{t}.{c.get('column_name')}" for t, c in ids if c.get("enable_entity_matching")]
    checks.append(("columns.entity_matching_scope", None if not ids else not matched,
                   "ID형 컬럼의 값 매칭이 꺼져 있습니다" if not matched else f"값 매칭을 끌 ID형 컬럼: {', '.join(matched[:5])}"))
    with_synonyms = sum(1 for _, c in columns if c.get("synonyms"))
    checks.append(("columns.synonyms", with_synonyms >= 1, f"동의어가 있는 컬럼 {with_synonyms}개"))
    checks.append(("joins.defined", None if count < 2 else len(joins) >= 1, f"조인 {len(joins)}개 (데이터 소스 {count}개)"))
    checks.append(("text.length", None if not texts else len(text) <= rules["text.length"]["max"], f"Text Instruction {len(text):,}자"))
    missing = [s for s in SECTIONS if s.lower() not in text.lower()]
    checks.append(("text.sections", not missing, "표준 섹션 3개가 있습니다" if not missing else f"빠진 섹션: {', '.join(missing)}"))
    allowed = [a.lower() for a in rules["sources.allowed"].get("catalogs") or []]
    outside = [t.get("identifier", "") for t in tables if allowed and not any(t.get("identifier", "").lower().startswith(a) for a in allowed)]
    checks.append(("sources.allowed", None if not allowed else not outside, "허용 범위의 데이터만 씁니다" if not outside else f"허용 밖: {', '.join(outside[:5])}"))

    results = []
    for rule, passed, message in checks:
        level = rules[rule]["level"]
        if level == "off" or passed is None:
            continue
        results.append({"id": rule, "level": level.upper(), "passed": bool(passed), "message": message, "fix": rules[rule]["fix"]})
    score = sum(r["passed"] for r in results) / len(results) if results else 1.0
    errors = sum(1 for r in results if r["level"] == "ERROR" and not r["passed"])
    return {"score": round(score, 3), "errors": errors, "results": results}


def format_step(space: dict[str, Any], lint_min: float = 0.9, **kwargs: Any) -> dict[str, Any]:
    """The Gate's `format` step for a no-code agent: no ERROR rule broken and the score at or above `lint_min`."""
    report = lint(space, **kwargs)
    failed = [r for r in report["results"] if not r["passed"]]
    ok = report["errors"] == 0 and report["score"] >= lint_min
    detail = (f"준수율 {report['score']:.0%} (기준 {lint_min:.0%}), ERROR 위반 {report['errors']}건"
              + ("" if not failed else " · " + "; ".join(f"{r['id']}: {r['message']}" for r in failed[:6])))
    return {"name": "format", "status": "passed" if ok else "failed", "detail": detail, "rules": report["results"], "score": report["score"]}
