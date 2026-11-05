import pytest

from aops import __version__
from aops.cli import main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"aops {__version__}"


def test_unknown_harness_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="unknown harness"):
        main(["session", "switch", "--harness", "nope"])
