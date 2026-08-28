"""The frozen-distribution query does not put module names on a command line.

PyInstaller's analysis names every top-level module it froze. On Windows,
once pywin32 is collected, that is thousands of them -- and a command
line has a length limit there, so passing them as arguments failed the
build with "The filename or extension is too long", which names neither
the command that failed nor the reason it did.

Nothing about the number of modules should be able to break a build, so
they travel on stdin, which has no such limit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

build_binary = pytest.importorskip("build_binary")


def test_the_names_are_not_passed_as_arguments(monkeypatch):
    seen = {}

    def fake_run(cmd, *, cwd=None, stdin=None):
        seen["cmd"] = list(cmd)
        seen["stdin"] = stdin
        return "[]"

    monkeypatch.setattr(build_binary, "_run", fake_run)
    many = {f"module_{n}" for n in range(4000)}

    build_binary._frozen_distributions(REPO_ROOT, many, sys.executable)

    assert len(seen["cmd"]) == 3, f"module names leaked into argv: {len(seen['cmd'])} args"
    assert seen["stdin"], "the names were not sent on stdin"
    assert "module_3999" in seen["stdin"]


def test_a_command_line_of_that_size_would_not_survive_windows(monkeypatch):
    """The limit this exists for, stated so the number is not mistaken for
    an arbitrary choice."""
    many = {f"module_{n}" for n in range(4000)}

    as_argv = sum(len(name) + 1 for name in many)

    assert as_argv > 32_767, "the example is too small to demonstrate the limit"
