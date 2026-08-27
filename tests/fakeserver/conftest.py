"""Shared test helper: drive a fakeserver ASGI app fully in-process via
`httpx.ASGITransport` -- no real socket. `asyncio.run` bridges the
async transport into ordinary sync test functions without adding a
pytest-asyncio dependency.
"""

import asyncio

import httpx


def get(app, path: str, *, params: dict | None = None) -> httpx.Response:
    """Issue a single GET against `app` in-process."""

    async def _do():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://fake") as client:
            return await client.get(path, params=params)

    return asyncio.run(_do())
