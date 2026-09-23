"""Blog comment feeds are 0-indexed; the crawl must start at page 0.

Blogs paginate from page 0, not 1 (the entry feeds were already known to be
0-indexed). The comment feeds are the same, and starting at page 1 silently
dropped whatever sat on page 0 -- the reported "missing blog comments". The
crawl now reads comments from page 0, and the blog adapter version is bumped
to `blogs-2` so an archive captured by the older code can be recognised as
repairable.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.apps._shared import BLOGS_ADAPTER_VERSION
from connections_export.crawler.crawl import crawl_blogs
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize_blogs
from tests.crawler.conftest import make_client

BASE_URL = "https://fake"


def _blog_app():
    seed = SynthSeed(seed=71, blog_count=1, posts_per_blog=2, comments_per_post=3)
    blogset = synthesize_blogs(seed)
    return make_app(None, blogset=blogset), blogset


def test_the_comment_feed_is_requested_from_page_zero(tmp_path):
    app, _ = _blog_app()
    archive = Archive.open(tmp_path / "archive")

    crawl_blogs(
        config=Config(base_url=BASE_URL, output_dir=tmp_path / "archive", page_size=2),
        client=make_client(app),
        archive=archive,
        blogs_homepage="homepage",
    )

    comment_pages = [
        parse_qs(urlsplit(u).query).get("page", [None])[0]
        for u in archive.seen_urls()
        if "/entrycomments/" in u
    ]
    assert comment_pages, "no comment feed was fetched at all"
    assert "0" in comment_pages, (
        f"comments never requested from page 0: {sorted(set(comment_pages))}"
    )


def test_the_adapter_version_is_bumped_so_old_archives_are_detectable():
    # blogs-1 archives are the ones that missed page 0; the bump is what
    # `archive_repair_status` keys the offer of a repair off.
    assert BLOGS_ADAPTER_VERSION == "blogs-2"
