"""Version zips: what `register` uploads and what `load` unpacks.

The zip holds the agent folder (aops.toml, SPEC.md, instructions, skills, tools, evals). Harness
settings made from aops.toml and local records stay out. The hash covers the files inside, not the
zip bytes, with the same rule as skill folders, so it does not change with zip details.
"""

from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .project import Project

EXCLUDE_DIRS = {"runs", ".aops", ".claude", ".agents", ".codex", ".git", "__pycache__", ".venv", "node_modules"}
EXCLUDE_FILES = {"CLAUDE.md", "AGENTS.md", ".DS_Store"}
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass
class Archive:
    data: bytes
    sha256: str
    files: list[str]


def files_sha256(files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for rel, data in sorted(files):
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(str(len(data)).encode())
        digest.update(b"\0")
        digest.update(data)
    return digest.hexdigest()


def collect(project: Project) -> list[tuple[str, bytes, int]]:
    """(relative path, content, file mode) for every file that goes into the version."""
    out = []
    for path in sorted(project.root.rglob("*")):
        rel = path.relative_to(project.root)
        if path.is_file() and included(rel.as_posix()):
            out.append((rel.as_posix(), path.read_bytes(), stat.S_IMODE(path.stat().st_mode)))
    return out


def included(rel: str) -> bool:
    """Whether a file (path relative to the agent folder) goes into the version zip."""
    if not safe_name(rel):
        return False
    parts = PurePosixPath(rel).parts
    if not parts or any(p in EXCLUDE_DIRS for p in parts):
        return False
    return not (len(parts) == 1 and parts[0] in EXCLUDE_FILES or parts[-1] == ".DS_Store")


def build(project: Project) -> Archive:
    return build_entries(collect(project))


def build_entries(entries: list[tuple[str, bytes, int]]) -> Archive:
    """The version zip from (relative path, content, file mode); same bytes and hash as `build` for the same files."""
    entries = sorted(entries)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data, mode in entries:
            info = zipfile.ZipInfo(rel, FIXED_TIME)
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return Archive(buffer.getvalue(), files_sha256([(r, d) for r, d, _ in entries]), [r for r, _, _ in entries])


def safe_name(name: str) -> bool:
    """A zip entry name that stays inside the folder it is unpacked into."""
    if not name or "\\" in name or name.startswith("/") or ":" in name.split("/", 1)[0]:
        return False
    parts = name.split("/")
    return all(p not in ("", ".", "..") for p in parts)


def read(data: bytes) -> dict[str, bytes]:
    """The files in a version zip. Refuses names that would leave the folder (they never come from the CLI)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [info for info in zf.infolist() if not info.is_dir()]
        bad = [info.filename for info in members if not safe_name(info.filename)]
        if bad:
            raise ValueError(f"unsafe path in the version zip: {bad[0]}")
        return {info.filename: zf.read(info) for info in members}


def unpack(data: bytes, dest: Path, expected_sha256: str | None = None) -> list[str]:
    """Write the files into `dest` and restore file modes. Refuses paths that leave `dest`."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [info for info in zf.infolist() if not info.is_dir()]
        contents = [(info.filename, zf.read(info)) for info in members]
        if expected_sha256 and files_sha256(contents) != expected_sha256:
            raise ValueError("the version zip does not match its recorded hash")
        root = dest.resolve()
        for info, (name, content) in zip(members, contents):
            rel = PurePosixPath(name)
            target = (root / rel).resolve()
            if rel.is_absolute() or ".." in rel.parts or not str(target).startswith(str(root)):
                raise ValueError(f"unsafe path in the version zip: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                target.chmod(mode)
    return [name for name, _ in contents]


FRONT = re.compile(r"^---\n(.*?)\n---", re.S)


def front_matter(text: str) -> dict[str, str]:
    """`key: value` lines between the leading `---` markers (comments after `#` are dropped)."""
    match = FRONT.match(text.lstrip("﻿"))
    values: dict[str, str] = {}
    if not match:
        return values
    for line in match.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.split(" #", 1)[0].strip()
    return values
