"""Under WSL the console opens the Windows browser.

Python's browser opening looks for Linux helpers (`xdg-open`,
`x-www-browser`, ...), which a WSL distribution usually lacks: the browser a
WSL user has is on the Windows side, so the console said where it was and
opened nothing. Under WSL it now uses `wslview` when installed, else
`explorer.exe`, which opens the Windows default browser. `$BROWSER`, the
Linux convention for choosing one, still wins; elsewhere nothing changes.
"""

from __future__ import annotations

import connections_export.cli as cli

URL = "http://127.0.0.1:8000/"


def _record(monkeypatch, *, wsl: bool, which: dict[str, str], env: dict | None = None):
    started: list[list[str]] = []
    opened: list[str] = []
    monkeypatch.setattr(cli, "_in_wsl", lambda: wsl)
    monkeypatch.setattr(cli.shutil, "which", lambda name: which.get(name))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda args, **_kw: started.append(args))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    for key in ("BROWSER",):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    cli._open_in_browser(URL)
    return started, opened


def test_under_wsl_it_uses_wslview_when_installed(monkeypatch):
    started, opened = _record(
        monkeypatch, wsl=True, which={"wslview": "/usr/bin/wslview", "explorer.exe": "/x"}
    )
    assert started == [["/usr/bin/wslview", URL]] and opened == []


def test_under_wsl_without_wslview_it_uses_explorer(monkeypatch):
    started, _ = _record(monkeypatch, wsl=True, which={"explorer.exe": "/mnt/c/explorer.exe"})
    assert started == [["/mnt/c/explorer.exe", URL]]


def test_browser_variable_still_wins_under_wsl(monkeypatch):
    started, opened = _record(
        monkeypatch, wsl=True, which={"wslview": "/usr/bin/wslview"}, env={"BROWSER": "firefox"}
    )
    assert started == [] and opened == [URL]


def test_outside_wsl_nothing_changes(monkeypatch):
    started, opened = _record(monkeypatch, wsl=False, which={"wslview": "/usr/bin/wslview"})
    assert started == [] and opened == [URL]


def test_wsl_is_recognised_from_the_kernel_release_or_its_variable():
    assert cli._is_wsl("5.15.0-microsoft-standard-WSL2", {}) is True
    assert cli._is_wsl("6.8.0-45-generic", {"WSL_DISTRO_NAME": "Ubuntu"}) is True
    assert cli._is_wsl("6.8.0-45-generic", {}) is False
