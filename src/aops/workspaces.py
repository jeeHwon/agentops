"""The workspaces aops works across, from the policy. No Databricks calls here (validate and the server share it).

- home: the workspace the aops server runs in (its registry warehouse and Jobs). Always there.
- dev: where people build agents (folders, dev Genie spaces, Genie Code). Their own tokens work only there.
- target: where production resources go (production Genie spaces, production apps).

With no `workspaces` in the policy, home is dev and target too: one workspace, as before. Each listed workspace
names its roles; when none is dev (or target), home keeps that role.

    workspaces:
      personal: {host: https://adb-1.azuredatabricks.net, roles: [dev]}
      project:  {host: https://adb-2.azuredatabricks.net, roles: [dev, target], catalogs: [project], warehouse_id: abc}
    default_target: project

A request names the workspace its token belongs to (header `X-Aops-Workspace`); the server takes only hosts
listed here, so a token is never sent to a host it does not know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

HOME = "home"
ROLES = ("dev", "target")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
HEADER = "x-aops-workspace"


def normalize_host(url: str | None) -> str:
    """https://<host>[:port], lower case, without a path: how hosts are compared."""
    text = (url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"워크스페이스 주소는 https://<호스트>입니다: {url}")
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return f"https://{parsed.hostname.lower()}{port}"


@dataclass(frozen=True)
class Workspace:
    name: str
    host: str
    roles: tuple[str, ...] = ()
    catalogs: tuple[str, ...] = ()  # target: production data may come only from these catalogs (empty: any)
    warehouse_id: str | None = None  # target: the SQL warehouse its production Genie spaces use
    home: bool = False

    def has(self, role: str) -> bool:
        return role in self.roles

    def summary(self) -> dict[str, Any]:
        return {"name": self.name, "host": self.host, "roles": list(self.roles), "catalogs": list(self.catalogs),
                "warehouse_id": self.warehouse_id, "home": self.home}


def entries(policy: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = (policy or {}).get("workspaces") or {}
    return raw if isinstance(raw, dict) else {}


def check_entries(raw: dict[str, Any]) -> list[str]:
    """What is wrong with a `workspaces` value (for admin setup and the policy screen)."""
    problems = []
    hosts: dict[str, str] = {}
    for name, entry in raw.items():
        if not NAME.match(str(name)) or name == HOME:
            problems.append(f"이름 {name!r}: 영문 소문자·숫자·_·- 32자 이하 (home은 서버 워크스페이스 이름)")
        if not isinstance(entry, dict):
            problems.append(f"{name}: {{host, roles}} 형태여야 합니다")
            continue
        try:
            host = normalize_host(entry.get("host"))
        except ValueError as error:
            problems.append(f"{name}: {error}")
            continue
        if not host:
            problems.append(f"{name}: host가 없습니다")
        elif host in hosts:
            problems.append(f"{name}: {hosts[host]}와 주소가 같습니다")
        hosts[host] = name
        bad = [r for r in entry.get("roles") or [] if r not in ROLES]
        if bad or not entry.get("roles"):
            problems.append(f"{name}: roles는 {', '.join(ROLES)} 중 하나 이상입니다")
    return problems


@dataclass
class Workspaces:
    home_host: str
    listed: dict[str, Workspace] = field(default_factory=dict)
    default_target: str | None = None

    @classmethod
    def from_policy(cls, policy: dict[str, Any] | None, home_host: str) -> "Workspaces":
        try:
            home = normalize_host(home_host)
        except ValueError:
            home = ""
        listed = {}
        for name, entry in entries(policy).items():
            if not isinstance(entry, dict) or not NAME.match(str(name)) or name == HOME:
                continue
            try:
                host = normalize_host(entry.get("host"))
            except ValueError:
                continue
            if not host:
                continue
            listed[name] = Workspace(name, host, tuple(r for r in entry.get("roles") or [] if r in ROLES),
                                     tuple(str(c).lower() for c in entry.get("catalogs") or []), entry.get("warehouse_id") or None,
                                     home=host == home)
        return cls(home, listed, (policy or {}).get("default_target") or None)

    @property
    def configured(self) -> bool:
        """Workspaces are listed: more than the server's own workspace may be in play."""
        return bool(self.listed)

    @property
    def home(self) -> Workspace:
        mine = next((w for w in self.listed.values() if w.home), None)
        roles = tuple(r for r in ROLES if (mine and mine.has(r)) or not any(w.has(r) for w in self.listed.values()))
        if mine:
            return Workspace(mine.name, mine.host, roles, mine.catalogs, mine.warehouse_id, home=True)
        return Workspace(HOME, self.home_host, roles, home=True)

    def all(self) -> list[Workspace]:
        home = self.home
        return [home] + [w for w in self.listed.values() if w.host != home.host]

    def get(self, name: str | None) -> Workspace:
        """A workspace by name; none (or `home`) is the server's own. Unknown names raise KeyError."""
        if not name or name == HOME:
            return self.home
        for ws in self.all():
            if ws.name == name:
                return ws
        raise KeyError(name)

    def find(self, name: str | None) -> Workspace | None:
        try:
            return self.get(name)
        except KeyError:
            return None

    def by_host(self, host: str | None) -> Workspace | None:
        try:
            wanted = normalize_host(host)
        except ValueError:
            return None
        return next((w for w in self.all() if w.host == wanted), None)

    def with_role(self, role: str) -> list[Workspace]:
        return [w for w in self.all() if w.has(role)]

    def target(self, name: str | None = None) -> Workspace:
        """The production workspace: the one named (it must have the target role), else the policy default."""
        targets = self.with_role("target")
        if name:
            found = next((w for w in targets if w.name == name), None)
            if found is None:
                raise KeyError(name)
            return found
        if self.default_target:
            found = next((w for w in targets if w.name == self.default_target), None)
            if found:
                return found
        home = self.home
        return home if home.has("target") else targets[0]


def outside(sources: list[str] | set[str], catalogs: tuple[str, ...] | list[str]) -> list[str]:
    """The tables whose catalog is not one of `catalogs` (none when no catalogs are given)."""
    allowed = {c.lower().strip("`") for c in catalogs}
    if not allowed:
        return []
    return sorted(s for s in sources if s and s.split(".", 1)[0].strip("`").lower() not in allowed)


def spec_target(spec: dict[str, Any] | None) -> str | None:
    value = str((spec or {}).get("target_workspace") or "").strip()
    return value or None
