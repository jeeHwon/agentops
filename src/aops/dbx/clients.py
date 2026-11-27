"""Workspace clients: the server's own, and the aops service principal in the other workspaces of the policy.

The service principal reaches another workspace with OAuth M2M (one account-level service principal assigned
to each workspace). Its credentials come from, in order:

1. `AOPS_WORKSPACE_PROFILES=name=profile,...`: a server on a PC uses a Databricks CLI profile per workspace
2. the policy's `workspace_credentials` secret scope (client id and secret): the server and the Jobs read it
3. the server's own OAuth client (`DATABRICKS_CLIENT_ID`/`SECRET`, a container's service principal)

A person's token is used only in the workspace it came from (see `same_workspace`).
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

from ..workspaces import Workspace, Workspaces
from .base import Caller, ServiceError

WORKSPACES_TTL = 30  # seconds the policy's workspaces are kept (the policy is read from the registry)


def workspace_profiles() -> dict[str, str]:
    out = {}
    for part in (os.environ.get("AOPS_WORKSPACE_PROFILES") or "").split(","):
        name, _, profile = part.partition("=")
        if name.strip() and profile.strip():
            out[name.strip()] = profile.strip()
    return out


class WorkspacesMixin:
    """Service methods that pick the workspace a call goes to."""

    w: Any
    config: Any

    def workspaces(self) -> Workspaces:
        cached = self.__dict__.get("_workspaces")
        if cached and time.time() - cached[0] < WORKSPACES_TTL:
            return cached[1]
        home = getattr(getattr(getattr(self, "w", None), "config", None), "host", None) or ""
        spaces = Workspaces.from_policy(self.policy()[1], home)  # type: ignore[attr-defined]
        self.__dict__["_workspaces"] = (time.time(), spaces)
        return spaces

    def workspace(self, name: str | None) -> Workspace:
        try:
            return self.workspaces().get(name)
        except KeyError:
            raise ServiceError(409, "unknown_workspace", f"정책에 없는 워크스페이스입니다: {name}", "aops admin setup --workspace") from None

    @staticmethod
    def stored(ws: Workspace) -> str | None:
        """How a workspace is kept in the registry: its name, or nothing for the server's own (rows stay as before)."""
        return None if ws.home else ws.name

    def client(self, where: Workspace | str | None = None) -> Any:
        """The aops service principal in that workspace (the server's own client for home)."""
        ws = where if isinstance(where, Workspace) else self.workspace(where)
        if ws.home:
            return self.w
        cache = self.__dict__.setdefault("_clients", {})
        if ws.host not in cache:
            from databricks.sdk import WorkspaceClient

            cache[ws.host] = WorkspaceClient(**self._credentials(ws))
        return cache[ws.host]

    def _credentials(self, ws: Workspace) -> dict[str, Any]:
        profile = workspace_profiles().get(ws.name)
        if profile:
            return {"profile": profile}
        settings = self.policy()[1].get("workspace_credentials") or {}  # type: ignore[attr-defined]
        if settings.get("secret_scope"):
            scope = settings["secret_scope"]
            return {"host": ws.host, "auth_type": "oauth-m2m",
                    "client_id": self._secret(scope, settings.get("client_id_key") or "client-id"),
                    "client_secret": self._secret(scope, settings.get("client_secret_key") or "client-secret")}
        config = self.w.config
        if getattr(config, "client_id", None) and getattr(config, "client_secret", None):
            return {"host": ws.host, "auth_type": "oauth-m2m", "client_id": config.client_id, "client_secret": config.client_secret}
        raise ServiceError(503, "no_workspace_credentials",
                           f"{ws.name} 워크스페이스({ws.host})를 부를 aops 서비스 주체 자격이 없습니다. "
                           "관리자가 정책에 시크릿 범위를 정합니다(aops admin setup --workspace-secret-scope)", "aops admin setup --workspace-secret-scope")

    def _secret(self, scope: str, key: str) -> str:
        try:
            value = self.w.secrets.get_secret(scope, key).value
        except Exception as error:
            raise ServiceError(503, "no_workspace_credentials", f"시크릿 {scope}/{key}를 읽지 못했습니다 ({type(error).__name__}). "
                               "aops 서비스 주체에 그 범위의 READ가 있어야 합니다", "aops admin setup --workspace-secret-scope") from error
        return base64.b64decode(value or "").decode("utf-8").strip()

    def cli_env(self, ws: Workspace) -> tuple[str | None, dict[str, str]]:
        """(profile, environment) for a CLI (agentbricks) that acts in `ws` as the aops service principal."""
        if ws.home:
            return self.config.profile, {}
        credentials = self._credentials(ws)
        if "profile" in credentials:
            return credentials["profile"], {}
        return None, {"DATABRICKS_HOST": credentials["host"], "DATABRICKS_CLIENT_ID": credentials["client_id"],
                      "DATABRICKS_CLIENT_SECRET": credentials["client_secret"], "DATABRICKS_AUTH_TYPE": "oauth-m2m"}

    # ── where a person's request came from ──────────────────────────────────

    def caller_workspace(self, caller: Caller) -> Workspace:
        if caller.host:
            found = self.workspaces().by_host(caller.host)
            if found:
                return found
        return self.workspace(caller.workspace)

    def user_host(self, caller: Caller) -> str:
        """Where the requester's token works (the server's own workspace unless the request named another)."""
        return (caller.host or self.w.config.host).rstrip("/")

    def require_dev(self, caller: Caller, command: str) -> Workspace:
        """Making something new in the requester's workspace: it has to be a development workspace."""
        ws = self.caller_workspace(caller)
        if not ws.has("dev"):
            names = ", ".join(w.name for w in self.workspaces().with_role("dev"))
            raise ServiceError(409, "not_dev_workspace", f"{ws.name} 워크스페이스는 개발용이 아닙니다. "
                               f"개발 워크스페이스({names})의 Genie Code·CLI에서 만드세요", command,
                               data={"workspace": ws.name, "dev": names})
        return ws

    def same_workspace(self, caller: Caller, name: str | None, what: str, command: str) -> Workspace:
        """Work on something in workspace `name` with the requester's own token: only with a token from there."""
        ws = self.workspace(name)
        mine = self.caller_workspace(caller)
        if ws.host != mine.host:
            raise ServiceError(409, "wrong_workspace", f"{what}은(는) {ws.name} 워크스페이스({ws.host})에 있습니다. 내 권한으로 다루려면 "
                               f"그 워크스페이스의 Genie Code·CLI에서 하세요 (이 요청은 {mine.name}에서 왔습니다)", command,
                               data={"workspace": ws.name, "host": ws.host, "from": mine.name})
        return ws

    def registry_ready(self) -> None:
        """With more than one workspace the registry keeps where things are: the columns admin setup adds."""
        if not self.workspaces().configured or self.__dict__.get("_registry_ready"):
            return
        sql = self.sql  # type: ignore[attr-defined]
        try:
            sql.run(f"SELECT dev_workspace, prod_workspace FROM {sql.table('genie_agents')} LIMIT 0")
            sql.run(f"SELECT workspace FROM {sql.table('agent_production')} LIMIT 0")
        except Exception as error:
            raise ServiceError(409, "registry_outdated", "여러 워크스페이스를 쓰려면 레지스트리에 위치 열이 있어야 합니다. "
                               "관리자가 aops admin setup --only tables를 실행합니다", "aops admin setup --only tables") from error
        self.__dict__["_registry_ready"] = True
