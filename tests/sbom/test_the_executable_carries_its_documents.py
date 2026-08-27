"""The single-file executable holds the documents the program reads.

Three documents are not decoration: the console renders `manual.md` as its
Manual tab, `write_package` copies the interchange spec into every package
it writes, and the architecture document ships beside the code. The wheel
gets them through `force-include` in `pyproject.toml`. A frozen build gets
them a different way -- PyInstaller collects package data from a *source*
tree, where none of the three is at the path the program looks for.

So the executable served a console with no manual, answered 404 for the
spec, and would have raised `FileNotFoundError` on writing a package,
which is the one that is a crash rather than a gap.

The fix reads the wheel's own force-include map rather than keeping a
second list. A document added to one is then in both, which is the only
arrangement that stays true.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

build_binary = pytest.importorskip("build_binary")


def _force_include() -> dict[str, str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]


def test_the_map_is_read_from_the_wheels_own_configuration():
    """Not a copy of it. A copy is a second place to remember."""
    assert build_binary.bundled_documents() == _force_include()
    assert build_binary.bundled_documents(), "the wheel force-includes nothing?"


def test_every_document_the_wheel_carries_is_placed_for_the_freeze(tmp_path):
    source = tmp_path / "source"
    for relative in _force_include():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"contents of {relative}", encoding="utf-8")

    build_binary.place_documents(source)

    for relative, target in _force_include().items():
        placed = source / target
        assert placed.is_file(), f"{relative} was not placed at {target}"
        assert placed.read_text(encoding="utf-8") == f"contents of {relative}"


def test_a_missing_document_fails_the_build_rather_than_shipping_without_it(tmp_path):
    """Silently freezing without one is how this went unnoticed: the build
    succeeds, the executable runs, and the gap only shows when someone opens
    the Manual tab or writes a package."""
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(build_binary.BuildError) as caught:
        build_binary.place_documents(source)

    assert "manual.md" in str(caught.value) or "interchange-format.md" in str(caught.value)


def test_the_program_looks_where_the_documents_are_placed():
    """The targets are not arbitrary -- each is the path the code resolves.
    A target changed here and not there puts the file in the executable and
    still out of reach."""
    targets = set(_force_include().values())

    assert "connections_export/manual.md" in targets
    assert "connections_export/interchange/interchange-format.md" in targets
    assert "connections_export/architecture.md" in targets
