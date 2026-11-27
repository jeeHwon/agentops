"""Pieces every dbx module shares: the error the API turns into a problem response, the requester, ids."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")  # the init rule: volume names, paths and app names use it
CRITICALITY = {"low", "medium", "high"}
SENSITIVITY = {"public", "internal", "confidential", "personal"}


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, command: str | None = None, data: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.command, self.data = status, code, message, command, data


def clean_note(note: Any) -> str | None:
    """The registration note: trimmed, up to 500 characters, None when empty."""
    text = str(note or "").strip()
    return text[:500] or None


def gate_open_error(gate: dict[str, Any]) -> ServiceError:
    """One request moves at a time per agent: the 409 carries the open one (the screen offers to cancel it)."""
    return ServiceError(409, "gate_open", f"진행 중인 배포 요청이 있습니다 ({gate['version']}, {gate['gate_id']})", "aops deploy",
                        data={k: gate.get(k) for k in ("gate_id", "version", "decision", "requester", "deployment_status")})


@dataclass
class Caller:
    email: str
    token: str | None = None
    groups: list[str] = field(default_factory=list)
    # the workspace the token works in (its name in the policy, and its host); None: the server's own
    workspace: str | None = None
    host: str | None = None


def now_id() -> str:
    return uuid.uuid4().hex


# agent_versions columns an INSERT names (note is set apart by set_version_note: a registry without it still registers)
VERSION_COLUMNS = ("(agent_id, version, status, source_sha256, artifact_path, size_bytes, skills_sha256, harness, "
                   "uc_model_version, spec, registered_by, registered_at)")
