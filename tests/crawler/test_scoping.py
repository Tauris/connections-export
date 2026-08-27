"""`crawl(..., wiki_labels=[...])` scopes traversal to the
named wiki(s) -- go straight to that wiki's nav feed instead of
enumerating the wikis feed first (crawler single-wiki
scoping). `wiki_labels=None` is unchanged -- today's all-wikis
behaviour -- proven here as a regression check alongside the existing
full traversal suite (`tests/crawler/test_traversal.py`), which is
never touched by this change.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.crawl import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from tests.crawler.conftest import make_client


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url="https://fake", output_dir=tmp_path / "archive", **overrides)


def _synth(seed: int) -> object:
    return synthesize(
        SynthSeed(
            seed=seed,
            wiki_count=3,
            depth=1,
            pages_per_level=2,
            comments_per_page=1,
            versions_per_page=1,
            attachments_per_page=1,
        )
    )


def test_scoped_crawl_archives_only_the_named_wiki(tmp_path):
    wikiset = _synth(1)
    target = wikiset.wikis[0]
    other = wikiset.wikis[1]
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    crawl(config=_config(tmp_path), client=client, archive=archive, wiki_labels=[target.label])

    seen = archive.seen_urls()

    def any_containing(*fragments: str) -> bool:
        return any(all(f in url for f in fragments) for url in seen)

    # The named wiki's own nav feed and its pages were fetched...
    assert any_containing(f"/wiki/{target.label}/nav/feed")
    assert any_containing(f"/wiki/{target.label}/page/")
    # ...but the wikis feed itself was never enumerated (the design:
    # "skip the wikis-feed enumeration") and no other wiki was touched.
    assert not any_containing("/wikis/basic/api/wikis/feed")
    assert not any_containing(f"/wiki/{other.label}/")


def test_scoped_crawl_emits_page_derived_only_for_the_named_wiki(tmp_path):
    wikiset = _synth(2)
    target = wikiset.wikis[0]
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl(
        config=_config(tmp_path),
        client=client,
        archive=archive,
        emit=seen_events.append,
        wiki_labels=[target.label],
    )

    derived = [e for e in seen_events if isinstance(e, events.PageDerived)]
    assert derived, "expected the named wiki's pages to be derived"
    assert all(e.wiki == target.label for e in derived)
    expected_pages = len(target.pages)
    assert len(derived) == expected_pages


def test_unscoped_crawl_still_covers_all_wikis_regression(tmp_path):
    """`wiki_labels=None` (the default) must behave exactly as today --
    every wiki traversed (None -> today's behaviour
    (unchanged))."""
    wikiset = _synth(3)
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl(config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append)

    derived_wikis = {e.wiki for e in seen_events if isinstance(e, events.PageDerived)}
    assert derived_wikis == {w.label for w in wikiset.wikis}

    seen = archive.seen_urls()
    assert any("/wikis/basic/api/wikis/feed" in url for url in seen)


def test_scoped_crawl_accepts_multiple_labels(tmp_path):
    wikiset = _synth(4)
    first, second, third = wikiset.wikis
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl(
        config=_config(tmp_path),
        client=client,
        archive=archive,
        emit=seen_events.append,
        wiki_labels=[first.label, second.label],
    )

    derived_wikis = {e.wiki for e in seen_events if isinstance(e, events.PageDerived)}
    assert derived_wikis == {first.label, second.label}
    assert third.label not in derived_wikis
