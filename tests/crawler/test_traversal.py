"""Traversal core -- wikis feed -> nav feed -> per page
entry/body/comments/versions/attachments, all archived; hierarchy from
the nav feed; `page_derived` per page.
"""

from pathlib import Path

from connections_export.adapters.wikis import parse_nav_feed
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.crawl import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from tests.crawler.conftest import make_client


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url="https://fake", output_dir=tmp_path / "archive", **overrides)


def test_full_wiki_is_archived(tmp_path):
    wikiset = synthesize(
        SynthSeed(
            seed=1,
            wiki_count=1,
            depth=1,
            pages_per_level=1,
            comments_per_page=2,
            versions_per_page=2,
            attachments_per_page=1,
        )
    )
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    crawl(config=_config(tmp_path), client=client, archive=archive)

    seen = archive.seen_urls()

    def any_containing(*fragments: str) -> bool:
        return any(all(f in url for f in fragments) for url in seen)

    assert any_containing("/wikis/basic/api/wikis/feed")
    assert any_containing(f"/wikis/basic/api/wiki/{wiki.label}/nav/feed")
    assert any_containing(f"/wiki/{wiki.label}/page/{page.label}/entry")
    assert any_containing(f"/wiki/{wiki.label}/page/{page.label}/media")
    assert any_containing(f"/wiki/{wiki.label}/page/{page.label}/feed")  # comments (bare)
    assert any_containing(f"/wiki/{wiki.label}/page/{page.label}/feed", "category=version")
    assert any_containing(f"/wiki/{wiki.label}/page/{page.label}/feed", "category=attachment")


def test_hierarchy_and_ordinal_come_from_the_nav_feed(tmp_path):
    wikiset = synthesize(SynthSeed(seed=2, wiki_count=1, depth=2, pages_per_level=2))
    wiki = wikiset.wikis[0]
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    # Independently parse the nav feed ourselves -- the expectation --
    # without going through the crawler at all.
    nav_client = make_client(app)
    nav_url = f"https://fake/wikis/basic/api/wiki/{wiki.label}/nav/feed?tree=true&acls=true"
    nav_result = nav_client.get(nav_url)
    nav_tree = parse_nav_feed(nav_result.content)
    expected = {node.id: (node.parent, node.ordinal) for node in nav_tree.nodes}

    seen_events = []
    crawl(config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append)

    derived = {
        e.page_id: (e.parent, e.ordinal) for e in seen_events if isinstance(e, events.PageDerived)
    }

    assert derived.keys() == expected.keys()
    for page_id, (parent, ordinal) in expected.items():
        want_parent = parent or None
        assert derived[page_id] == (want_parent, ordinal)


def test_page_derived_emitted_per_page_with_counts(tmp_path):
    wikiset = synthesize(
        SynthSeed(
            seed=3,
            wiki_count=1,
            depth=1,
            pages_per_level=1,
            comments_per_page=3,
            versions_per_page=2,
            attachments_per_page=4,
        )
    )
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl(config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append)

    derived = [
        e for e in seen_events if isinstance(e, events.PageDerived) and e.page_id == page.uuid
    ]
    assert len(derived) == 1
    event = derived[0]
    assert event.wiki == wiki.label
    assert event.title == page.title
    assert event.parent is None
    assert event.ordinal == 0
    assert event.comment_count == 3
    assert event.version_count == 2
    assert event.attachment_count == 4


def test_run_started_and_run_complete_bracket_the_event_stream(tmp_path):
    wikiset = synthesize(SynthSeed(seed=4, wiki_count=1, depth=1, pages_per_level=1))
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen_events = []
    crawl(config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append)

    assert isinstance(seen_events[0], events.RunStarted)
    assert seen_events[0].base_url == "https://fake"
    assert isinstance(seen_events[-1], events.RunComplete)
