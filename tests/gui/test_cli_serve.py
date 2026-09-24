"""`serve_main` -- builds a `Config`, an app, and launches uvicorn.

It builds the same app however it is called. There is no demo mode for a
switch to set: the synthetic deployment lives at an address no real
system can occupy, so which one is read follows from the URL in front of
the console and from nothing else. `--demo` and `--no-demo` are still
accepted, and do nothing, so an existing command keeps working.

Tests inject `run` so no real socket is ever bound.
"""

from __future__ import annotations

from connections_export.cli import serve_main


def _served(argv, tmp_path, monkeypatch, env=None):
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.chdir(tmp_path)  # no connections-export.toml is discovered here
    assert serve_main(argv, env=env if env is not None else {}, run=fake_run) == 0
    return captured


def test_it_serves_on_the_documented_defaults(tmp_path, monkeypatch):
    captured = _served([], tmp_path, monkeypatch)

    assert captured["kwargs"]["host"] == "127.0.0.1"
    assert captured["kwargs"]["port"] == 8000


def test_the_demo_switches_are_accepted_and_change_nothing(tmp_path, monkeypatch):
    """They name a mode that does not exist. Accepted so a script passing
    one does not fail, and carrying no meaning, so that nothing beside the
    address can disagree with it and win."""
    plain = _served([], tmp_path, monkeypatch)["app"]
    demo = _served(["--demo"], tmp_path, monkeypatch)["app"]
    no_demo = _served(["--no-demo"], tmp_path, monkeypatch)["app"]

    for app in (plain, demo, no_demo):
        assert not hasattr(app.state, "demo"), "the app still carries a demo mode"

    routes = {tuple(sorted(r.path for r in app.routes)) for app in (plain, demo, no_demo)}
    assert len(routes) == 1, "the switches build different apps"


def test_a_configured_base_url_exposes_live_auth_settings(tmp_path, monkeypatch):
    """A live console must not hide authentication settings as demo-only."""
    (tmp_path / "connections-export.toml").write_text(
        'base_url = "https://connections.example.corp"\n', encoding="utf-8"
    )
    captured = _served([], tmp_path, monkeypatch)

    from starlette.testclient import TestClient

    response = TestClient(captured["app"]).get(
        "http://127.0.0.1/api/settings", headers={"host": "127.0.0.1"}
    )
    assert response.json()["demo"] is False
    assert response.json()["auth_mode"] == "sspi"


def test_a_configured_base_url_does_not_change_what_is_built(tmp_path, monkeypatch):
    """It decides what a URL-less request means, not what the server is."""
    (tmp_path / "connections-export.toml").write_text(
        'base_url = "https://connections.example.corp"\n', encoding="utf-8"
    )
    captured = _served([], tmp_path, monkeypatch)

    assert not hasattr(captured["app"].state, "demo")


def test_serve_main_host_and_port_flags():
    captured = {}

    def fake_run(app, **kwargs):
        captured["kwargs"] = kwargs

    exit_code = serve_main(["--host", "0.0.0.0", "--port", "9001"], env={}, run=fake_run)

    assert exit_code == 0
    assert captured["kwargs"]["host"] == "0.0.0.0"
    assert captured["kwargs"]["port"] == 9001


def test_serve_main_never_calls_uvicorn_run_directly(monkeypatch):
    """Belt-and-suspenders on top of dependency injection: even if a
    test forgot to inject `run`, this proves the production default is
    wired through `uvicorn.run` and not, say, invoked eagerly at import
    time -- by replacing the real thing and confirming it's the one
    that gets called when `run` isn't injected."""
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))

    exit_code = serve_main(["--port", "9002"], env={})

    assert exit_code == 0
    assert len(calls) == 1
    app, kwargs = calls[0]
    assert kwargs["port"] == 9002


def test_free_port_returns_preferred_when_available():
    import socket

    from connections_export.cli import _free_port

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert _free_port("127.0.0.1", free) == free


def test_free_port_skips_a_busy_port():
    import socket

    from connections_export.cli import _free_port

    with socket.socket() as busy_sock:
        busy_sock.bind(("127.0.0.1", 0))
        busy = busy_sock.getsockname()[1]
        busy_sock.listen()
        chosen = _free_port("127.0.0.1", busy)
    assert chosen != busy
    assert chosen > busy
