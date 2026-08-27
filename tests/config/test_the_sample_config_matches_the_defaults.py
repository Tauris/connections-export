"""The sample configuration does not contradict the program.

`connections-export.example.toml` ships, and it is the file a user copies
to make their own. A setting in it that disagrees with the built-in
default does not read as an example -- it reads as advice, and it is the
advice they will follow. It shipped saying `min_interval = 0.2` while the
program defaults to 1.0 and both the manual and the README say one
second: a sample that would have had every new user asking a live
deployment for something five times a second.

Some values here are illustrations rather than settings -- an address has
to be some address, and the placeholder is the point. Those are named
below, with the reason. Everything else must agree with the default it
is showing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from connections_export.config import Config

SAMPLE = Path(__file__).resolve().parents[2] / "connections-export.example.toml"

#: Keys whose value in the sample is deliberately not the default, because
#: there is no useful default to show.
ILLUSTRATIONS = {
    "base_url": "a placeholder deployment; there is no default address",
    "hcl_hosts": "the asset boundary of that placeholder deployment",
    "source_version": "the version being exported, which is the user's fact",
    "output_dir": "shown as a plain relative path a reader can recognise",
}


def _sample() -> dict:
    return tomllib.loads(SAMPLE.read_text(encoding="utf-8"))


def test_the_sample_exists_and_parses():
    assert SAMPLE.is_file()
    assert _sample()


def test_every_setting_it_shows_is_the_one_the_program_uses():
    disagreements = []
    for key, shown in _sample().items():
        if key in ILLUSTRATIONS or key not in Config.model_fields:
            continue
        default = Config.model_fields[key].default
        if isinstance(default, Path):
            default = str(default)
        if shown != default:
            disagreements.append(f"{key}: sample says {shown!r}, the default is {default!r}")

    assert disagreements == [], "\n".join(disagreements)


def test_the_illustrations_are_still_settings():
    """So the exception list cannot outlive what it excuses."""
    sample = _sample()
    for key in ILLUSTRATIONS:
        assert key in sample, f"{key} is excused from a sample that no longer shows it"


def test_the_pacing_it_shows_is_the_pacing_the_documents_promise():
    """Three places say what an export waits between requests. A user who
    reads one and copies another has been told two different things."""
    manual = (SAMPLE.parent / "docs" / "manual.md").read_text(encoding="utf-8")

    assert _sample()["min_interval"] == 1
    assert "waits **1 second between them** by default" in manual
