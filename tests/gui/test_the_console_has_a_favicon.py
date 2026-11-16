"""The browser's own `/favicon.ico` request is answered.

Left unanswered, it was a 404 printed as the last line in the terminal right
after start-up; where the browser had opened out of sight, people read that
line as the application failing to start."""

from __future__ import annotations

import asyncio

import httpx

from connections_export.gui.app import make_app


def _get(path: str) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=make_app(demo=True))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            return await client.get(path)

    return asyncio.run(go())


def test_favicon_ico_is_a_real_icon() -> None:
    response = _get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/x-icon")
    # ICO header: reserved 0, type 1 (icon), at least one image.
    assert response.content[:4] == b"\x00\x00\x01\x00"
    assert int.from_bytes(response.content[4:6], "little") >= 1


def test_favicon_svg_is_served_and_linked_from_the_console() -> None:
    response = _get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    page = _get("/").text
    assert 'rel="icon" href="/favicon.svg"' in page
    assert 'rel="icon" href="/favicon.ico"' in page


def test_the_banner_is_served_and_shown_on_the_overview() -> None:
    response = _get("/banner.webp")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/webp")
    assert response.content[:4] == b"RIFF" and response.content[8:12] == b"WEBP"
    # Small enough to ship in the package.
    assert len(response.content) < 200_000
    page = _get("/").text
    assert 'class="ov-banner" src="/banner.webp"' in page
    assert 'alt="connections-export' in page
