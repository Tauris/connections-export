"""The macOS executables must not quietly require a newer macOS.

Every native part of a Mac executable declares the oldest macOS it runs on.
Nothing is compiled on the Mac runners, so those declarations come from the
ready-built packages -- and a dependency that one day ships wheels only for
a new macOS would make the whole executable require it, with no one the
wiser until someone on an older Mac could not start it. The Linux build had
exactly that failure with glibc. `tools/check_macos_minimum.py` reads the
declarations out of the finished executable and fails the build over a limit.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

check = pytest.importorskip("check_macos_minimum")


def _encode(version: tuple[int, int, int]) -> int:
    major, minor, patch = version
    return (major << 16) | (minor << 8) | patch


def _thin(version, *, old_style=False) -> bytes:
    if old_style:  # LC_VERSION_MIN_MACOSX
        command = struct.pack("<IIII", 0x24, 16, _encode(version), 0)
    else:  # LC_BUILD_VERSION, platform 1 = macOS
        command = struct.pack("<IIIIII", 0x32, 24, 1, _encode(version), 0, 0)
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x0100000C, 0, 6, 1, len(command), 0, 0)
    return header + command


def _fat(*slices: bytes) -> bytes:
    offset = 4096
    head = struct.pack(">II", 0xCAFEBABE, len(slices))
    body = b""
    for data in slices:
        head += struct.pack(">iiIII", 0x01000007, 3, offset + len(body), len(data), 12)
        body += data
    return head.ljust(offset, b"\0") + body


def test_reads_the_build_version():
    assert check.macho_minimum(_thin((11, 0, 0))) == (11, 0, 0)


def test_reads_the_older_version_min_command():
    assert check.macho_minimum(_thin((10, 13, 0), old_style=True)) == (10, 13, 0)


def test_a_universal_binary_counts_its_highest_slice():
    assert check.macho_minimum(_fat(_thin((10, 9, 0)), _thin((11, 0, 0)))) == (11, 0, 0)


def test_something_that_is_not_mach_o_has_no_minimum():
    assert check.macho_minimum(b"\x7fELF...") is None
    assert check.macho_minimum(b"") is None


def test_parts_over_the_limit_are_named():
    parts = {
        "libgood.dylib": _thin((11, 0, 0)),
        "libtoo_new.so": _thin((14, 0, 0)),
        "data.txt": b"not a binary",
    }

    over, checked = check.over_limit(parts.items(), (11, 0, 0))

    assert over == [("libtoo_new.so", (14, 0, 0))]
    assert checked == 2


def test_a_named_exception_has_its_own_limit_and_no_more():
    """Playwright's Node.js driver needs macOS 13.5 -- documented as the PDF
    export's minimum. Allowed exactly that far; beyond it, or anywhere else,
    a jump still fails."""
    parts = [
        ("playwright/driver/node", _thin((13, 5, 0))),
        ("libother.dylib", _thin((13, 5, 0))),
    ]
    allow = {"playwright/driver/node": (13, 5, 0)}

    over, _ = check.over_limit(parts, (11, 0, 0), allow)
    assert over == [("libother.dylib", (13, 5, 0))]

    newer = [("playwright/driver/node", _thin((14, 0, 0)))]
    assert check.over_limit(newer, (11, 0, 0), allow)[0] == [("playwright/driver/node", (14, 0, 0))]


def test_the_command_line_takes_exceptions(tmp_path, monkeypatch):
    monkeypatch.setattr(
        check,
        "_executable_parts",
        lambda exe: [("app", _thin((11, 0, 0))), ("playwright/driver/node", _thin((13, 5, 0)))],
    )
    exe = tmp_path / "app"
    assert check.main([str(exe), "--max", "11.0"]) == 1
    assert check.main([str(exe), "--max", "11.0", "--allow", "playwright/driver/node=13.5"]) == 0
