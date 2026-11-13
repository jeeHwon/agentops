import io
import json
from types import SimpleNamespace

import pytest

from aops import package
from aops.evals import read_questions, usable

pytest.importorskip("databricks.sdk")  # the dbx extra

from aops.dbx import gate_eval, nocode  # noqa: E402
from aops.dbx.service import DEFAULT_POLICY, Caller, Config, Service, ServiceError  # noqa: E402

SPEC = "---\nagent_id: a\nname: a\nbuild_type: skill\nservice_criticality: low\ndata_sensitivity: internal\ndescription: 할인 계산\n---\n"
SKILL = "---\nname: discount\ndescription: 할인율을 물을 때 쓴다\n---\n\n10개 이상 5% 할인\n"
QUESTIONS = """# 평가셋
questions:
  - id: q1
    text: 10개면 할인율은?
    expected: 5%
  - id: q2
    text: "3개면?"
    guideline: 할인이 없다고 답한다
  - id: q3
    text: 여기에 질문을 적습니다
    expected: 이 질문의 정답으로 볼 답을 적습니다
  - id: q4
    text: 판정 기준이 없는 질문
"""


def skill_files(questions=QUESTIONS):
    files = {"SPEC.md": SPEC.encode(), "aops.toml": b'[project]\nname = "a"\n', "agent/instructions.md": "할인 도우미".encode(),
             "agent/skills/discount/SKILL.md": SKILL.encode()}
    if questions is not None:
        files["evals/questions.yaml"] = questions.encode()
    return files


def test_eval_set_reads_the_fixed_shape_and_skips_unjudgeable_questions():
    questions = read_questions(QUESTIONS)
    assert [q["id"] for q in questions] == ["q1", "q2", "q3", "q4"]
    assert questions[1]["text"] == "3개면?" and questions[1]["guideline"] == "할인이 없다고 답한다"
    assert [q["id"] for q in usable(questions)] == ["q1", "q2"]  # q3 is the template, q4 has no expected or guideline


def serving(answers, verdicts):
    calls = []

    def query(name, max_tokens, temperature, messages):
        calls.append((name, messages[0].content, messages[1].content))
        text = verdicts.pop(0) if "판정만" in messages[0].content else answers.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    return SimpleNamespace(serving_endpoints=SimpleNamespace(query=query)), calls


def test_skill_evaluation_answers_with_the_skills_and_judges_against_the_expected_answer():
    w, calls = serving(["5% 할인입니다", "할인 없음"], ['{"verdict": "GOOD", "reason": "맞음"}', 'BAD: 틀림'])
    step, results = gate_eval.evaluate_skill(w, skill_files(), {"service_criticality": "low"}, {})
    assert "10개 이상 5% 할인" in calls[0][1] and "## 스킬 discount" in calls[0][1]  # the agent's skill is in the prompt
    assert [r["assessment"] for r in results] == ["GOOD", "BAD"]
    assert step["status"] == "failed" and step["metrics"]["accuracy"] == 0.5 and step["metrics"]["threshold"] == 0.8
    w, _ = serving(["5%", "없음"], ['{"verdict":"GOOD"}', '{"verdict":"GOOD"}'])
    assert gate_eval.evaluate_skill(w, skill_files(), {"service_criticality": "high"}, {})[0]["status"] == "passed"


def test_skill_evaluation_needs_a_real_eval_set():
    for questions in (None, "questions:\n  - id: q1\n    text: 여기에 질문을 적습니다\n    expected: x\n"):
        step, results = gate_eval.evaluate_skill(SimpleNamespace(), skill_files(questions), {}, {})
        assert step["status"] == "failed" and "평가셋이 없습니다" in step["detail"] and results == []


def test_policy_overrides_thresholds_and_models():
    config = gate_eval.settings({"evaluate": {"accuracy_min": {"low": 0.5}, "model": "m"}})
    assert config["accuracy_min"] == {"high": 0.9, "medium": 0.85, "low": 0.5} and config["model"] == "m"
    assert gate_eval.threshold(config, {"service_criticality": "nonsense"}) == (0.85, "medium")


def genie(states, results):
    from databricks.sdk.service.dashboards import EvaluationStatusType, GenieEvalAssessment

    def details(space, run, result_id):
        r = results[result_id]
        return SimpleNamespace(assessment=GenieEvalAssessment(r[1]), assessment_reasons=[],
                               expected_response=[SimpleNamespace(response="SELECT 1")], actual_response=[SimpleNamespace(response="SELECT 2")])

    return SimpleNamespace(genie=SimpleNamespace(
        genie_create_eval_run=lambda space: SimpleNamespace(eval_run_id="run1"),
        genie_get_eval_run=lambda space, run: SimpleNamespace(eval_run_status=EvaluationStatusType(states.pop(0) if len(states) > 1 else states[0])),
        genie_list_eval_results=lambda space, run, page_token=None: SimpleNamespace(next_page_token=None, eval_results=[
            SimpleNamespace(result_id=k, benchmark_question_id=v[0], question=f"질문 {v[0]}", benchmark_answer=None) for k, v in results.items()]),
        genie_get_eval_result_details=details))


def test_genie_benchmarks_count_accuracy_without_needs_review_and_catch_regressions():
    w = genie(["RUNNING", "DONE"], {"r1": ("b1", "GOOD"), "r2": ("b2", "GOOD"), "r3": ("b3", "NEEDS_REVIEW"), "r4": ("b4", "BAD")})
    step, results = gate_eval.evaluate_genie(w, "s", {"service_criticality": "low"}, {}, baseline={"b4": "GOOD"}, sleep=lambda s: None)
    assert step["metrics"]["accuracy"] == round(2 / 3, 3) and step["metrics"]["needs_review"] == 1
    assert step["metrics"]["regressions"] == 1 and "b4" in step["detail"] and step["status"] == "failed"
    assert results[0]["expected"] == "SELECT 1" and results[0]["generated"] == "SELECT 2"
    w = genie(["DONE"], {"r1": ("b1", "GOOD"), "r2": ("b2", "GOOD"), "r3": ("b3", "GOOD"), "r4": ("b4", "GOOD"), "r5": ("b5", "BAD")})
    step, _ = gate_eval.evaluate_genie(w, "s", {"service_criticality": "low"}, {}, baseline=None, sleep=lambda s: None)
    assert step["status"] == "passed" and "첫 배포" in step["detail"]  # 80% meets low


def test_genie_cancel_or_timeout_is_an_error_not_a_failure():
    with pytest.raises(gate_eval.EvaluationError):
        gate_eval.evaluate_genie(genie(["EVALUATION_CANCELLED"], {}), "s", {}, {}, sleep=lambda s: None)
    ticks = iter(range(0, 10_000, 1000))
    with pytest.raises(gate_eval.EvaluationError) as error:
        gate_eval.evaluate_genie(genie(["RUNNING"], {}), "s", {}, {"evaluate": {"timeout_sec": 2500}}, sleep=lambda s: None, clock=lambda: next(ticks))
    assert "2500초" in str(error.value)


# ── the Gate run and the self-check in the service ──────────────────────────

class Sql:
    def __init__(self):
        self.statements, self.atomics = [], []

    def table(self, name):
        return f"c.s.{name}"

    def run(self, statement, params=None):
        self.statements.append((statement, params or {}))
        return []

    def update(self, statement, params=None):
        return 1

    def one(self, statement, params=None):
        return None

    def atomic(self, statements):
        self.atomics.append(list(statements))


def gate_service(files, kind="skill"):
    data = package.build_entries([(r, d, 0o644) for r, d in files.items()]).data
    sha = package.files_sha256(list(files.items()))
    svc = object.__new__(Service)
    svc.config = Config("c.s", "wh")
    svc.sql = Sql()
    svc.w = SimpleNamespace(files=SimpleNamespace(download=lambda path: SimpleNamespace(contents=io.BytesIO(data))))
    svc.policy = lambda: (1, dict(DEFAULT_POLICY))
    svc.agent = lambda a: {"agent_id": a, "build_type": kind, "owner": "dev@x.com", "owner_group": None, "spec": None}
    svc.version = lambda a, v: {"version": "1.0.0", "source_sha256": sha, "artifact_path": "/Volumes/c/s/a/1.0.0.zip"}
    run = {"validation_run_id": "r1", "agent_id": "a", "version": "1.0.0", "source_sha256": sha, "started_by": "dev@x.com",
           "steps": [], "checks": {}, "policy_version": 1}
    svc.evaluation = lambda run_id, results=False: dict(run)
    svc._event_statement = lambda agent_id, version, kind_, actor, detail=None: (f"EVENT {kind_}", detail)
    return svc


def test_gate_runs_the_form_checks_then_the_evaluation_and_records_each_question(monkeypatch):
    svc = gate_service(skill_files())
    monkeypatch.setattr(gate_eval, "evaluate_skill", lambda w, files, spec, policy: (
        {"name": "evaluate", "status": "passed", "detail": "정확도 100%"},
        [{"question_id": "q1", "question": "10개면?", "assessment": "GOOD", "reasons": ["맞음"], "expected": "5%", "generated": "5%"}]))
    report = svc.run_evaluation("r1")
    assert [s["name"] for s in report["steps"]] == ["format", "skills", "secrets", "evaluate", "record"] and report["status"] == "passed"
    statements = [s for s, _ in svc.sql.atomics[0]]
    assert any(s.startswith("INSERT INTO c.s.eval_results") for s in statements) and "EVENT evaluate" in statements


def test_gate_does_not_spend_on_evaluation_when_the_form_fails(monkeypatch):
    files = skill_files()
    files["SPEC.md"] = SPEC.replace("service_criticality: low", "service_criticality: huge").encode()
    svc = gate_service(files)
    monkeypatch.setattr(gate_eval, "evaluate_skill", lambda *a: pytest.fail("evaluated a malformed agent"))
    report = svc.run_evaluation("r1")
    evaluate = next(s for s in report["steps"] if s["name"] == "evaluate")
    assert evaluate["status"] == "skipped" and report["status"] == "failed"


def test_code_agents_skip_the_quality_step_for_now():
    files = {"SPEC.md": SPEC.replace("build_type: skill", "build_type: code").encode(), "aops.toml": b"",
             "agent.toml": b'[agent]\nframework = "openai"\nserver = "agentbricks"\n', "app.yaml": b"command: []\n",
             "pyproject.toml": b"[project]\nname='a'\n", "uv.lock": b""}
    report = gate_service(files, kind="code").run_evaluation("r1")
    evaluate = next(s for s in report["steps"] if s["name"] == "evaluate")
    assert evaluate["status"] == "skipped" and "다음 단계" in evaluate["detail"]


def test_no_code_gate_runs_benchmarks_on_the_dev_space_then_integrity(monkeypatch):
    spec = SPEC.replace("build_type: skill", "build_type: no_code")
    definition = nocode.initial_space(["main.s.t"], "질문")
    files = nocode.bundle("a", spec, definition)
    svc = gate_service(files, kind="no_code")
    svc.version = lambda a, v: None  # a snapshot: no version yet
    svc.genie_agent = lambda a: {"dev_space_id": "dev1"}
    svc._eval_baseline = lambda a: None
    asked = []
    monkeypatch.setattr(gate_eval, "evaluate_genie", lambda w, space, spec_, policy, baseline: (
        asked.append(space) or {"name": "evaluate", "status": "passed", "detail": "ok"}, []))
    monkeypatch.setattr(nocode, "check_steps", lambda files_, policy, fmt: [fmt, {"name": "definition", "status": "passed", "detail": ""}])
    svc.integrity_step = lambda agent_id, files_: {"name": "integrity", "status": "passed", "detail": ""}
    report = svc.run_evaluation("r1")
    assert asked == ["dev1"] and [s["name"] for s in report["steps"]] == ["format", "definition", "evaluate", "integrity", "record"]


def test_self_check_reads_an_upload_and_records_nothing():
    files = skill_files()
    svc = gate_service(files)
    svc.agent = lambda a: None  # not registered yet: an upload still works
    data = package.build_entries([(r, d, 0o644) for r, d in files.items()]).data
    result = svc.self_check(Caller("dev@x.com"), "a", data=data)
    assert result["status"] == "passed" and [s["name"] for s in result["steps"]] == ["format", "skills", "secrets"]
    assert svc.sql.statements == [] and svc.sql.atomics == []
    with pytest.raises(ServiceError) as error:
        svc.self_check(Caller("dev@x.com"), "a")  # nothing to check and nothing registered
    assert error.value.status == 404


def test_self_check_of_a_registered_version_is_for_owners():
    svc = gate_service(skill_files())
    with pytest.raises(ServiceError) as error:
        svc.self_check(Caller("other@x.com"), "a", version="1.0.0")
    assert error.value.status == 403
    assert svc.self_check(Caller("dev@x.com"), "a", version="1.0.0")["source"] == "등록 버전 1.0.0"


def test_cli_validate_checks_the_folder_without_a_server(tmp_path, monkeypatch, capsys):
    from aops import init
    from aops.cli import main

    init.create(tmp_path, "fresh", edition="dbx")
    monkeypatch.chdir(tmp_path / "fresh")
    monkeypatch.delenv("AOPS_SERVER", raising=False)
    assert main(["validate"]) == 1  # the untouched template
    out = capsys.readouterr().out
    assert "기록하지 않음" in out and "✗ skills" in out


def test_old_jobs_still_run_the_gate(monkeypatch):
    from aops.dbx import jobs

    ran = []
    monkeypatch.setattr(jobs, "Service", lambda config: SimpleNamespace(run_evaluation=lambda run_id: ran.append(run_id) or {
        "agent_id": "a", "version": "1", "status": "passed", "steps": [], "files": [], "results": []}))
    assert jobs.main(["--kind", "validate", "--id", "r9", "--registry", "c.s", "--warehouse", "w"]) == 0
    assert ran == ["r9"]
    assert json.loads(json.dumps(gate_eval.DEFAULTS))["max_regressions"] == 0
