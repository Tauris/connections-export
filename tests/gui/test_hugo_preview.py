"""Previewing a Hugo export with the person's own Hugo, from the console.

The console never installs Hugo; it asks whether one is on PATH and offers
the preview only then. The preview is a separate `hugo server` on its own
port -- exported pages are other people's HTML and must never be served
from the console's origin -- started from an argument list, never a shell,
over a folder the console itself wrote and nothing else. One runs at a time,
and it stops with the console.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from connections_export.gui import hugo_preview, support
from connections_export.gui.app import make_app
from connections_export.gui.demo import run_demo

HUGO = shutil.which("hugo")


def _call(app, method: str, url: str, body=None):
    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.request(method, url, json=body)

    return asyncio.run(_do())


class _FakeProcess:
    """A `hugo server` that does as it is told: keeps running, or exits
    at once having printed `output`."""

    def __init__(self, args, *, exit_code=None, output=b"", **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.returncode = exit_code
        self.terminated = False
        if output and kwargs.get("stdout") is not None:
            kwargs["stdout"].write(output)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def fake_hugo(monkeypatch):
    """Hugo 'installed' at a made-up path, every server it starts recorded
    and answering at once."""
    started: list[_FakeProcess] = []

    def popen(args, **kwargs):
        process = _FakeProcess(args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(
        hugo_preview,
        "hugo_info",
        lambda: {"available": True, "version": "0.166.0", "path": "/opt/hugo/hugo"},
    )
    monkeypatch.setattr(hugo_preview, "Popen", popen)
    monkeypatch.setattr(hugo_preview, "port_answers", lambda _port: True)
    return started


def _export(app, *, starter_site: bool = True) -> Path:
    """A Hugo export of a demo archive, written through the console."""
    run_demo(lambda _e: None, archive_dir=support.ARCHIVES_BASE / "run", delay=0)
    response = _call(
        app,
        "POST",
        "/api/ingest",
        {"format": "hugo", "archives": ["run"], "starter_site": starter_site},
    )
    assert response.status_code == 200, response.text
    return Path(response.json()["path"])


# --- is Hugo there? ------------------------------------------------------------------


def test_without_hugo_the_console_says_so(monkeypatch):
    monkeypatch.setattr(hugo_preview.shutil, "which", lambda _name: None)
    body = _call(make_app(demo=True), "GET", "/api/hugo").json()

    assert body == {
        "available": False,
        "version": None,
        "path": None,
        "error": None,
        "preview": None,
    }


def test_with_hugo_the_console_names_its_version(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args, 0, stdout="hugo v0.166.0-78400b4 linux/amd64 BuildDate=unknown\n", stderr=""
        )

    monkeypatch.setattr(hugo_preview.shutil, "which", lambda _name: "/opt/hugo/hugo")
    monkeypatch.setattr(hugo_preview.subprocess, "run", run)
    body = _call(make_app(demo=True), "GET", "/api/hugo").json()

    assert body["available"] is True
    assert body["version"] == "0.166.0" and body["path"] == "/opt/hugo/hugo"
    ((args, kwargs),) = calls
    assert args == ["/opt/hugo/hugo", "version"]
    assert not kwargs.get("shell")


def test_hugo_version_is_given_time_for_a_slow_first_run(monkeypatch):
    """The first run of a newly installed program on Windows can take many
    seconds while antivirus scans it; a Hugo that is there must not be
    reported missing for that."""
    seen = []

    def run(args, **kwargs):
        seen.append(kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, stdout="hugo v0.166.0\n", stderr="")

    monkeypatch.setattr(hugo_preview.shutil, "which", lambda _name: "/opt/hugo/hugo")
    monkeypatch.setattr(hugo_preview.subprocess, "run", run)
    hugo_preview.hugo_info()

    assert seen == [15.0]


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(["hugo", "version"], 15.0),
        OSError("access denied"),
    ],
)
def test_a_hugo_that_does_not_answer_is_told_apart_from_no_hugo(monkeypatch, failure):
    """Found on PATH but silent: `path` names where, and `error` says it did
    not answer -- the dialog words that differently from 'not installed'."""

    def run(args, **kwargs):
        raise failure

    monkeypatch.setattr(hugo_preview.shutil, "which", lambda _name: "/opt/hugo/hugo")
    monkeypatch.setattr(hugo_preview.subprocess, "run", run)

    info = hugo_preview.hugo_info()
    assert info == {
        "available": False,
        "version": None,
        "path": "/opt/hugo/hugo",
        "error": "did not answer",
    }


def test_a_hugo_that_exits_with_an_error_did_not_answer(monkeypatch):
    monkeypatch.setattr(hugo_preview.shutil, "which", lambda _name: "/opt/hugo/hugo")
    monkeypatch.setattr(
        hugo_preview.subprocess,
        "run",
        lambda args, **_kw: subprocess.CompletedProcess(args, 1, stdout="", stderr="boom"),
    )

    info = hugo_preview.hugo_info()
    assert info["available"] is False and info["error"] == "did not answer"


def test_a_hugo_that_answered_is_remembered(monkeypatch):
    """Asked once per process: the dialog asks on every open and every switch
    to Hugo, and a Hugo that answered stays found."""
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="hugo v0.166.0\n", stderr="")

    monkeypatch.setattr(hugo_preview.shutil, "which", lambda _name: "/opt/hugo/hugo")
    monkeypatch.setattr(hugo_preview.subprocess, "run", run)

    first = hugo_preview.hugo_info()
    first["version"] = "changed by the caller"
    second = hugo_preview.hugo_info()

    assert len(calls) == 1
    assert second["available"] is True and second["version"] == "0.166.0"


def test_a_hugo_that_did_not_answer_is_asked_again(monkeypatch):
    """Not remembered: a Hugo still being scanned on its first run may answer
    the next time the dialog asks."""
    answers = iter([subprocess.TimeoutExpired(["hugo"], 15.0), None])

    def run(args, **kwargs):
        failure = next(answers)
        if failure is not None:
            raise failure
        return subprocess.CompletedProcess(args, 0, stdout="hugo v0.166.0\n", stderr="")

    monkeypatch.setattr(hugo_preview.shutil, "which", lambda _name: "/opt/hugo/hugo")
    monkeypatch.setattr(hugo_preview.subprocess, "run", run)

    assert hugo_preview.hugo_info()["available"] is False
    assert hugo_preview.hugo_info()["available"] is True


def test_no_hugo_on_path_is_not_remembered(monkeypatch):
    """Installing Hugo onto a PATH the console already has is picked up at
    the next ask, with no restart."""
    found = iter([None, "/opt/hugo/hugo"])
    monkeypatch.setattr(hugo_preview.shutil, "which", lambda _name: next(found))
    monkeypatch.setattr(
        hugo_preview.subprocess,
        "run",
        lambda args, **_kw: subprocess.CompletedProcess(args, 0, stdout="hugo v0.1.0\n", stderr=""),
    )

    assert hugo_preview.hugo_info()["path"] is None
    assert hugo_preview.hugo_info()["available"] is True


# --- which folder -------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc",
        "relative-hugo-content",
        "{base}/../escaped-hugo-content",
        "{base}/run",
        "{base}/run/../../escaped-hugo-content",
        "{outside}",
        "{link}",
    ],
)
def test_only_a_folder_the_console_wrote_can_be_previewed(fake_hugo, tmp_path, path):
    """`hugo server` runs the templates in the folder it is given; a path
    from the request is never trusted to be an export."""
    app = make_app(demo=True)
    _export(app)
    outside = tmp_path / "elsewhere" / "escaped-hugo-content"
    (outside / "content").mkdir(parents=True)
    (outside / "layouts").mkdir()
    (outside / "hugo.toml").write_text("", encoding="utf-8")
    (support.ARCHIVES_BASE.parent / "escaped-hugo-content").mkdir(exist_ok=True)
    link = support.ARCHIVES_BASE / "link-hugo-content"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:  # Windows without the right to make symlinks
        if path == "{link}":
            pytest.skip("symlinks cannot be created here")
    raw = path.format(base=support.ARCHIVES_BASE, outside=outside, link=link)

    response = _call(app, "POST", "/api/hugo-preview", {"path": raw})

    assert response.status_code == 403, response.text
    assert fake_hugo == []


def test_an_export_without_the_starter_site_says_how_to_get_one(fake_hugo):
    app = make_app(demo=True)
    folder = _export(app, starter_site=False)

    response = _call(app, "POST", "/api/hugo-preview", {"path": str(folder)})

    assert response.status_code == 422
    assert "starter site" in response.json()["error"]
    assert not (folder / "hugo.toml").exists(), "nothing is written into the folder unasked"
    assert fake_hugo == []


def test_the_starter_site_is_refused_for_another_format():
    app = make_app(demo=True)
    run_demo(lambda _e: None, archive_dir=support.ARCHIVES_BASE / "run", delay=0)

    response = _call(
        app, "POST", "/api/ingest", {"format": "jekyll", "archives": ["run"], "starter_site": True}
    )

    assert response.status_code == 422


def test_without_hugo_nothing_is_started(monkeypatch):
    app = make_app(demo=True)
    folder = _export(app)
    monkeypatch.setattr(
        hugo_preview,
        "hugo_info",
        lambda: {"available": False, "version": None, "path": None, "error": None},
    )

    response = _call(app, "POST", "/api/hugo-preview", {"path": str(folder)})

    assert response.status_code == 422
    assert "not installed" in response.json()["error"]


def test_a_hugo_that_did_not_answer_starts_nothing_and_says_where_it_is(monkeypatch):
    app = make_app(demo=True)
    folder = _export(app)
    monkeypatch.setattr(
        hugo_preview,
        "hugo_info",
        lambda: {
            "available": False,
            "version": None,
            "path": "/opt/hugo/hugo",
            "error": "did not answer",
        },
    )

    response = _call(app, "POST", "/api/hugo-preview", {"path": str(folder)})

    assert response.status_code == 422
    assert "/opt/hugo/hugo" in response.json()["error"]
    assert "did not answer" in response.json()["error"]


# --- the server ----------------------------------------------------------------------


def test_the_preview_is_hugo_server_on_its_own_port_from_an_argument_list(fake_hugo):
    app = make_app(demo=True)
    folder = _export(app)

    response = _call(app, "POST", "/api/hugo-preview", {"path": str(folder)})

    assert response.status_code == 200, response.text
    (process,) = fake_hugo
    args = process.args
    assert isinstance(args, list) and args[:2] == ["/opt/hugo/hugo", "server"]
    port = args[args.index("--port") + 1]
    assert args[args.index("--bind") + 1] == "127.0.0.1"
    assert args[args.index("--baseURL") + 1] == f"http://127.0.0.1:{port}/"
    assert "--renderToMemory" in args and "--disableFastRender" in args
    assert not process.kwargs.get("shell")
    assert Path(process.kwargs["cwd"]) == folder.resolve()
    assert response.json()["url"] == f"http://127.0.0.1:{port}/"
    status = _call(app, "GET", "/api/hugo").json()
    assert status["preview"]["url"] == response.json()["url"]


def test_an_export_from_before_a_restart_can_still_be_previewed(fake_hugo):
    """A Hugo export folder beside the archives is the console's own, even
    when this console process did not write it."""
    folder = _export(make_app(demo=True))
    app = make_app(demo=True)

    response = _call(app, "POST", "/api/hugo-preview", {"path": str(folder)})

    assert response.status_code == 200, response.text


def test_one_preview_at_a_time(fake_hugo):
    app = make_app(demo=True)
    folder = _export(app)

    first = _call(app, "POST", "/api/hugo-preview", {"path": str(folder)}).json()
    second = _call(app, "POST", "/api/hugo-preview", {"path": str(folder)}).json()

    assert len(fake_hugo) == 2
    assert fake_hugo[0].terminated and not fake_hugo[1].terminated
    assert first["ok"] and second["ok"]


def test_stop_ends_the_preview(fake_hugo):
    app = make_app(demo=True)
    folder = _export(app)
    _call(app, "POST", "/api/hugo-preview", {"path": str(folder)})

    stopped = _call(app, "POST", "/api/hugo-preview/stop").json()
    again = _call(app, "POST", "/api/hugo-preview/stop").json()

    assert stopped == {"ok": True, "stopped": True}
    assert again == {"ok": True, "stopped": False}
    assert fake_hugo[0].terminated
    assert _call(app, "GET", "/api/hugo").json()["preview"] is None


def test_the_preview_stops_with_the_console(fake_hugo):
    app = make_app(demo=True)
    folder = _export(app)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/hugo-preview", json={"path": str(folder)}).status_code == 200
        assert not fake_hugo[0].terminated

    assert fake_hugo[0].terminated


def test_a_failed_build_reports_hugos_own_words(fake_hugo, monkeypatch):
    app = make_app(demo=True)
    folder = _export(app)

    def popen(args, **kwargs):
        process = _FakeProcess(
            args,
            exit_code=1,
            output=b'\x1b[31mError:\x1b[0m error building site: "layouts/page.html:3": boom\n',
            **kwargs,
        )
        fake_hugo.append(process)
        return process

    monkeypatch.setattr(hugo_preview, "Popen", popen)
    monkeypatch.setattr(hugo_preview, "port_answers", lambda _port: False)

    response = _call(app, "POST", "/api/hugo-preview", {"path": str(folder)})

    assert response.status_code == 422
    error = response.json()["error"]
    assert "boom" in error and "\x1b[" not in error
    assert _call(app, "GET", "/api/hugo").json()["preview"] is None


def test_a_server_that_never_answers_is_given_up(fake_hugo, monkeypatch):
    monkeypatch.setattr(hugo_preview, "port_answers", lambda _port: False)
    preview = hugo_preview.HugoPreview()
    folder = support.ARCHIVES_BASE / "x-hugo-content"
    folder.mkdir(parents=True)

    with pytest.raises(hugo_preview.PreviewError, match="did not start serving"):
        preview.start(folder, "/opt/hugo/hugo", timeout=0.3)

    assert fake_hugo[0].terminated


# --- for real ------------------------------------------------------------------------


@pytest.mark.skipif(HUGO is None, reason="no hugo binary on PATH")
def test_a_real_preview_serves_the_export_on_its_own_port():
    app = make_app(demo=True)
    folder = _export(app)

    response = _call(app, "POST", "/api/hugo-preview", {"path": str(folder)})
    try:
        assert response.status_code == 200, response.text
        url = response.json()["url"]
        assert url.startswith("http://127.0.0.1:")
        home = httpx.get(url, timeout=30)
        assert home.status_code == 200
        # The demo holds two communities: the front page lists them, and the
        # wiki is in its community's folder.
        assert "Platform Engineering" in home.text
        wiki = httpx.get(
            url + "platform-engineering/wikis/engineering-handbook/onboarding/", timeout=30
        )
        assert wiki.status_code == 200 and "Onboarding" in wiki.text
        assert "Engineering Handbook" in wiki.text
        assert not (folder / "public").exists(), "the preview renders to memory"
    finally:
        stopped = _call(app, "POST", "/api/hugo-preview/stop").json()
    assert stopped["stopped"] is True
    with pytest.raises(httpx.TransportError):
        httpx.get(url, timeout=2)
