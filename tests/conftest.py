"""Suite-wide safety net: tests never write where the user keeps things.

`connections_export.gui.support.ARCHIVES_BASE` is resolved once at import
time from the environment and the working directory, so in a checkout it points at
the developer's actual `connections-export-archives/`. Any test that starts a
run without overriding it -- `/api/start`, `run_demo` through the GUI, the
demo pipeline -- creates a real archive there. Only 4 of the 35 GUI test files
overrode it, so a single `pytest tests/gui/` left 13 archives behind, and a
user who had just deleted their demo archives watched them reappear.

Redirecting per test rather than per session keeps tests isolated from each
other as well, and an explicit `monkeypatch.setattr(gui_support,
"ARCHIVES_BASE",...)` inside a test still wins, since it is applied after
this fixture.
"""

from __future__ import annotations

import pytest

from connections_export.gui import settings_store
from connections_export.gui import support as gui_support


@pytest.fixture(autouse=True)
def _isolate_archives_base(tmp_path_factory, monkeypatch):
    """Point the console's archive root at a per-test temporary directory."""
    try:
        pass
    except Exception:  # noqa: BLE001 - suites that never import the GUI
        return

    sandbox = tmp_path_factory.mktemp("archives")
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", sandbox)


@pytest.fixture(autouse=True)
def _isolate_saved_settings(tmp_path_factory, monkeypatch):
    """Point saved settings at a per-test temporary directory.

    Settings persist to the user's own config directory (`$XDG_CONFIG_HOME` /
    `%APPDATA%`), which is the point of them. But `PUT /api/settings` in a test
    then writes the developer's real settings file: a suite run turned "hide
    the capture screens" on and left it on, and the test asserting the default
    failed on the next run against state a previous run had saved.

    The same hazard as `ARCHIVES_BASE` above, and the same answer. Per test,
    so tests cannot see each other's saved settings either.
    """
    monkeypatch.setenv(settings_store.SETTINGS_DIR_ENV, str(tmp_path_factory.mktemp("settings")))
