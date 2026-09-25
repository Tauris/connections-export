"""The console authenticates the way the command line does.

Username/password and pasted-token sign-in read their secrets from the
environment (`CONNECTIONS_EXPORT_USER`/`_PASSWORD`, `CONNECTIONS_EXPORT_TOKEN`),
and the proxy decision reads `HTTPS_PROXY`/`NO_PROXY` from it. The console
built its clients with an empty environment -- a leftover from when it could
only use Windows sign-in -- so on a deployment without SSO a run could never
find the credentials the command line found. Reported, with the fix, in the
public repository's pull request #1 by Christoph Stoettner.
"""

from __future__ import annotations

import asyncio

import httpx

import connections_export.cli as cli_module
from connections_export.gui.app import make_app


def _capture(monkeypatch):
    seen: dict = {}

    def capture(config, env, **_overrides):
        seen["env"] = dict(env or {})
        seen["auth_mode"] = config.auth_mode
        raise RuntimeError("stop: only what the client was built from is checked")

    monkeypatch.setattr(cli_module, "_build_default_client", capture)
    return seen


def test_a_console_run_sees_the_credentials_in_the_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONNECTIONS_EXPORT_USER", "reader")
    monkeypatch.setenv("CONNECTIONS_EXPORT_PASSWORD", "secret")
    seen = _capture(monkeypatch)
    app = make_app()

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            await client.post(
                "/api/start",
                json={
                    "base_url": "https://connections.example.corp",
                    "auth_mode": "basic",
                    "wiki_label": "eng-handbook",
                },
            )
            for _ in range(200):
                if "env" in seen:
                    return
                await asyncio.sleep(0.02)

    asyncio.run(_do())

    assert seen["auth_mode"] == "basic"
    assert seen["env"].get("CONNECTIONS_EXPORT_USER") == "reader"
    assert seen["env"].get("CONNECTIONS_EXPORT_PASSWORD") == "secret"


def test_feed_info_uses_the_environment_and_the_configured_auth_mode(tmp_path, monkeypatch):
    """It asked with Windows sign-in, whatever was configured, and with no
    environment -- so on a username/password deployment it could not count."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "connections-export.toml").write_text('auth_mode = "basic"\n', encoding="utf-8")
    monkeypatch.setenv("CONNECTIONS_EXPORT_USER", "reader")
    seen = _capture(monkeypatch)
    app = make_app()
    app.state.live_cookies = None

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            # The console's flow: the URL is dropped (identified) first.
            await client.post(
                "/api/identify",
                json={"url": "https://connections.example.corp/wikis/home/wiki/eng-handbook"},
            )
            await client.get(
                "/api/feed-info",
                params={"url": "https://connections.example.corp/wikis/home/wiki/eng-handbook"},
            )

    asyncio.run(_do())

    assert seen["auth_mode"] == "basic"
    assert seen["env"].get("CONNECTIONS_EXPORT_USER") == "reader"
