"""`console.html` was split into three same-origin files
(`console.html` + `console.css` + `console.js`) so the monolith
shrinks while staying loadable both from the server (`GET /`) and
directly over `file://` (classic `<script src>` / `<link>`, no
`fetch`/module imports, so the sibling files still resolve when the
page is opened straight off disk). This file locks the two new routes
and the served HTML's relative references to them.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui.app import make_app


def _get(path: str) -> httpx.Response:
    app = make_app(demo=True)
    transport = httpx.ASGITransport(app=app)

    async def _do():
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get(path)

    return asyncio.run(_do())


def test_console_css_is_served_with_the_right_content_type():
    response = _get("/console.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_console_js_is_served_with_the_right_content_type():
    response = _get("/console.js")
    assert response.status_code == 200
    assert "text/javascript" in response.headers["content-type"]


def test_served_html_links_the_split_assets_with_relative_paths():
    # Relative (not absolute/`//host`) so the same markup also resolves
    # its siblings when console.html is opened directly via file://.
    html = _get("/").text
    assert 'href="console.css"' in html
    assert 'src="console.js"' in html


def test_served_html_no_longer_inlines_style_or_script_blocks():
    # The split moved the CSS/JS out of the HTML entirely -- this is the
    # behavior-identical refactor's whole point, so guard against a
    # regression back to inlining either block.
    html = _get("/").text
    assert "<style>" not in html
    assert "<script>" not in html
