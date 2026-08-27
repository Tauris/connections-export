"""Capturing a community's picture into the archive.

The picture belongs to the community as much as its files do, and it is what
`{logo}` renders in a PDF margin. Captured rather than linked so an archive
read years later still has it.

Where the deployment serves it is still unverified, so this follows whatever
link the community document names rather than a URL shape we would be
guessing at. That means the capture works the moment the link turns out to be
there, and does nothing quietly until then -- which is the right behaviour for
both outcomes.
"""

from __future__ import annotations

import httpx

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl_community_logo
from connections_export.http.client import HttpClient

BASE = "https://fake"
UUID = "c-1"
PNG = bytes.fromhex("89504e470d0a1a0a")


def _deployment(*, link: str | None, image: bytes | None = PNG):
    """A community document, optionally naming a picture."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if "community/instance" in url:
            body = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<entry xmlns="http://www.w3.org/2005/Atom">'
                "<id>urn:x</id><title>Platform Engineering</title>"
                f"{link or ''}</entry>"
            ).encode()
            return httpx.Response(
                200, content=body, headers={"content-type": "application/atom+xml"}
            )
        if image is None:
            return httpx.Response(404, content=b"gone")
        return httpx.Response(200, content=image, headers={"content-type": "image/png"})

    return HttpClient(transport=httpx.MockTransport(handler), sleep=lambda _s: None), seen


def _run(tmp_path, client):
    archive = Archive.open(tmp_path / "archive")
    crawl_community_logo(
        config=Config(base_url=BASE, output_dir=tmp_path),
        client=client,
        archive=archive,
        community_uuid=UUID,
        emit=lambda _e: None,
    )
    return archive


def test_a_named_picture_is_fetched_and_archived(tmp_path):
    client, seen = _deployment(
        link='<link rel="alternate" type="image/png" href="/comm/logo.png"/>'
    )

    archive = _run(tmp_path, client)

    urls = [r.url for r in archive._read_records() if r.outcome == "ok"]
    assert any(url.endswith("/comm/logo.png") for url in urls)


def test_the_bytes_land_in_the_archive(tmp_path):
    """Not just a manifest line: `{logo}` needs the image itself."""
    from connections_export.archive.blobs import read_blob

    client, _ = _deployment(link='<link rel="alternate" type="image/png" href="/comm/logo.png"/>')

    archive = _run(tmp_path, client)

    record = next(r for r in archive._read_records() if r.url.endswith("logo.png"))
    assert read_blob(archive.root, record.body_hash.split(":")[-1]) == PNG


def test_a_relative_href_is_resolved_against_the_deployment(tmp_path):
    client, seen = _deployment(link='<link rel="logo" href="/comm/logo.png"/>')

    _run(tmp_path, client)

    assert any(url == f"{BASE}/comm/logo.png" for url in seen)


def test_a_community_with_no_picture_costs_one_request(tmp_path):
    """Reading the community document is the whole cost when there is nothing
    to fetch. It must not go hunting at guessed URLs."""
    client, seen = _deployment(link=None)

    _run(tmp_path, client)

    assert len(seen) == 1


def test_a_picture_that_cannot_be_fetched_is_not_a_failed_run(tmp_path):
    """A missing logo must never fail the capture around it -- losing a
    community's wiki over its avatar would be absurd."""
    client, _ = _deployment(
        link='<link rel="alternate" type="image/png" href="/comm/logo.png"/>', image=None
    )

    archive = _run(tmp_path, client)

    assert archive.report() is not None
