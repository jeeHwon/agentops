"""Pieces every dbx module shares: the error the API turns into a problem response, the requester, ids."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")  # the init rule: volume names, paths and app names use it
CRITICALITY = {"low", "medium", "high"}
SENSITIVITY = {"public", "internal", "confidential", "personal"}


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, command: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.command = status, code, message, command


@dataclass
class Caller:
    email: str
    token: str | None = None
    groups: list[str] = field(default_factory=list)


def now_id() -> str:
    return uuid.uuid4().hex
