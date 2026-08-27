"""`render_html` over a *real* derived `Interchange` (not the hand-built
fixtures in test_html.py): synthesize -> crawl (real client + fake app,
same pattern as tests/derive/test_roundtrip.py) -> derive(archive) ->
render_html, with `blob_bytes` backed by the real archive's own
`read_blob`. Proves the pure render core also handles exactly what
`derive` produces from a real capture, images and all -- not just
scenarios shaped by hand.
"""

from __future__ import annotations

from pathlib import Path

from connections_export.archive.blobs import read_blob
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import crawl
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.pdf.html import render_html
from tests.crawler.conftest import make_client

BASE_URL = "https://fake"


def _blob_bytes_from_archive(archive: Archive):
    def _lookup(digest: str) -> bytes | None:
        path = archive.root / "blobs" / digest
        if not path.exists():
            return None
        return read_blob(archive.root, digest)

    return _lookup


def test_render_html_over_a_real_derived_interchange(tmp_path: Path):
    wikiset = synthesize(
        SynthSeed(
            seed=42,
            wiki_count=1,
            depth=1,
            pages_per_level=2,
            comments_per_page=2,
            versions_per_page=1,
            attachments_per_page=1,
            tags_per_page=1,
        )
    )
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")
    config = Config(base_url=BASE_URL, output_dir=tmp_path / "archive")

    result = crawl(config=config, client=client, archive=archive)
    assert result.ok is True

    interchange = derive(archive)
    html = render_html(interchange, blob_bytes=_blob_bytes_from_archive(archive))

    # The fake server's same-host and cross-app images (the
    # own body-HTML fixture, `fakeserver.synth._body_html`) really do
    # get captured and embedded -- the round-trip invariant
    # tests/derive/test_roundtrip.py proves ("every same-deployment
    # asset resolved present=True") carries through into the render.
    # The fixture's body images are genthumb SVGs served at a `.png` href;
    # they must embed as image/svg+xml (sniffed from the bytes), not be
    # mislabelled image/png from the href -- otherwise they'd render blank in
    # the PDF (the #37 "diagrams not visible" bug).
    assert "data:image/svg+xml;base64," in html
    assert "[image not captured]" not in html

    # Every page in the wiki got its own section.
    wiki = interchange.wikis[0]
    assert wiki.pages, "synth produced no pages"
    for page_id in wiki.pages:
        assert f'id="p-{page_id}"' in html

    # The body fixture's external link survives, visibly, on its own
    # original URL (External links are kept verbatim).
    assert 'href="https://example.com"' in html
    assert "https://example.com" in html

    # And its internal (self-referencing) link became an in-document
    # anchor, not a raw HCL API href.
    assert "#p-" in html
