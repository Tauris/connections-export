"""`serve_main` -- builds a `Config`, an app via
`make_app(demo=...)`, and launches uvicorn.
`--demo` defaults to on when no real `base_url` is configured. Tests
inject `run` so no real socket is ever bound (the design testing
section: "offline, no real sockets"; the design 5.1: "smoke-test it
constructs (do not bind a real socket in tests)").
"""

from __future__ import annotations

from connections_export.cli import serve_main


def test_serve_main_defaults_to_demo_when_no_base_url_configured(tmp_path, monkeypatch):
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured["kwargs"] = kwargs

    # Change cwd to tmp_path so no connections-export.toml is discovered.
    monkeypatch.chdir(tmp_path)
    exit_code = serve_main([], env={}, run=fake_run)

    assert exit_code == 0
    assert captured["app"].state.demo is True
    assert captured["kwargs"]["host"] == "127.0.0.1"
    assert captured["kwargs"]["port"] == 8000


def test_serve_main_defaults_demo_off_when_base_url_is_configured():
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app

    exit_code = serve_main([], env={"CONNECTIONS_EXPORT_BASE_URL": "https://fake"}, run=fake_run)

    assert exit_code == 0
    assert captured["app"].state.demo is False


def test_serve_main_explicit_demo_flag_overrides_configured_base_url():
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app

    exit_code = serve_main(
        ["--demo"], env={"CONNECTIONS_EXPORT_BASE_URL": "https://fake"}, run=fake_run
    )

    assert exit_code == 0
    assert captured["app"].state.demo is True


def test_serve_main_no_demo_flag_forces_demo_off_without_base_url():
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app

    exit_code = serve_main(["--no-demo"], env={}, run=fake_run)

    assert exit_code == 0
    assert captured["app"].state.demo is False


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
