from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass


class DatabricksError(RuntimeError):
    pass


@dataclass(frozen=True)
class Profile:
    name: str
    host: str


def list_profiles() -> list[Profile]:
    try:
        process = subprocess.run(
            ["databricks", "auth", "profiles", "--skip-validate", "-o", "json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        raw = json.loads(process.stdout)
    except FileNotFoundError as exc:
        raise DatabricksError("Databricks CLI is not installed or not on PATH.") from exc
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise DatabricksError(f"Cannot list Databricks profiles: {exc}") from exc
    profiles: list[Profile] = []
    for item in raw.get("profiles", []):
        name = str(item.get("name", "")).strip()
        host = str(item.get("host", "")).strip().rstrip("/")
        if name and host.startswith("https://") and "accounts.cloud.databricks.com" not in host:
            profiles.append(Profile(name, host))
    return profiles


def choose_profile(profiles: list[Profile], *, input_fn=input, print_fn=print) -> Profile:
    if not profiles:
        raise DatabricksError("No Databricks workspace profiles were found.")
    print_fn("Databricks Profile을 선택하세요:")
    for index, profile in enumerate(profiles, start=1):
        print_fn(f"  {index}. {profile.name}  {profile.host}")
    while True:
        answer = input_fn("번호: ").strip()
        try:
            selected = int(answer)
        except ValueError:
            selected = 0
        if 1 <= selected <= len(profiles):
            return profiles[selected - 1]
        print_fn("목록의 번호를 입력하세요.")


def get_profile(name: str, profiles: list[Profile] | None = None) -> Profile:
    candidates = profiles if profiles is not None else list_profiles()
    match = next((item for item in candidates if item.name == name), None)
    if match is None:
        raise DatabricksError(f"Databricks profile not found: {name}")
    return match


def validate_profile(profile: Profile) -> str:
    try:
        from databricks.sdk import WorkspaceClient

        user = WorkspaceClient(profile=profile.name).current_user.me()
    except Exception as exc:
        raise DatabricksError(f"Databricks authentication failed for {profile.name}: {exc}") from exc
    identity = getattr(user, "user_name", None) or getattr(user, "display_name", None)
    return str(identity or "authenticated user")


def get_default_warehouse(profile_name: str) -> str:
    try:
        process = subprocess.run(
            [
                "databricks",
                "experimental",
                "aitools",
                "tools",
                "get-default-warehouse",
                "--profile",
                profile_name,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise DatabricksError(f"Cannot resolve the default SQL warehouse: {exc}") from exc
    warehouse_id = process.stdout.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", warehouse_id):
        raise DatabricksError("Databricks returned an invalid default SQL warehouse ID.")
    return warehouse_id
