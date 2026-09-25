"""Viewing a Hugo export with the person's own Hugo, from the console.

A Hugo export written with its starter site (`ingest.hugo_starter`) is a
complete site; `hugo server` in its folder shows it. The console can start
that for someone who never opens a terminal -- when Hugo is installed. It
never downloads or installs Hugo: `hugo_info` says whether one is on PATH,
and the console offers the preview only then.

The preview is served by that separate `hugo server` process, on a port of
its own on 127.0.0.1, and never through the console. The exported pages are
someone else's content -- in `html` and `raw` mode, HTML as its authors
wrote it -- and anything running in them must not run on the console's
origin, where it could call the console's API. On another port it is
another origin; the browser marks its requests to the console `same-site`,
which `security.LocalGuardMiddleware` refuses for every `/api/` route.

One preview at a time: starting another stops the one before, and the
console stops the last one when it exits (its lifespan, and `atexit` for a
process that ends without running it).
"""

from __future__ import annotations

import atexit
import contextlib
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import weakref
from pathlib import Path

#: How long `hugo version` may take before Hugo counts as not answering.
#: Generous on purpose: the first run of a newly installed program on
#: Windows can take many seconds while antivirus scans it, and a Hugo that is
#: really there must not be reported missing because of that.
_VERSION_TIMEOUT = 15.0

#: How long the first build may take before the preview is given up. Hugo
#: listens only once the site has built, so this is the build's time too.
START_TIMEOUT = 60.0

#: How much of Hugo's output an error shows: the end, where the reason is.
_OUTPUT_TAIL = 4000

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_VERSION = re.compile(r"v(\d+\.\d+\.\d+)")

#: What starts the server -- a name of this module's own, so a test can
#: stand in for Hugo without replacing `subprocess.Popen` for everyone.
Popen = subprocess.Popen

#: Every preview manager with a server that may be running, for `atexit`.
_LIVE: weakref.WeakSet[HugoPreview] = weakref.WeakSet()


class PreviewError(Exception):
    """The preview could not be started; the message says why, for the
    person reading the console."""


#: Every Hugo that answered `hugo version`, by the path it was found at.
#: A Hugo that answered once is taken as there for the life of the process,
#: so the dialog asking again is instant. A Hugo that did not answer is not
#: remembered: it is asked again next time, and may answer then.
_FOUND: dict[str, dict] = {}
_FOUND_LOCK = threading.Lock()


def hugo_info() -> dict:
    """Whether Hugo is usable here: `{available, version, path, error}`.

    `path` is where Hugo was found on PATH, or `None` when it is not there
    at all. `version` is Hugo's own, as it reports it (`0.146.0`), or
    `None` when it could not be read. `error` tells the two ways Hugo can be
    unavailable apart: `None` when it is simply not on PATH, `"did not
    answer"` when it is there but `hugo version` failed or timed out -- the
    dialog words those differently, because the remedies differ."""
    path = shutil.which("hugo")
    if path is None:
        return {"available": False, "version": None, "path": None, "error": None}
    with _FOUND_LOCK:
        known = _FOUND.get(path)
    if known is not None:
        return dict(known)
    silent = {"available": False, "version": None, "path": path, "error": "did not answer"}
    try:
        result = subprocess.run(
            [path, "version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_VERSION_TIMEOUT,
            check=False,
            **_no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return silent
    if result.returncode != 0:
        return silent
    match = _VERSION.search(result.stdout or "")
    found = {
        "available": True,
        "version": match.group(1) if match else None,
        "path": path,
        "error": None,
    }
    with _FOUND_LOCK:
        _FOUND[path] = found
    return dict(found)


def forget_hugo() -> None:
    """Drop every remembered Hugo, so the next `hugo_info` asks again."""
    with _FOUND_LOCK:
        _FOUND.clear()


def _no_window() -> dict:
    """On Windows, keep a console window from opening for Hugo when the
    console itself runs without one."""
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flag} if flag else {}


def free_port() -> int:
    """A port on 127.0.0.1 nothing is listening on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def port_answers(port: int) -> bool:
    """Whether something accepts connections on 127.0.0.1:`port`."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def server_command(hugo: str, port: int) -> list[str]:
    """The `hugo server` command line, as an argument list -- never a shell
    string, so no folder name or path is ever parsed as a command.

    Bound to 127.0.0.1 only, on the console's chosen port, with the base URL
    naming that same address so every link in the preview stays on it.
    `--renderToMemory` writes nothing into the export folder, and
    `--disableFastRender` rebuilds every page on a change, so an edited
    template shows everywhere at once."""
    return [
        hugo,
        "server",
        "--bind",
        "127.0.0.1",
        "--port",
        str(port),
        "--baseURL",
        f"http://127.0.0.1:{port}/",
        "--renderToMemory",
        "--disableFastRender",
    ]


class HugoPreview:
    """The one `hugo server` the console runs, if any."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._output = None
        self._url: str | None = None
        self._folder: Path | None = None

    def status(self) -> dict | None:
        """`{url, path}` of the running preview, or `None`."""
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                return None
            return {"url": self._url, "path": str(self._folder)}

    def start(self, folder: Path, hugo: str, *, timeout: float = START_TIMEOUT) -> str:
        """Serve `folder` with `hugo server` and return its address, once it
        answers. Stops any preview already running first. Raises
        `PreviewError` with Hugo's own output when it does not come up."""
        with self._lock:
            self._stop_locked()
            port = free_port()
            output = tempfile.TemporaryFile()
            try:
                process = Popen(
                    server_command(hugo, port),
                    cwd=str(folder),
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env=os.environ.copy(),
                    **_no_window(),
                )
            except OSError as error:
                output.close()
                raise PreviewError(f"could not start Hugo: {error}") from error
            self._process, self._output = process, output
            self._url, self._folder = f"http://127.0.0.1:{port}/", folder
            _LIVE.add(self)

            deadline = time.monotonic() + timeout
            while True:
                if process.poll() is not None:
                    reason = self._output_tail()
                    self._stop_locked()
                    raise PreviewError(
                        "Hugo stopped before the preview was ready"
                        + (f":\n{reason}" if reason else ".")
                    )
                if port_answers(port):
                    return self._url
                if time.monotonic() > deadline:
                    reason = self._output_tail()
                    self._stop_locked()
                    raise PreviewError(
                        f"Hugo did not start serving within {int(timeout)} seconds"
                        + (f":\n{reason}" if reason else ".")
                    )
                time.sleep(0.1)

    def stop(self) -> bool:
        """Stop the preview; whether one was running."""
        with self._lock:
            return self._stop_locked()

    def _stop_locked(self) -> bool:
        process, self._process = self._process, None
        output, self._output = self._output, None
        self._url = self._folder = None
        running = process is not None and process.poll() is None
        if process is not None:
            if running:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=5)
        if output is not None:
            output.close()
        return running

    def _output_tail(self) -> str:
        """The end of what Hugo printed, colour codes removed."""
        if self._output is None:
            return ""
        try:
            self._output.flush()
            self._output.seek(0)
            text = self._output.read().decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""
        return _ANSI.sub("", text).strip()[-_OUTPUT_TAIL:]


@atexit.register
def _stop_all() -> None:
    for preview in list(_LIVE):
        preview.stop()
