from aops.dbx.jobs import print_validation
from aops.dbx.service import SKILL_NAME, check_skill_agent

SPEC = b"---\nagent_id: a\nname: a\nbuild_type: skill\nservice_criticality: medium\ndata_sensitivity: internal\n---\n"


def skill(name, description):
    return f"---\nname: {name}\ndescription: {description}\n---\n".encode()


def test_skill_name_follows_agent_skills_rule():
    for good in ("a", "discount-policy", "a1-b2", "x" * 64):
        assert SKILL_NAME.match(good), good
    for bad in ("-a", "a-", "a--b", "A", "x" * 65, ""):
        assert not SKILL_NAME.match(bad), bad


def test_description_is_counted_in_characters():
    long_korean = "가" * 1000  # 3,000 bytes but 1,000 characters
    steps = check_skill_agent({"SPEC.md": SPEC, "agent/skills/k/SKILL.md": skill("k", long_korean)})
    assert {s["name"]: s["status"] for s in steps} == {"format": "passed", "skills": "passed", "secrets": "passed"}
    steps = check_skill_agent({"SPEC.md": SPEC, "agent/skills/k/SKILL.md": skill("k", "가" * 1025)})
    assert next(s for s in steps if s["name"] == "skills")["status"] == "failed"


def test_validation_report_shows_rules_and_files(capsys):
    steps = check_skill_agent({"SPEC.md": SPEC, "agent/skills/k/SKILL.md": skill("k", "쓸 때")})
    print_validation("r1", "cat.sch", {"agent_id": "a", "version": "1.0.0", "artifact_path": "/Volumes/c/s/a/1.0.0.zip",
                                       "sha256": "abc", "files": ["SPEC.md"], "steps": steps, "status": "passed"})
    out = capsys.readouterr().out
    assert "규칙: SPEC.md에 agent_id" in out and "검사한 파일" in out and "cat.sch.validation_runs" in out


def test_untouched_init_template_is_rejected(tmp_path):
    from aops import init, package

    project = init.create(tmp_path, "fresh", edition="dbx")
    files = package.read(package.build(project).data)
    steps = {s["name"]: s for s in check_skill_agent(files)}
    assert steps["format"]["status"] == "failed" and "템플릿" in steps["format"]["detail"]
    assert steps["skills"]["status"] == "failed" and "my-skill" in steps["skills"]["detail"]


def test_renamed_skill_with_template_description_is_rejected():
    from aops.init import SKILL_DESCRIPTION

    steps = check_skill_agent({"SPEC.md": SPEC, "agent/skills/k/SKILL.md": skill("k", SKILL_DESCRIPTION)})
    assert next(s for s in steps if s["name"] == "skills")["status"] == "failed"


def test_format_detail_names_only_the_problems():
    spec = b"---\nagent_id: a\nname: a\nbuild_type: skill\nservice_criticality: huge\ndata_sensitivity: internal\n---\n"
    step = check_skill_agent({"SPEC.md": spec, "agent/skills/k/SKILL.md": skill("k", "쓸 때")})[0]
    assert step["status"] == "failed" and step["detail"] == "SPEC.md: 허용되지 않는 값 service_criticality='huge'"


def test_env_templates_are_not_forbidden_but_their_content_is_scanned():
    from aops.dbx.service import find_secrets

    assert find_secrets({".env": b"A=1", "app/.env.local": b"A=1", "key.pem": b"x"}) == [".env (금지 파일)", "app/.env.local (금지 파일)", "key.pem (금지 파일)"]
    assert find_secrets({".env.example": b"DATABRICKS_HOST=\n", ".env.sample": b"", ".env.template": b""}) == []
    assert find_secrets({".env.example": b"TOKEN=dapi" + b"0" * 32}) == [".env.example (Databricks 토큰)"]
