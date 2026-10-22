from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_uv_lock_public_urls", ROOT / "scripts/check_uv_lock_public_urls.py",
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)

MIRROR = "https://mirror.example.com"
PACKAGE_PATH = "/packages/ab/cd/" + "e" * 60 + "/example_package-1.0.0-py3-none-any.whl"
HASH = "sha256:" + "a" * 64


def lockfile(index: str, origin: str) -> str:
    return f"""\
version = 1
revision = 2
requires-python = ">=3.11"

[[package]]
name = "example-package"
version = "1.0.0"
source = {{ registry = "{index}" }}
dependencies = [
    {{ name = "example-dependency", version = "2.0.0", source = {{ registry = "{index}" }} }},
]
wheels = [
    {{ url = "{origin}{PACKAGE_PATH}", hash = "{HASH}", size = 1234 }},
]
"""


PUBLIC_LOCK = lockfile(checker.PUBLIC_REGISTRY, checker.PUBLIC_PACKAGE_ORIGIN)
MIRROR_LOCK = lockfile(MIRROR + "/simple/", MIRROR)


def test_repository_lockfile_contains_only_public_pypi_urls():
    assert checker.check_uv_lock(ROOT / "uv.lock") == []


def test_check_rejects_mirrored_registry_and_artifact_urls(tmp_path):
    target = tmp_path / "uv.lock"
    target.write_text(MIRROR_LOCK)
    failures = checker.check_uv_lock(target)
    assert len(failures) == 3
    assert any("dependencies[0].source.registry" in failure for failure in failures)
    assert any("wheels[0].url" in failure for failure in failures)


@pytest.mark.parametrize("single_quotes", [False, True])
def test_normalization_preserves_everything_except_url_fields(single_quotes):
    contents = MIRROR_LOCK.replace('"', "'") if single_quotes else MIRROR_LOCK
    normalized, counts = checker.normalize(contents, MIRROR + "/simple/")
    original = tomllib.loads(contents)
    expected = tomllib.loads(PUBLIC_LOCK)
    assert tomllib.loads(normalized) == expected
    assert counts == {"registry": 2, "url": 1}
    original_package = original["package"][0]
    public_package = expected["package"][0]
    assert original_package["version"] == public_package["version"]
    assert original_package["wheels"][0]["hash"] == public_package["wheels"][0]["hash"]
    assert original_package["wheels"][0]["size"] == public_package["wheels"][0]["size"]
    assert PACKAGE_PATH in public_package["wheels"][0]["url"]


def test_normalization_is_idempotent_on_public_lockfile():
    contents, counts = checker.normalize(PUBLIC_LOCK, MIRROR + "/simple")
    assert contents == PUBLIC_LOCK
    assert counts == {"registry": 0, "url": 0}


@pytest.mark.parametrize(
    "url",
    [
        "http://mirror.example.com" + PACKAGE_PATH,
        "https://other.example.com" + PACKAGE_PATH,
        MIRROR + "/downloads/example.whl",
        MIRROR + PACKAGE_PATH + "?token=synthetic",
        MIRROR + PACKAGE_PATH + "#fragment",
        "https://user:synthetic@mirror.example.com" + PACKAGE_PATH,
        MIRROR + ":443" + PACKAGE_PATH,
        MIRROR + "/packages/ab/cd/../example.whl",
    ],
)
def test_normalization_refuses_unrecognized_artifact_urls(url):
    contents = MIRROR_LOCK.replace(MIRROR + PACKAGE_PATH, url)
    with pytest.raises(ValueError, match="unsupported artifact"):
        checker.normalize(contents, MIRROR + "/simple")


@pytest.mark.parametrize("digest", ["sha256:abc", "md5:" + "a" * 32, ""])
def test_normalization_requires_sha256_integrity_metadata(digest):
    with pytest.raises(ValueError, match="SHA256"):
        checker.normalize(MIRROR_LOCK.replace(HASH, digest), MIRROR + "/simple")


@pytest.mark.parametrize(
    "url",
    [
        "http://mirror.example.com/simple",
        "https://user:synthetic@mirror.example.com/simple",
        MIRROR + "/other",
        MIRROR + "/simple?token=synthetic",
    ],
)
def test_invalid_mirror_configuration_is_rejected(url):
    with pytest.raises(ValueError):
        checker.normalize(MIRROR_LOCK, url)


def test_normalization_rejects_another_registry():
    contents = MIRROR_LOCK.replace(MIRROR + "/simple/", "https://other.example.com/simple")
    with pytest.raises(ValueError, match="selected mirror"):
        checker.normalize(contents, MIRROR + "/simple")


def test_normalization_refuses_to_change_an_unrelated_metadata_field():
    contents = MIRROR_LOCK + f'\nnote = "{MIRROR}/simple/"\n'
    with pytest.raises(ValueError, match="unrelated metadata"):
        checker.normalize(contents, MIRROR + "/simple")


def test_direct_git_sources_are_not_public_pypi_metadata(tmp_path):
    target = tmp_path / "uv.lock"
    target.write_text(PUBLIC_LOCK.replace(
        f'{{ registry = "{checker.PUBLIC_REGISTRY}" }}',
        '{ git = "https://example.com/source.git" }',
        1,
    ))
    with pytest.raises(ValueError, match="not a registry"):
        checker.check_uv_lock(target)


def test_root_editable_project_is_allowed(tmp_path):
    target = tmp_path / "uv.lock"
    target.write_text(PUBLIC_LOCK + """
[[package]]
name = "local-project"
version = "1.0.0"
source = { editable = "." }
""")
    assert checker.check_uv_lock(target) == []


def test_public_artifact_with_missing_hash_is_rejected(tmp_path):
    target = tmp_path / "uv.lock"
    target.write_text(PUBLIC_LOCK.replace(f', hash = "{HASH}"', ""))
    assert checker.check_uv_lock(target)


def test_cli_check_is_read_only_and_fix_requires_explicit_mirror(tmp_path):
    target = tmp_path / "uv.lock"
    target.write_text(MIRROR_LOCK)
    assert checker.main([str(target)]) == 1
    assert target.read_text() == MIRROR_LOCK
    with pytest.raises(SystemExit) as error:
        checker.main([str(target), "--fix"])
    assert error.value.code == 2
    assert checker.main([str(target), "--fix", "--mirror-url", MIRROR + "/simple"]) == 0
    assert target.read_text() == PUBLIC_LOCK


def test_failed_normalization_does_not_partially_write_the_lockfile(tmp_path):
    target = tmp_path / "uv.lock"
    contents = MIRROR_LOCK.replace(MIRROR + PACKAGE_PATH, MIRROR + "/downloads/example.whl")
    target.write_text(contents)
    assert checker.main([str(target), "--fix", "--mirror-url", MIRROR + "/simple"]) == 1
    assert target.read_text() == contents


@pytest.mark.parametrize("contents", ["not valid TOML", "version = 1\n", "package = [1]\n"])
def test_cli_rejects_malformed_or_empty_lockfile(tmp_path, contents):
    target = tmp_path / "uv.lock"
    target.write_text(contents)
    assert checker.main([str(target)]) == 1
