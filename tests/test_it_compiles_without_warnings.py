"""Nothing the package ships warns when it is first imported.

A `SyntaxWarning` -- an invalid escape in a docstring, say -- is raised
by the compiler, so it appears exactly once per module: when the `.pyc`
is written. In a working tree that happened long ago, and every run
since has loaded the cached bytecode in silence. The first person to see
it is whoever installs a fresh copy, and they see it on every command
until their cache warms.

So this compiles from source, with warnings promoted, the way a new
install does.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import connections_export

PACKAGE_ROOT = Path(connections_export.__file__).parent


def _sources() -> list[Path]:
    return [
        path
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        # Vendored third-party code is not ours to edit.
        if "vendor" not in path.parts
    ]


def test_every_shipped_module_compiles_cleanly():
    offenders = []
    for path in _sources():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        for entry in caught:
            offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{entry.lineno}: {entry.message}")

    assert offenders == []
