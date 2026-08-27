"""Nothing printed names a command that does not exist.

The console script is `connections-export`. `hcl-serve` was an earlier
name for it and is not installed by anything, so a line beginning
`hcl-serve:` tells a reader to go and run something they do not have --
and the very first line a bare `connections-export` prints was one of
them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import connections_export

PACKAGE_ROOT = Path(connections_export.__file__).parent
GONE = "hcl-serve"


def _printed_strings(path: Path) -> list[tuple[int, str]]:
    """Every string literal that reaches a `print`, including f-string parts."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print"):
            continue
        for piece in ast.walk(node):
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                found.append((piece.lineno, piece.value))
    return found


def test_no_printed_line_names_a_command_that_is_not_installed():
    offenders = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "vendor" in path.parts:
            continue
        for lineno, text in _printed_strings(path):
            if GONE in text:
                offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{lineno}: {text.strip()[:60]}")

    assert offenders == [], "\n".join(offenders)


def test_the_name_it_prints_is_the_script_that_is_installed():
    """Read from the packaging metadata rather than repeated here."""
    import tomllib

    root = Path(connections_export.__file__).resolve().parents[1]
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return  # an installed copy has no source tree to compare against
    scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"]

    assert "connections-export" in scripts
    assert GONE not in scripts
