"""`connections-export` with nothing after it.

Someone who has just installed this and types its name should end up looking
at the thing, not at a usage message. A usage message is the right answer to a
wrong command, not to a bare one.

So: the demo, on a port that is actually free, with a browser pointed at it.
"""

from __future__ import annotations

from connections_export import cli


def test_a_bare_command_starts_the_console(monkeypatch):
    served: dict = {}
    monkeypatch.setattr(
        cli, "_serve_for_default_launch", lambda argv: served.update(argv=argv) or 0
    )

    assert cli.main([]) == 0
    assert served["argv"] is not None


def test_the_bare_command_runs_the_demo(monkeypatch):
    """Nothing is configured yet on a fresh install, and a console with no
    deployment behind it can still show what an archive looks like."""
    captured: dict = {}
    monkeypatch.setattr(cli, "serve_main", lambda argv, **kw: captured.update(argv=argv) or 0)

    cli.main([])

    assert "--demo" in captured["argv"]


def test_the_bare_command_opens_a_browser(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(cli, "serve_main", lambda argv, **kw: captured.update(argv=argv) or 0)

    cli.main([])

    assert "--open" in captured["argv"]


def test_help_still_prints_usage(monkeypatch, capsys):
    """A bare command means "show me"; asking for help means "tell me"."""
    monkeypatch.setattr(cli, "serve_main", lambda argv, **kw: 0)

    assert cli.main(["--help"]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_an_unknown_command_still_fails(monkeypatch):
    """Starting a server because a command was misspelled would be a strange
    way to report a typo."""
    monkeypatch.setattr(cli, "serve_main", lambda argv, **kw: 0)

    assert cli.main(["crwal"]) == 2


def test_the_browser_opens_at_the_port_actually_bound(monkeypatch):
    """The requested port may be busy, and serve moves to a free one. Opening
    the requested port would show a browser pointed at somebody else's server,
    or at nothing. So whatever port is handed to the server is the port
    opened."""
    seen: dict = {}
    monkeypatch.setattr(
        cli, "_open_browser_when_listening", lambda host, port: seen.update(port=port)
    )

    cli.serve_main(["--demo", "--open", "--port", "8123"], run=lambda *a, **kw: seen.update(ran=kw))

    assert seen["port"] == seen["ran"]["port"]


def test_the_browser_waits_until_something_is_listening(monkeypatch):
    """`run` blocks, so the browser is launched from a thread that waits for
    the port to answer. Opening immediately shows a connection error for as
    long as startup takes, which reads as a broken install."""
    import socket
    import time

    opened: list = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(1)
    try:
        cli._open_browser_when_listening("127.0.0.1", port)
        deadline = time.monotonic() + 5
        while not opened and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        listener.close()

    assert opened == [f"http://127.0.0.1:{port}/"]


def test_no_browser_is_opened_when_nothing_ever_listens(monkeypatch):
    """A browser that did not open is not itself worth an error message -- the
    server's own output is where a failure to start belongs."""
    import time

    opened: list = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    # A port nothing is on. The wait gives up rather than opening anyway.
    cli._open_browser_when_listening("127.0.0.1", 1)
    time.sleep(0.4)

    assert opened == []


def test_serve_does_not_open_a_browser_unless_asked(monkeypatch):
    """`serve` is also what runs on a machine nobody is sitting at. Launching
    a browser there would be at best pointless."""
    opened: list = []
    monkeypatch.setattr(cli, "_open_browser_when_listening", lambda *a: opened.append(a))

    cli.serve_main(["--demo"], run=lambda *a, **kw: None)

    assert opened == []
