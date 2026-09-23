"""Derive must read blog comments from page 0.

The bug a user reported: repair "recovered nothing". The crawler was fixed to
read per-post comment feeds from page 0 (HCL numbers them from 0; page 1 is
empty or a 400), and a repair re-fetches them correctly into the archive. But
derive reconstructs a legacy archive's comment sequence from the manifest, and
it did that from page 1 -- the generic paginator default. On a real deployment
page 1 is empty or a 400, so derive read past the only page that holds
comments and the model, the Reader, and the "comments recovered" count all
showed zero, though the page-0 responses were sitting in the archive.

This is asserted at the request level, because the in-process fake returns a
comment feed's items regardless of the `page` query -- which is precisely why
no round-trip test caught a page-1 read. The fix is that derive ASKS for
page 0; on a real deployment that is the difference between every comment and
none.
"""

from __future__ import annotations

import re
from pathlib import Path

from connections_export.archive.feeds import FEEDS_FILENAME
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import crawl_blogs
from connections_export.derive import derive
from connections_export.derive import index as index_mod
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize, synthesize_blogs
from tests.crawler.conftest import make_client

BASE_URL = "https://fake"


def _crawl_one_blog(tmp_path: Path) -> Archive:
    seed = SynthSeed(
        seed=71,
        wiki_count=1,
        depth=1,
        pages_per_level=1,
        blog_count=1,
        posts_per_blog=1,
        comments_per_post=3,
    )
    wikiset = synthesize(SynthSeed(seed=seed.seed, wiki_count=1, depth=1, pages_per_level=1))
    blogset = synthesize_blogs(seed)
    client = make_client(make_app(wikiset, blogset=blogset))
    archive = Archive.open(tmp_path / "archive")
    result = crawl_blogs(
        config=Config(base_url=BASE_URL, output_dir=tmp_path / "archive", page_size=2),
        client=client,
        archive=archive,
        blogs_homepage=blogset.homepage,
    )
    assert result.ok is True
    return archive


def _comment_pages_derive_requests(archive: Archive, monkeypatch) -> list[int]:
    """The `page=` numbers derive asks for on each per-post comment feed."""
    requested: list[int] = []
    original = index_mod.ArchiveIndex.get

    def spy(self, url):
        if "entrycomments" in url:
            match = re.search(r"[?&]page=(\d+)", url)
            if match:
                requested.append(int(match.group(1)))
        return original(self, url)

    monkeypatch.setattr(index_mod.ArchiveIndex, "get", spy)
    derive(archive)
    return requested


def test_derive_reads_blog_comments_from_page_zero_on_a_legacy_archive(tmp_path, monkeypatch):
    """A legacy archive has no `feeds.jsonl`, so derive reconstructs the
    comment sequence from the manifest -- and it must begin at page 0, where a
    real deployment keeps the comments. Beginning at page 1 asks a page that on
    a live server is empty or a 400, and the comments are lost."""
    archive = _crawl_one_blog(tmp_path)
    # No recorded walk -> the reconstruct path, where the start page governs.
    (archive.root / FEEDS_FILENAME).unlink()

    pages = _comment_pages_derive_requests(archive, monkeypatch)

    assert pages, "derive asked for no comment feed at all"
    assert 0 in pages, (
        f"derive never requested page 0 of a comment feed (asked for pages {sorted(set(pages))}); "
        "on a live deployment the page-0 comments would be dropped"
    )
