"""Where the console's saved settings live between runs.

Held only in `app.state`, a saved setting lasts until the process stops and
is back to its default on the next start -- and nothing on screen says so,
because the field goes on showing the saved number until then. So they are
written to disk.

Stored beside the USER rather than beside the working directory. Settings that
depend on which folder you launched from are the same trap in a different
place -- and this tool is launched by double-clicking an executable as often
as from a shell.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

#: Overrides where settings are stored. Exists for tests and for anyone who
#: keeps a machine's configuration somewhere specific.
SETTINGS_DIR_ENV = "CONNECTIONS_EXPORT_SETTINGS_DIR"

SETTINGS_FILENAME = "settings.json"

#: What a user can choose and expect to find again. Deliberately a list rather
#: than "whatever is in app.state": a stored key nobody set is a value that
#: quietly outlives the version that introduced it.
PERSISTED_KEYS = (
    "base_url",
    "auth_mode",
    "min_interval",
    "default_author_filter",
    "pdf_style",
    "pdf_marks",
    "pdf_timeout",
    "archive_only",
)


def settings_dir() -> Path:
    """The directory holding this user's console settings.

    `%APPDATA%\\connections-export` on Windows, `$XDG_CONFIG_HOME` or
    `~/.config/connections-export` elsewhere -- the conventions each platform's
    users already expect to find configuration in.
    """
    override = os.environ.get(SETTINGS_DIR_ENV)
    if override and override.strip():
        return Path(override.strip())
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "connections-export"


def settings_path() -> Path:
    return settings_dir() / SETTINGS_FILENAME


def load_settings() -> dict[str, Any]:
    """What was saved, or `{}`.

    Never raises. A settings file that cannot be read is a reason to fall back
    to the defaults, not a reason the console will not start -- and starting
    is how someone fixes it.
    """
    try:
        stored = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {key: stored[key] for key in PERSISTED_KEYS if key in stored}


def save_settings(settings: dict[str, Any]) -> Path | None:
    """Write the settings a user chose. Returns the path, or `None` if it
    could not be written -- a read-only home directory is not a reason to
    fail the save the user just made in the UI."""
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        keep = {key: settings[key] for key in PERSISTED_KEYS if key in settings}
        path.write_text(json.dumps(keep, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return None
    return path
