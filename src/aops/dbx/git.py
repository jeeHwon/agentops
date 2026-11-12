"""Git mirror (optional): every registered version as a commit and a tag in the agent's own repository.

The volume zip stays the record; Git is a copy people can browse and diff. GitHub is called through
a Unity Catalog HTTP connection's proxy, so the token is kept in Unity Catalog and only principals
with USE CONNECTION on that connection (the aops service principal) can use it. No clone: the Git
data REST API builds each commit from the version's files.

Layout of an agent repository (`<repo_prefix><agent_id>`, private):
- `main`: one commit per registered version, a full snapshot of the version zip (deleted files go away)
- tags `v<version>` on those commits
- `production`: the commit of the version in production (moves on deploy and on rollback)
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

PROXY = "/api/2.0/unity-catalog/connections/{connection}/proxy"
PRODUCTION = "production"


class GitError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub {status}: {message}")
        self.status = status


@dataclass(frozen=True)
class GitSettings:
    connection: str  # Unity Catalog HTTP connection to the Git host's API (bearer token in a secret)
    owner: str  # organisation that holds the agent repositories
    repo_prefix: str = "aops-"
    mode: str = "commit"  # commit: versions go straight onto main

    @classmethod
    def from_policy(cls, policy: dict[str, Any]) -> GitSettings | None:
        git = policy.get("git") or {}
        if not git.get("connection") or not git.get("owner"):
            return None
        return cls(git["connection"], git["owner"], git.get("repo_prefix") or "aops-", git.get("mode") or "commit")

    def repo(self, agent_id: str) -> str:
        return f"{self.repo_prefix}{agent_id}"


Transport = Callable[[str, str, Any], tuple[int, Any]]


def proxy_transport(workspace: Any, connection: str) -> Transport:
    """Calls through the connection proxy with the caller's own Databricks identity."""
    base = workspace.config.host.rstrip("/") + PROXY.format(connection=connection)

    def call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
        headers = {**workspace.config.authenticate(), "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                return error.code, json.loads(raw) if raw else None
            except ValueError:
                return error.code, {"message": raw[:200].decode("utf-8", "replace")}

    return call


class GitHub:
    def __init__(self, settings: GitSettings, transport: Transport, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.call = transport
        self.sleep = sleep

    def _ok(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200, 201)) -> Any:
        status, data = self.call(method, path, body)
        if status not in ok:
            raise GitError(status, (data or {}).get("message", "") if isinstance(data, dict) else str(data))
        return data

    def ensure_repo(self, agent_id: str, description: str | None) -> dict[str, Any]:
        """The agent's repository; made private on the first version (with a README so `main` exists)."""
        owner, name = self.settings.owner, self.settings.repo(agent_id)
        status, data = self.call("GET", f"/repos/{owner}/{name}", None)
        if status == 200:
            return data
        if status != 404:
            raise GitError(status, (data or {}).get("message", ""))
        return self._ok("POST", f"/orgs/{owner}/repos", {
            "name": name, "private": True, "auto_init": True,
            "description": (description or f"aops 에이전트 {agent_id}")[:350] + " (aops가 관리: 등록한 버전의 사본)",
        })

    def _ref(self, repo: str, ref: str) -> str | None:
        status, data = self.call("GET", f"/repos/{repo}/git/ref/{ref}", None)
        if status == 200:
            return data["object"]["sha"]
        if status in (404, 409):  # 409: the repository has no commits yet
            return None
        raise GitError(status, (data or {}).get("message", ""))

    def tag_commit(self, repo: str, version: str) -> str | None:
        return self._ref(repo, f"tags/v{version}")

    def publish(self, repo_row: dict[str, Any], version: str, files: dict[str, bytes], message: str,
                author_email: str) -> str:
        """Commit the version's files on the default branch and tag it `v<version>`; the commit SHA.

        Already tagged (an earlier try got that far): the tagged commit, nothing new is written.
        """
        repo, branch = repo_row["full_name"], repo_row.get("default_branch") or "main"
        tagged = self.tag_commit(repo, version)
        if tagged:
            return tagged
        tree = self._ok("POST", f"/repos/{repo}/git/trees", {"tree": self._entries(repo, files)})["sha"]
        author = {"name": author_email.split("@")[0], "email": author_email,
                  "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        for attempt in range(3):
            head = self._head(repo, branch)
            commit = self._ok("POST", f"/repos/{repo}/git/commits",
                              {"message": message, "tree": tree, "parents": [head] if head else [], "author": author})["sha"]
            if head is None:
                status, data = self.call("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit})
            else:
                status, data = self.call("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", {"sha": commit, "force": False})
            if status in (200, 201):
                break
            if status != 422 or attempt == 2:  # 422: someone moved the branch meanwhile; read it again
                raise GitError(status, (data or {}).get("message", ""))
        self._ok("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/tags/v{version}", "sha": commit})
        return commit

    def _head(self, repo: str, branch: str) -> str | None:
        # Right after the repository is made, its first commit can take a moment to show.
        for attempt in range(5):
            head = self._ref(repo, f"heads/{branch}")
            if head or attempt == 4:
                return head
            self.sleep(1 + attempt)
        return None

    def _entries(self, repo: str, files: dict[str, bytes]) -> list[dict[str, Any]]:
        entries = []
        for rel, data in sorted(files.items()):
            text = _text(data)
            if text is not None:
                entries.append({"path": rel, "mode": "100644", "type": "blob", "content": text})
            else:
                blob = self._ok("POST", f"/repos/{repo}/git/blobs",
                                {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})["sha"]
                entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob})
        return entries

    def move_production(self, repo: str, commit: str) -> None:
        """Point the `production` branch at the commit (forward on deploy, back on rollback)."""
        status, data = self.call("PATCH", f"/repos/{repo}/git/refs/heads/{PRODUCTION}", {"sha": commit, "force": True})
        if status in (404, 422):  # no such branch yet
            self._ok("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{PRODUCTION}", "sha": commit})
        elif status not in (200, 201):
            raise GitError(status, (data or {}).get("message", ""))


def _text(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def commit_message(agent_id: str, version: dict[str, Any]) -> str:
    return (f"{agent_id} {version['version']}\n\n"
            f"Aops-Version: {version['version']}\n"
            f"Aops-Sha256: {version.get('source_sha256') or ''}\n"
            f"Aops-Registered-By: {version.get('registered_by') or ''}\n")
