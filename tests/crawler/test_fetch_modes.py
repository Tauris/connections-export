"""Three fetch modes, because two were never enough.

`resume` and `refresh` are the historical pair: skip every URL already
archived, or refetch the world. Neither is what "bring this archive up to date"
needs -- the first fetches nothing new, the second re-downloads everything it
already has.

`update` is the third: always refetch the documents that REVEAL change (feeds,
search pages, the rich content layout), and refetch an item's expensive parts
only when the item's own date says it moved.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from connections_export.config import Config


def test_the_default_is_resume():
    """Unchanged behaviour for every existing caller: a re-run into an archive
    skips what it already has."""
    assert Config().fetch == "resume"


def test_refresh_is_still_reachable():
    assert Config(fetch="refresh").fetch == "refresh"


def test_update_is_a_mode_of_its_own():
    assert Config(fetch="update").fetch == "update"


def test_an_unknown_mode_is_refused():
    """A typo silently falling back to `resume` would look like a successful
    update that fetched nothing."""
    with pytest.raises(ValidationError):
        Config(fetch="incremental")


def test_into_names_the_archive_being_extended():
    from pathlib import Path

    config = Config(into=Path("archive/export-20260810-atlas"))

    assert config.into == Path("archive/export-20260810-atlas")


def test_a_since_override_is_optional_and_absent_by_default():
    """By default the cutoff comes from the archive's own provenance, so a
    scripted monthly re-run stays correct without anyone editing a date."""
    assert Config().since_override is None
