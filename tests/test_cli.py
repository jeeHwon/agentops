import pytest

from aops import __version__
from aops.cli import main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"aops {__version__}"


def test_unknown_harness_is_one_line_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["session", "switch", "--harness", "nope"]) == 2
    assert capsys.readouterr().err.strip().startswith("aops: unknown harness 'nope'")


def test_init_creates_a_skill_agent(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "price-helper"]) == 0
    assert (tmp_path / "price-helper" / ".claude/skills/my-skill/SKILL.md").is_file()
    assert main(["init", "Bad Name"]) == 2


def test_deploy_stops_when_a_person_has_to_decide():
    from aops.lifecycle import person_decides

    pending = {"decision": "pending_approval", "approval_mode": "auto", "decision_reason": None}
    assert not person_decides(pending)  # the policy is about to approve
    assert person_decides({**pending, "decision_reason": "검사 실패 (검사 방식 optional, 승인자 판단): …"})
    assert person_decides({**pending, "approval_mode": "required"})
    assert person_decides({**pending, "approval_mode": "optional"})
    assert not person_decides({**pending, "decision": "approved", "approval_mode": "required"})


def test_admin_setup_adds_user_scopes_with_an_app_update_and_checks_them(capsys):
    from types import SimpleNamespace

    from aops import admin

    state = {"scopes": ["files"]}
    calls = []

    def create_update_and_wait(name, mask, app=None, timeout=None):
        calls.append((name, mask, app.user_api_scopes))
        if "bogus" in app.user_api_scopes:
            raise RuntimeError("Invalid scope: bogus")
        state["scopes"] = app.user_api_scopes

    apps = SimpleNamespace(create_update_and_wait=create_update_and_wait,
                           get=lambda name: SimpleNamespace(user_api_scopes=list(state["scopes"])))
    app = SimpleNamespace(name="aops-server", user_api_scopes=["files"], effective_user_api_scopes=["files", "iam.current-user:read"])
    admin._ensure_user_scopes(SimpleNamespace(apps=apps), app)
    assert calls[0] == ("aops-server", "user_api_scopes", ["files", "genie", "workspace.workspace"])
    assert state["scopes"] == ["files", "genie", "workspace.workspace"] and "더함: genie, workspace.workspace" in capsys.readouterr().out

    state["scopes"] = ["files"]
    original = admin.USER_SCOPES
    admin.USER_SCOPES = ("files", "genie", "bogus")
    try:
        admin._ensure_user_scopes(SimpleNamespace(apps=apps), SimpleNamespace(name="a", user_api_scopes=["files"], effective_user_api_scopes=[]))
    finally:
        admin.USER_SCOPES = original
    out = capsys.readouterr().out
    assert state["scopes"] == ["files", "genie"] and "더하지 못했습니다: bogus (Invalid scope: bogus)" in out
