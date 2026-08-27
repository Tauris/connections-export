"""What a test run must not touch: the places a user's own state lives.

Both of these have already gone wrong. Archives: a `pytest tests/gui/` left
13 real archives in the developer's `connections-export-archives/`. Settings:
once saving them was made to persist, `PUT /api/settings` in a test wrote the
developer's real settings file -- turning "hide the capture screens" on for
good and making the test that asserts the default fail on the NEXT run.

These assert the nets in `tests/conftest.py` are actually in force, because a
net that silently stops applying looks exactly like one that works.
"""

from __future__ import annotations

from pathlib import Path

from connections_export.gui import settings_store
from connections_export.gui import support as gui_support
from connections_export.gui.settings_store import settings_path


def _is_below(path: Path, ancestor: Path) -> bool:
    return ancestor in path.resolve().parents


def test_saved_settings_do_not_land_in_the_users_config_directory(monkeypatch):
    """Asked of the real directory, not of a place it happens to sit under.

    Naming `~/.config` and `~/AppData` was a proxy for it, and the proxy is
    wrong on Windows: pytest's own temporary directory lives under
    `~/AppData/Local/Temp`, so a correctly isolated path is below `AppData`
    and the check fired on an isolation that was working.
    """
    isolated = settings_path().resolve()

    # What the directory would be with no isolation in force: the user's own.
    monkeypatch.delenv(settings_store.SETTINGS_DIR_ENV, raising=False)
    real = settings_store.settings_dir().resolve()

    # Below, not merely different: `settings_path()` is a file and `real` is a
    # directory, so comparing them for equality is true whether isolation is
    # in force or not, and would pass on exactly the failure it is here for.
    assert not _is_below(isolated, real), (
        f"settings would be written to {isolated}, inside the user's own {real}"
    )


def test_archives_do_not_land_in_the_working_directory():
    assert not _is_below(Path(gui_support.ARCHIVES_BASE).resolve(), Path.cwd())
