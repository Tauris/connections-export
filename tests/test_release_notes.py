"""The release notes a tag publishes come from the changelog.

The public repository is one commit per release, so `--generate-notes` has no
commits to list and produces an empty release body. Written notes are the only
ones that can say anything, which makes "the version has no section" a failure
worth reporting rather than an empty string worth publishing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tools.release_notes import section

REPO_ROOT = Path(__file__).resolve().parent.parent

SAMPLE = """# Changelog

Preamble that belongs to no version.

## 0.2.0 — 2026-10-01

Newest.

## 0.1.3 — 2026-09-06

### A heading inside the section

- A bullet.

## 0.1.30 — 2026-09-05

The trap.

## 0.1.2 — 2026-08-29

Oldest.
"""


def test_it_returns_one_version_without_its_heading():
    assert section(SAMPLE, "0.1.3") == "### A heading inside the section\n\n- A bullet."


def test_a_section_stops_at_the_next_version():
    assert "The trap." not in section(SAMPLE, "0.1.3")
    assert "Newest." not in section(SAMPLE, "0.1.3")


def test_a_version_is_matched_whole():
    """`0.1.3` must not match `0.1.30`. Prefix matching would publish the
    wrong notes, which is worse than publishing none."""
    assert section(SAMPLE, "0.1.30") == "The trap."


def test_the_last_section_runs_to_the_end():
    assert section(SAMPLE, "0.1.2") == "Oldest."


def test_a_missing_version_is_an_error_not_an_empty_body():
    with pytest.raises(LookupError):
        section(SAMPLE, "9.9.9")


def test_an_empty_argument_means_absent_not_a_version_named_nothing():
    """A workflow passes `"$GITHUB_REF_NAME"`, and an unset variable expands
    to an empty string rather than to no argument at all. Looked up as a
    version it matches no section and reports a half-sentence ending in
    nothing; taken as absent it falls back to the packaged version, which is
    the only useful reading. The step runs only on a tag, so no push
    exercises it -- which is the argument for exercising it here."""
    from tools.release_notes import main

    assert main([""]) == 0
    assert main([]) == 0


def test_the_current_version_actually_has_notes():
    """The one that matters: this release is about to be tagged, and a tag is
    the thing that cannot be taken back."""
    version = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    body = section(
        (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
        version["project"]["version"],
    )
    assert body.strip(), "CHANGELOG.md has no section for the version in pyproject.toml"
