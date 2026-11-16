"""`connections-export` with nothing after it.

Someone who has just installed this and types its name should end up looking
at the thing, not at a usage message. A usage message is the right answer to a
wrong command, not to a bare one.

So: the demo, on a port that is actually free, with a browser pointed at it.
"""

from __future__ import annotations

import pytest

from connections_export import cli


def test_a_bare_command_starts_the_console(monkeypatch):
    served: dict = {}
    monkeypatch.setattr(
        cli, "_serve_for_default_launch", lambda argv: served.update(argv=argv) or 0
    )

    assert cli.main([]) == 0
    assert served["argv"] is not None


def test_the_bare_command_opens_the_console_without_choosing_a_deployment():
    """It used to pass `--demo`, which meant a console started this way read
    the synthetic deployment whatever URL was dropped on it -- so a real
    community came back empty and the console reported that the community
    held nothing, rather than that it had not looked there.

    It asks for a browser and nothing else. Which deployment gets read
    follows from the URL, as it does everywhere else; the demo is on the
    setup screen either way, as URLs to try."""
    seen = {}

    def fake_serve(argv):
        seen["argv"] = list(argv)
        return 0

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli, "_serve_for_default_launch", fake_serve)
        assert cli.main([]) == 0

    assert "--demo" not in seen["argv"], "a bare launch still forces the demo"
    assert "--open" in seen["argv"], "a bare launch should still open a browser"


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
    # The note printed after opening must not land in a later test's output.
    monkeypatch.setattr(cli, "_REASSURE_AFTER_SECONDS", 0)

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


def _open_against_a_listener(monkeypatch, *, browser_opens: bool) -> list[str]:
    """Run the open-when-listening thread against a real listener and return
    what it printed."""
    import socket
    import time

    printed: list[str] = []
    monkeypatch.setattr(cli, "_REASSURE_AFTER_SECONDS", 0)
    monkeypatch.setattr(cli, "_open_in_browser", lambda url: browser_opens)
    monkeypatch.setattr(
        "builtins.print", lambda *a, **_kw: printed.append(" ".join(str(x) for x in a))
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(1)
    try:
        cli._open_browser_when_listening("127.0.0.1", port)
        deadline = time.monotonic() + 5
        while not printed and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        listener.close()
    return printed


def test_opening_the_browser_ends_on_a_reassuring_line(monkeypatch):
    """Where the browser came up out of sight, the last terminal line was all
    anyone saw -- and when that was a 404 for the icon, people took it for a
    failed start. The last word is now that it is running, and where."""
    printed = _open_against_a_listener(monkeypatch, browser_opens=True)
    assert len(printed) == 1
    assert "the console is running" in printed[0]
    assert "behind other windows" in printed[0]
    assert "http://127.0.0.1:" in printed[0]
    assert "Ctrl+C" in printed[0]


def test_when_no_browser_opens_it_says_to_open_the_address(monkeypatch):
    printed = _open_against_a_listener(monkeypatch, browser_opens=False)
    assert len(printed) == 1
    assert "no browser could be opened" in printed[0]
    assert "Open http://127.0.0.1:" in printed[0]
