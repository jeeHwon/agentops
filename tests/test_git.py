import argparse
import base64
from types import SimpleNamespace

import pytest

from aops.dbx.git import GitHub, GitSettings, commit_message

SETTINGS = GitSettings(connection="git_conn", owner="org")
REPO = {"full_name": "org/aops-a", "default_branch": "main", "html_url": "https://github.com/org/aops-a"}


class FakeGitHub:
    """Answers GitHub REST calls from a table of (method, path) → (status, body); records every call."""

    def __init__(self, answers):
        self.answers = {k: list(v) if isinstance(v, list) else [v] for k, v in answers.items()}
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        queue = self.answers.get((method, path))
        if not queue:
            return 404, {"message": "Not Found"}
        return queue.pop(0) if len(queue) > 1 else queue[0]


def hub(answers):
    fake = FakeGitHub(answers)
    return GitHub(SETTINGS, fake, sleep=lambda s: None), fake


def test_settings_come_from_the_policy():
    assert GitSettings.from_policy({}) is None
    assert GitSettings.from_policy({"git": {"connection": "c"}}) is None  # no owner: off
    settings = GitSettings.from_policy({"git": {"connection": "c", "owner": "o"}})
    assert (settings.repo("price-helper"), settings.mode) == ("aops-price-helper", "commit")


def test_first_version_makes_the_repo_and_commits_a_full_snapshot():
    github, fake = hub({
        ("POST", "/orgs/org/repos"): (201, REPO),
        ("GET", "/repos/org/aops-a/git/ref/heads/main"): (200, {"object": {"sha": "readme"}}),
        ("POST", "/repos/org/aops-a/git/blobs"): (201, {"sha": "blob1"}),
        ("POST", "/repos/org/aops-a/git/trees"): (201, {"sha": "tree1"}),
        ("POST", "/repos/org/aops-a/git/commits"): (201, {"sha": "c1"}),
        ("PATCH", "/repos/org/aops-a/git/refs/heads/main"): (200, {}),
        ("POST", "/repos/org/aops-a/git/refs"): (201, {}),
    })
    repo = github.ensure_repo("a", "할인 계산")
    assert fake.calls[1][2]["private"] is True and fake.calls[1][2]["auto_init"] is True
    commit = github.publish(repo, "1.0.0", {"SPEC.md": "설명".encode(), "agent/logo.png": b"\x89PNG\0\1"},
                            "a 1.0.0", "dev@x.com")
    assert commit == "c1"
    tree = next(body for method, path, body in fake.calls if path.endswith("/git/trees"))
    assert "base_tree" not in tree  # a full snapshot: files removed in this version go away
    assert {"path": "SPEC.md", "mode": "100644", "type": "blob", "content": "설명"} in tree["tree"]
    assert {"path": "agent/logo.png", "mode": "100644", "type": "blob", "sha": "blob1"} in tree["tree"]
    blob = next(body for method, path, body in fake.calls if path.endswith("/git/blobs"))
    assert base64.b64decode(blob["content"]) == b"\x89PNG\0\1"
    made = next(body for method, path, body in fake.calls if path.endswith("/git/commits"))
    assert made["parents"] == ["readme"] and made["author"]["email"] == "dev@x.com"
    assert fake.calls[-1] == ("POST", "/repos/org/aops-a/git/refs", {"ref": "refs/tags/v1.0.0", "sha": "c1"})


def test_a_version_already_tagged_is_not_written_again():
    github, fake = hub({("GET", "/repos/org/aops-a/git/ref/tags/v1.0.0"): (200, {"object": {"sha": "c0"}})})
    assert github.publish(REPO, "1.0.0", {"SPEC.md": b"x"}, "m", "dev@x.com") == "c0"
    assert [method for method, _, _ in fake.calls] == ["GET"]


def test_a_moved_branch_is_read_again():
    github, fake = hub({
        ("GET", "/repos/org/aops-a/git/ref/heads/main"): [(200, {"object": {"sha": "h1"}}), (200, {"object": {"sha": "h2"}})],
        ("POST", "/repos/org/aops-a/git/trees"): (201, {"sha": "t"}),
        ("POST", "/repos/org/aops-a/git/commits"): [(201, {"sha": "c1"}), (201, {"sha": "c2"})],
        ("PATCH", "/repos/org/aops-a/git/refs/heads/main"): [(422, {"message": "Update is not a fast forward"}), (200, {})],
        ("POST", "/repos/org/aops-a/git/refs"): (201, {}),
    })
    assert github.publish(REPO, "1.0.1", {"SPEC.md": b"x"}, "m", "dev@x.com") == "c2"
    parents = [body["parents"] for method, path, body in fake.calls if path.endswith("/git/commits")]
    assert parents == [["h1"], ["h2"]]


def test_production_branch_is_made_then_moved():
    github, fake = hub({
        ("PATCH", "/repos/org/aops-a/git/refs/heads/production"): (422, {"message": "Reference does not exist"}),
        ("POST", "/repos/org/aops-a/git/refs"): (201, {}),
    })
    github.move_production("org/aops-a", "c1")
    assert fake.calls[0][2] == {"sha": "c1", "force": True}  # force: a rollback moves it back
    assert fake.calls[1] == ("POST", "/repos/org/aops-a/git/refs", {"ref": "refs/heads/production", "sha": "c1"})


def test_commit_message_carries_the_aops_record():
    message = commit_message("a", {"version": "1.1.0", "source_sha256": "abc", "registered_by": "dev@x.com"})
    assert message.startswith("a 1.1.0\n\n") and "Aops-Sha256: abc" in message and "Aops-Registered-By: dev@x.com" in message


# ── service: register and deploy keep going whatever Git does ────────────────

pytest.importorskip("databricks.sdk")  # the dbx extra
from aops.dbx.service import Service  # noqa: E402


class RecordingSql:
    def __init__(self, git_rows=()):
        self.git_rows = list(git_rows)
        self.written = []

    def table(self, name):
        return f"c.s.{name}"

    def run(self, statement, params=None):
        return list(self.git_rows) if "agent_version_git" in statement and statement.startswith("SELECT") else []

    def atomic(self, statements):
        self.written.append(statements)


def git_service(versions, files, answers, git_rows=()):
    svc = object.__new__(Service)
    svc.sql = RecordingSql(git_rows)
    svc.versions = lambda agent_id: versions
    svc.agent = lambda agent_id: {"description": "d"}
    svc.w = SimpleNamespace(files=SimpleNamespace(download=lambda path: SimpleNamespace(
        contents=SimpleNamespace(read=lambda: pytest.fail("downloaded a version it already had")))))
    github, fake = hub(answers)
    return svc, github, fake


def statuses(svc):
    return [params["st"] for block in svc.sql.written for stmt, params in block if "agent_version_git" in stmt]


def test_a_version_with_secrets_never_goes_to_git():
    versions = [{"version": "1.0.0", "artifact_path": "/v/1.0.0.zip", "registered_by": "dev@x.com"}]
    svc, github, fake = git_service(versions, None, {})
    records, error = svc.sync_git("a", "dev@x.com", known={"1.0.0": {".env": b"KEY=1"}}, hub=github)
    assert records["1.0.0"]["status"] == "skipped" and ".env" in records["1.0.0"]["error"] and error is None
    assert fake.calls == []  # not even the repository is made


def test_git_failure_is_recorded_not_raised():
    versions = [{"version": "1.0.0", "artifact_path": "/v/1.0.0.zip", "registered_by": "dev@x.com"}]
    svc, github, fake = git_service(versions, None, {("POST", "/orgs/org/repos"): (403, {"message": "Resource not accessible"})})
    records, error = svc.sync_git("a", "dev@x.com", known={"1.0.0": {"SPEC.md": b"x"}}, hub=github)
    assert records["1.0.0"]["status"] == "failed" and "403" in error
    assert statuses(svc) == ["failed"]


def test_versions_that_missed_git_go_first_in_order():
    versions = [{"version": v, "artifact_path": f"/v/{v}.zip", "registered_by": "dev@x.com"} for v in ("1.0.0", "1.0.1", "1.1.0")]
    answers = {
        ("GET", "/repos/org/aops-a"): (200, REPO),
        ("GET", "/repos/org/aops-a/git/ref/heads/main"): (200, {"object": {"sha": "h"}}),
        ("POST", "/repos/org/aops-a/git/trees"): (201, {"sha": "t"}),
        ("POST", "/repos/org/aops-a/git/commits"): [(201, {"sha": "c1"}), (201, {"sha": "c2"})],
        ("PATCH", "/repos/org/aops-a/git/refs/heads/main"): (200, {}),
        ("POST", "/repos/org/aops-a/git/refs"): (201, {}),
    }
    svc, github, fake = git_service(versions, None, answers, git_rows=[{"version": "1.0.0", "status": "synced"}])
    known = {"1.0.1": {"SPEC.md": b"1"}, "1.1.0": {"SPEC.md": b"2"}}
    records, error = svc.sync_git("a", "dev@x.com", known=known, hub=github)
    tags = [body["ref"] for method, path, body in fake.calls if body and str(body.get("ref", "")).startswith("refs/tags/")]
    assert tags == ["refs/tags/v1.0.1", "refs/tags/v1.1.0"] and error is None
    assert records["1.1.0"]["url"] == "https://github.com/org/aops-a/tree/v1.1.0"


def test_no_git_in_the_policy_means_nothing_happens():
    svc = object.__new__(Service)
    svc.policy = lambda: (1, {"validation_mode": "mandatory"})
    assert svc.sync_git("a", "dev@x.com") is None and svc.git_production("a", "1.0.0", "dev@x.com") is None


# ── admin setup: Git settings in the policy ──────────────────────────────────

def policy_args(**git):
    base = {"validation_mode": "mandatory", "approval_mode": "auto", "approver_group": "",
            "git_connection": None, "git_owner": None, "git_repo_prefix": None, "no_git": False}
    return argparse.Namespace(**{**base, **git})


def test_policy_keeps_the_git_settings_unless_told():
    from aops.admin import policy_body

    current = {"git": {"provider": "github", "connection": "c", "owner": "o", "repo_prefix": "aops-", "mode": "commit"}}
    assert policy_body(policy_args(), current)["git"] == current["git"]  # `policy --approval-mode …` keeps Git
    assert "git" not in policy_body(policy_args(no_git=True), current)
    new = policy_body(policy_args(git_connection="c2", git_owner="o2"), current)["git"]
    assert (new["connection"], new["owner"], new["mode"]) == ("c2", "o2", "commit")
    with pytest.raises(SystemExit):
        policy_body(policy_args(git_connection="c2"), None)
