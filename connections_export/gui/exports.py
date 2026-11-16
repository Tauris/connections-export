"""Where the console's PDF exports go, and showing them to the person.

A large export is hundreds of megabytes or several files; it does not belong in
the server's memory or in a browser download alone. It is written to disk
beside the archive it came from, in a folder named after it -- the same
convention the Hugo, Jekyll and Obsidian exports follow (`<archive>-pdf`, which
the archive list knows is not an archive) -- and the console offers both the
download and "Show in folder".
"""

from __future__ import annotations

import datetime
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

#: The suffix of the folder beside an archive that holds its PDF exports.
PDF_FOLDER_SUFFIX = "-pdf"


def pdf_folder(archive_root: Path | None, fallback_base: Path) -> Path:
    """The folder for `archive_root`'s PDFs, created if needed: beside the
    archive (a directory or a `.zip`), named after it."""
    if archive_root is None:
        folder = fallback_base / f"export{PDF_FOLDER_SUFFIX}"
    else:
        name = archive_root.stem if archive_root.suffix.lower() == ".zip" else archive_root.name
        folder = archive_root.parent / f"{name}{PDF_FOLDER_SUFFIX}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def stamp(now: datetime.datetime | None = None) -> str:
    """A sortable, file-name-safe moment: year-month-day, then hours and
    minutes (`%Y-%m-%d %H%M`)."""
    return (now or datetime.datetime.now()).strftime("%Y-%m-%d %H%M")


def unique(path: Path) -> Path:
    """`path`, or `name (2).ext`, `name (3).ext`... if it is taken."""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _wsl() -> bool:
    return (
        bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in platform.uname().release.lower()
    )


def reveal(folder: Path) -> bool:
    """Open `folder` in the system's file manager. Returns whether a file
    manager was started. Only ever called with a folder this tool wrote."""
    target = str(folder)
    try:
        if sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
            return True
        if _wsl() and shutil.which("explorer.exe") and shutil.which("wslpath"):
            windows = subprocess.run(
                [shutil.which("wslpath"), "-w", target],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            subprocess.Popen([shutil.which("explorer.exe"), windows])
            return True
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.Popen([opener, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except (OSError, subprocess.SubprocessError):
        return False
    return False
