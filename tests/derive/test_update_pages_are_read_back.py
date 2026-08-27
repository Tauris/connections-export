"""An update's extra feed pages must be part of the feed derive reads.

The crawler asks a date-filtered feed for what changed, so it archives a URL
carrying `since=`. Derive walks the feed's canonical URL. Those are different
strings, and the archive is keyed by URL -- so without deliberate handling, an
update fetches the new content, stores it, and derive never looks at it.

The failure is invisible from the inside: the run reports success, the archive
grows, and the derived model is unchanged. It showed up only when a demo update
gained forum topics (discovered by their own per-topic feeds) but no blog posts
(which live only inside the entries feed).
"""

from __future__ import annotations

import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl_blogs
from connections_export.crawler.provenance import update_cutoff
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize, synthesize_blogs
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=1, human_names=True)
BASE = "https://fake"


def _stamps(prefix: str):
    return iter([f"{prefix}T{h:02d}:{m:02d}:00Z" for h in range(9, 23) for m in range(0, 60)])


@pytest.fixture
def phased(tmp_path):
    """A capture at wave 0, then an update after wave 1 arrives."""
    blogset = synthesize_blogs(SEED)
    archive = Archive.open(tmp_path / "archive")

    def run(*, wave: int, since: str | None, stamps, **kwargs):
        app = make_app(synthesize(SEED), blogset=blogset, visible_wave=wave)
        crawl_blogs(
            config=Config(base_url=BASE, output_dir=tmp_path, **kwargs),
            client=HttpClient(transport=SyncASGIBridge(app), sleep=lambda _s: None),
            archive=archive,
            blogs_homepage=blogset.homepage,
            emit=lambda _e: None,
            since=since,
            clock=lambda: next(stamps),
        )

    run(wave=0, since=None, stamps=_stamps("2021-06-01"))
    before = derive(archive)
    cutoff = update_cutoff(archive)
    run(wave=1, since=cutoff, stamps=_stamps("2024-06-01"), fetch="update")
    return before, derive(archive), blogset


def test_the_update_reaches_the_derived_model(phased):
    """The whole point: content an update fetched has to appear when the
    archive is read back."""
    before, after, blogset = phased

    def posts(model):
        return {pid for blog in model.blogs for pid in blog.posts}

    later = {p.uuid for b in blogset.blogs for p in b.posts if p.wave == 1}
    assert later, "the fixture has nothing later to find"
    assert not (later & posts(before)), "the first capture already had the later wave"
    assert later <= posts(after), "an update fetched content the derived model never saw"


def test_nothing_the_first_capture_held_is_lost(phased):
    """An update adds; it must never subtract. A derive that read only the
    filtered pages would show the new posts and drop every old one."""
    before, after, _blogset = phased

    def posts(model):
        return {pid for blog in model.blogs for pid in blog.posts}

    assert posts(before) <= posts(after)


def test_no_post_is_counted_twice(phased):
    """The filtered pages overlap the unfiltered ones by construction. The
    result must be a set, not a concatenation."""
    _before, after, _blogset = phased

    for blog in after.blogs:
        assert len(blog.post_ids) == len(set(blog.post_ids))
