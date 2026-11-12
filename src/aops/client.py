"""The CLI side of the dbx edition: requests to the aops server with the user's Databricks login."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from .project import Project


class ServerError(Exception):
    def __init__(self, status: int, body: dict[str, Any]):
        detail = body.get("title") or body.get("detail") or str(body)
        super().__init__(detail)
        self.status, self.body = status, body


DEFAULT_APP = "aops-server"


def in_notebook() -> bool:
    """Running inside Databricks (a notebook, or a cell Genie Code runs), not on a PC."""
    return bool(os.environ.get("DATABRICKS_RUNTIME_VERSION"))


class Server:
    def __init__(self, project: Project | None = None, *, url: str | None = None, profile: str | None = None):
        dbx = project.dbx if project else {}
        self.notebook = in_notebook()
        self.app = os.environ.get("AOPS_APP") or dbx.get("app") or DEFAULT_APP
        self.url = (url or os.environ.get("AOPS_SERVER") or dbx.get("server") or "").rstrip("/")
        # A PC profile means nothing inside Databricks: there the notebook's own login is used.
        self.profile = None if self.notebook else (profile or os.environ.get("AOPS_PROFILE") or dbx.get("profile") or None)
        self._token: str | None = None
        if not self.url and self.notebook:
            self.url = self._app().url.rstrip("/")
        if not self.url:
            raise ValueError("aops 서버 주소가 없습니다. aops.toml [dbx] server 또는 AOPS_SERVER를 정하세요")

    def _app(self) -> Any:
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient().apps.get(self.app)

    def _headers(self) -> dict[str, str]:
        from databricks.sdk import WorkspaceClient

        if not self.notebook:
            return WorkspaceClient(profile=self.profile).config.authenticate()
        if self._token is None:
            self._token = notebook_app_token(self._app().oauth2_app_client_id)
        return {"Authorization": f"Bearer {self._token}"}

    def request(self, method: str, path: str, *, body: Any = None, data: bytes | None = None,
                content_type: str | None = None, raw: bool = False) -> Any:
        headers = dict(self._headers())
        if body is not None:
            data, content_type = json.dumps(body).encode(), "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(f"{self.url}/api/v1{path}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                payload = resp.read()
                return payload if raw else (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as error:
            if error.code == 401 and self.notebook and self._token is not None:
                self._token = None  # the exchanged token expired: exchange again, once
                return self.request(method, path, body=body, data=data if body is None else None,
                                    content_type=content_type if body is None else None, raw=raw)
            text = error.read()
            try:
                problem = json.loads(text)
            except ValueError:
                problem = {"title": text.decode(errors="replace")[:300]}
            raise ServerError(error.code, problem) from None

    def upload(self, path: str, fields: dict[str, str | None], file_field: str, filename: str, content: bytes) -> Any:
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in fields.items():
            if value is not None:
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                     "Content-Type: application/zip\r\n\r\n".encode() + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return self.request("POST", path, data=b"".join(parts), content_type=f"multipart/form-data; boundary={boundary}")


def notebook_app_token(audience: str) -> str:
    """An OAuth token for the aops server app, for the user of this notebook.

    Databricks Apps take OAuth tokens only; the notebook's own token gets a 401. Exchanging it for a
    token whose audience is the app keeps the user's identity (the server records who asked).
    """
    import urllib.parse

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.runtime import dbutils

    subject = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()
    form = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": subject,
        "subject_token_type": "urn:databricks:params:oauth:token-type:personal-access-token",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "scope": "all-apis",
        "audience": audience,
    }).encode()
    host = WorkspaceClient().config.host.rstrip("/")
    req = urllib.request.Request(f"{host}/oidc/v1/token", data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]
