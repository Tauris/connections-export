"""The macOS and Linux executables sign in with Kerberos, so they must carry it.

"Sign in as yourself" is SSPI on Windows and Kerberos elsewhere. An
executable is meant to work out of the box -- nothing to pip install, no
conda recipe -- so a macOS or Linux build without the Kerberos modules is
refused, as a Windows build without SSPI already is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

build_binary = pytest.importorskip("build_binary")


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_kerberos_is_required_off_windows(monkeypatch, platform):
    monkeypatch.setattr(build_binary.sys, "platform", platform)

    required = build_binary._required_modules(lambda module: True)

    for module in build_binary.KERBEROS_RUNTIME_IMPORTS:
        assert module in required, f"{module} is not required on {platform}"
        assert required[module]


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_a_build_without_kerberos_refuses(monkeypatch, platform):
    monkeypatch.setattr(build_binary.sys, "platform", platform)

    with pytest.raises(build_binary.BuildError, match="requests_kerberos"):
        build_binary._required_modules(lambda module: module != "requests_kerberos")


def test_windows_does_not_need_kerberos(monkeypatch):
    monkeypatch.setattr(build_binary.sys, "platform", "win32")

    required = build_binary._required_modules(lambda module: True)

    assert "requests_kerberos" not in required


def test_only_a_local_freeze_check_may_leave_kerberos_out(monkeypatch):
    """The release gate's freeze runs on a developer machine without the
    Kerberos headers; it proves the package freezes. It says so explicitly --
    the default stays strict for every published build."""
    monkeypatch.setattr(build_binary.sys, "platform", "linux")

    required = build_binary._required_modules(lambda module: False, sign_in=False)

    assert "requests_kerberos" not in required
