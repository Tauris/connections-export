"""A refused sign-in redirect is explained where the user is looking.

Windows sign-in follows the deployment's redirects to its organisation's
login server, and refuses any other host (`http.auth.is_sign_in_host`). A
refusal is only useful if it says which host and how to allow it -- a
legitimate login server on another domain looks exactly like an attack
until it is listed in `hcl_hosts`. So the message has to reach the console
run's `failed` event and the command line intact, and `hcl_hosts` from the
configuration has to reach the handshake.

`requests` and `requests-negotiate-sspi` are optional and absent offline;
both are stood in for, so the real `SspiAuth.prepare` runs.
"""

from __future__ import annotations

import asyncio
import sys
import types

import httpx
import pytest

from connections_export.gui.app import make_app
from tests.gui.conftest import read_sse

DEPLOYMENT = "https://connections.example.com"
FOREIGN_LOGIN = "https://login.other.example/grab"


class _Response:
    def __init__(self, url, status, location=None):
        self.url = url
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.is_redirect = location is not None


@pytest.fixture
def handshake(monkeypatch):
    """A `requests` stand-in whose `/homepage/` redirects to `target`, and
    which records every URL the handshake was carried to."""
    visited: list[str] = []
    state = {"target": FOREIGN_LOGIN}

    class _Session:
        def __init__(self):
            self.cookies = []
            self.auth = None

        def get(self, url, allow_redirects=True):
            visited.append(url)
            if url.endswith("/homepage/"):
                return _Response(url, 302, state["target"])
            self.cookies = [types.SimpleNamespace(name="LtpaToken2", value="T", domain="")]
            return _Response(url, 200)

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(Session=_Session))
    monkeypatch.setitem(
        sys.modules,
        "requests_negotiate_sspi",
        types.SimpleNamespace(HttpNegotiateAuth=lambda: object()),
    )
    return state, visited


def test_the_command_line_names_the_refused_host(handshake, capsys, tmp_path, monkeypatch):
    from connections_export.cli import crawl_main

    monkeypatch.chdir(tmp_path)
    _state, visited = handshake

    code = crawl_main(
        [f"{DEPLOYMENT}/wikis/home/wiki/handbook", "--output-dir", str(tmp_path / "out")],
        env={},
    )

    err = capsys.readouterr().err
    assert code != 0
    assert "login.other.example" in err
    assert "hcl_hosts" in err
    assert FOREIGN_LOGIN not in visited, "the handshake went to the refused host"


def test_a_login_server_listed_in_hcl_hosts_is_followed(handshake, tmp_path):
    """The configured `hcl_hosts` reaches the handshake."""
    from connections_export.cli import _build_default_client
    from connections_export.config import Config

    state, visited = handshake
    state["target"] = "https://idp.example.net/login"

    client = _build_default_client(
        Config(base_url=DEPLOYMENT, hcl_hosts=["idp.example.net"], output_dir=tmp_path), env={}
    )

    assert "https://idp.example.net/login" in visited
    assert client.cookies.get("LtpaToken2") == "T"


def test_the_console_runs_failed_event_carries_the_message(handshake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = make_app()

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            started = await client.post(
                "/api/start",
                json={
                    "base_url": DEPLOYMENT,
                    "auth_mode": "sspi",
                    "wiki_label": "handbook",
                },
            )
            assert started.status_code == 200
            events: list[dict] = []
            async with client.stream("GET", "/events") as response:
                await read_sse(response, events)
            return events

    events = asyncio.run(_do())

    failures = [event for event in events if event.get("type") == "failed"]
    assert failures, f"no failed event: {[e.get('type') for e in events]}"
    text = " ".join(str(failure) for failure in failures)
    assert "login.other.example" in text
    assert "hcl_hosts" in text
