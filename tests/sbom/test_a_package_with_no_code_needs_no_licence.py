"""A distribution that ships no code owes no licence text.

`pypiwin32` is the case: version 223 is a 1,674-byte wheel containing
nothing but its own `dist-info`, whose entire substance is
`Requires-Dist: pywin32`. It exists to redirect an old name to a new one.
Its metadata declares no author, no home page and no licence, because
there is nothing to declare -- and `pywin32`, which holds the actual
code, is resolved separately and carries its own licence.

The build refused it, correctly by its own rule and wrongly in fact: the
obligation is to reproduce the licence of code you redistribute, and this
redistributes none. An override would have been worse than the refusal --
it would mean inventing a licence claim about a package whose author made
none.

So the rule is narrow: every file the distribution installs is its own
metadata, and it installs at least one, since a distribution reporting no
files at all is more likely a metadata read that failed.
"""

from __future__ import annotations

from connections_export import sbom


class _Dist:
    """Enough of `importlib.metadata.Distribution` for the question asked."""

    def __init__(self, name: str, files: list[str] | None):
        self.metadata = {"Name": name}
        self.files = files


def test_a_metadata_only_distribution_ships_no_code():
    alias = _Dist(
        "pypiwin32",
        [
            "pypiwin32-223.dist-info/METADATA",
            "pypiwin32-223.dist-info/RECORD",
            "pypiwin32-223.dist-info/WHEEL",
            "pypiwin32-223.dist-info/top_level.txt",
        ],
    )

    assert sbom.ships_no_code(alias) is True


def test_a_distribution_with_a_module_does_not():
    real = _Dist(
        "requests_negotiate_sspi",
        [
            "requests_negotiate_sspi/__init__.py",
            "requests_negotiate_sspi-0.5.2.dist-info/METADATA",
        ],
    )

    assert sbom.ships_no_code(real) is False


def test_a_distribution_reporting_no_files_is_not_treated_as_code_free():
    """An empty file list is far more often a RECORD that could not be read
    than a package that genuinely contains nothing. Excusing it would turn
    every unreadable distribution into a licence exemption."""
    assert sbom.ships_no_code(_Dist("mystery", [])) is False
    assert sbom.ships_no_code(_Dist("mystery", None)) is False


def test_egg_info_counts_as_metadata_too():
    legacy = _Dist("old", ["old.egg-info/PKG-INFO", "old.egg-info/SOURCES.txt"])

    assert sbom.ships_no_code(legacy) is True
