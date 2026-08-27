"""Shared helper for tests that inspect the served console.

The console is three same-origin files -- `console.html`,
`console.css` and `console.js` -- so a test has to grep the right one.
Not a test module itself (no `test_` prefix): DOM and markup
assertions belong on `served_console_html`, while JS behaviour and
security assertions (the sandboxed-iframe rule, the
`READER-PURE-BEGIN` block) belong on `served_console_js`, which is
where the script content lives.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui.app import make_app


def _fetch(path: str) -> str:
    app = make_app(demo=True)
    transport = httpx.ASGITransport(app=app)

    async def _do():
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get(path)

    response = asyncio.run(_do())
    assert response.status_code == 200
    # Normalise line endings. These assets are checked out from git, and on
    # Windows the default `core.autocrlf` rewrites them to CRLF -- so every
    # assertion here written with `\n` fails on that platform alone, for a
    # difference the browser does not care about. Normalising in the one place
    # the assets are loaded fixes all ten modules that grep them.
    return response.text.replace("\r\n", "\n")


def served_console_html() -> str:
    return _fetch("/")


def served_console_js() -> str:
    return _fetch("/console.js")


def served_console_css() -> str:
    return _fetch("/console.css")
