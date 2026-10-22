"""Check public PyPI URLs and normalize an explicitly selected mirror offline."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit


PUBLIC_REGISTRY = "https://pypi.org/simple"
PUBLIC_PACKAGE_ORIGIN = "https://files.pythonhosted.org"
PACKAGE_PATH = re.compile(r"/packages/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]+/[A-Za-z0-9_.+-]+")
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def _parse(contents: str) -> dict:
    data = tomllib.loads(contents)
    packages = data.get("package")
    if not isinstance(packages, list) or not packages:
        raise ValueError("Expected package records in the lockfile")
    for index, package in enumerate(packages):
        if not isinstance(package, dict):
            raise ValueError(f"package[{index}] is not a package record")
        source = package.get("source")
        if source != {"editable": "."} and (
            not isinstance(source, dict) or set(source) != {"registry"}
        ):
            raise ValueError(f"package[{index}].source is not a registry or the root project")
    return data


def _url_fields(value, path: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            location = f"{path}.{key}" if path else key
            if key in {"registry", "url"}:
                yield value, key, location
            else:
                yield from _url_fields(item, location)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _url_fields(item, f"{path}[{index}]")


def _url(value: str):
    if not isinstance(value, str):
        raise ValueError("Expected a URL string")
    parts = urlsplit(value)
    if (
        parts.scheme != "https" or not parts.hostname or parts.username is not None
        or parts.password is not None or parts.port is not None or parts.query or parts.fragment
    ):
        raise ValueError("Expected an HTTPS URL without credentials, port, query or fragment")
    return parts


def _artifact_path(value: str, origins: set[str]) -> str:
    parts = _url(value)
    if f"{parts.scheme}://{parts.netloc}" not in origins or not PACKAGE_PATH.fullmatch(parts.path):
        raise ValueError("Expected a known PyPI origin and a /packages/ artifact path")
    return parts.path


def _check(data: dict) -> list[str]:
    failures = []
    for entry, key, location in _url_fields(data):
        value = entry[key]
        if key == "registry":
            if value != PUBLIC_REGISTRY:
                failures.append(f"{location}: registry is not canonical public PyPI")
        else:
            try:
                _artifact_path(value, {PUBLIC_PACKAGE_ORIGIN})
                if not isinstance(entry.get("hash"), str) or not SHA256.fullmatch(entry["hash"]):
                    raise ValueError("Expected a SHA256 artifact hash")
            except ValueError:
                failures.append(f"{location}: expected a public PyPI artifact URL and SHA256 hash")
    return failures


def check_uv_lock(lockfile: Path) -> list[str]:
    return _check(_parse(lockfile.read_text(encoding="utf-8")))


def normalize(contents: str, mirror_url: str) -> tuple[str, dict[str, int]]:
    mirror = _url(mirror_url)
    if mirror.path.rstrip("/") != "/simple":
        raise ValueError("The selected mirror must use a /simple index")
    mirror_registry = mirror_url.rstrip("/")
    mirror_origin = f"{mirror.scheme}://{mirror.netloc}"
    expected = _parse(contents)
    replacements = {}
    counts = {"registry": 0, "url": 0}
    for entry, key, location in _url_fields(expected):
        value = entry[key]
        if key == "registry":
            if not isinstance(value, str) or value.rstrip("/") not in {
                mirror_registry, PUBLIC_REGISTRY,
            }:
                raise ValueError(f"{location}: registry is not public PyPI or the selected mirror")
            public_value = PUBLIC_REGISTRY
        else:
            try:
                path = _artifact_path(value, {mirror_origin, PUBLIC_PACKAGE_ORIGIN})
                if not isinstance(entry.get("hash"), str) or not SHA256.fullmatch(entry["hash"]):
                    raise ValueError("Expected a SHA256 artifact hash")
            except ValueError:
                raise ValueError(f"{location}: unsupported artifact URL or missing SHA256 hash") from None
            public_value = PUBLIC_PACKAGE_ORIGIN + path
        if value != public_value:
            replacements[value] = public_value
            entry[key] = public_value
            counts[key] += 1

    # Preserve generated formatting, then verify that only parsed URL fields changed.
    normalized = contents
    for original, public_value in replacements.items():
        for literal in (json.dumps(original), "'" + original + "'"):
            normalized = normalized.replace(literal, json.dumps(public_value))
    if tomllib.loads(normalized) != expected or _check(expected):
        raise ValueError("Normalization would change unrelated metadata or leave non-public URLs")
    return normalized, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lockfile", nargs="?", type=Path, default=Path("uv.lock"))
    parser.add_argument("--fix", action="store_true", help="normalize the selected mirror without network access")
    parser.add_argument("--mirror-url", help="approved mirror index URL used to generate the lockfile")
    args = parser.parse_args(argv)
    if bool(args.mirror_url) != args.fix:
        parser.error("--fix and --mirror-url must be supplied together")
    try:
        if args.fix:
            original = args.lockfile.read_text(encoding="utf-8")
            contents, counts = normalize(original, args.mirror_url)
            if contents != original:
                args.lockfile.write_text(contents, encoding="utf-8")
            print(f"Normalized {counts['registry']} registries and {counts['url']} artifact URLs")
        failures = check_uv_lock(args.lockfile)
    except (OSError, ValueError) as error:
        print(f"Lockfile check failed: {error}", file=sys.stderr)
        return 1
    if failures:
        for failure in failures:
            print(f"{args.lockfile}: {failure}", file=sys.stderr)
        return 1
    print("uv.lock public PyPI URL check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
