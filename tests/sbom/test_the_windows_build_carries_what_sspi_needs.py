"""SSPI's runtime imports are named, because analysis cannot find them.

`requests_negotiate_sspi` reaches the archive by ordinary means: it is
imported, so PyInstaller sees it. What it needs at RUNTIME is a
different matter -- pywin32 imports `win32timezone` lazily, from inside
`pywintypes`, at the moment a timestamp is converted. Nothing imports it
where a static analysis can see, so it is not collected, and the
executable is built and verified and shipped without it.

The failure lands nowhere near the cause. The console starts, `--help`
works, the deployment is reached, and the SSPI handshake dies with
"No module named win32timezone" -- which surfaces as every feed failing
to authenticate, and therefore as a community that appears to hold
nothing.

So they are named as hidden imports and then REQUIRED: the build refuses
rather than producing a Windows executable that cannot authenticate,
which is the only thing the Windows executable exists for.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

build_binary = pytest.importorskip("build_binary")


def test_the_lazily_imported_modules_are_named():
    assert "win32timezone" in build_binary.SSPI_RUNTIME_IMPORTS


def test_they_are_required_on_windows_so_a_build_without_them_refuses(monkeypatch):
    """Not merely hinted at. A hidden import that silently fails to resolve
    leaves exactly the executable this exists to prevent."""
    monkeypatch.setattr(build_binary.sys, "platform", "win32")

    required = build_binary._required_modules(lambda module: True)

    for module in build_binary.SSPI_RUNTIME_IMPORTS:
        assert module in required, f"{module} is not required, so its absence ships"
        assert required[module], f"{module} is required without saying what it breaks"


def test_they_are_not_required_off_windows(monkeypatch):
    """The package is Windows-only. Requiring it elsewhere would fail every
    Linux and macOS build for something none of them can use."""
    monkeypatch.setattr(build_binary.sys, "platform", "linux")

    required = build_binary._required_modules(lambda module: True)

    assert "win32timezone" not in required


def test_a_windows_build_without_the_sspi_package_still_refuses(monkeypatch):
    """The existing rule, kept: the reason to build a Windows executable is
    native authentication."""
    monkeypatch.setattr(build_binary.sys, "platform", "win32")

    with pytest.raises(build_binary.BuildError) as caught:
        build_binary._required_modules(lambda module: False)

    assert "requests_negotiate_sspi" in str(caught.value)
