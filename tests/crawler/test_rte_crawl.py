"""Crawling a community's Rich Content pages.

Two steps in two different applications: the widget layout lists what is
placed, and a per-page route returns the body inline. The parts worth testing
are the seams that a live capture made awkward -- discovery is a different app
from retrieval, a layout lists other applications' widgets too, and a widget
can exist with no page behind it.
"""

from __future__ import annotations

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl_rich_content
from connections_export.crawler.events import (
    Failed,
    RichContentDerived,
    RichContentDiscovered,
)
from connections_export.crawler.events import (
    Warning as CrawlWarning,
)
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_rich_content,
)
from tests.crawler.conftest import make_client

COMMUNITY = "c-1"


def _app_and_set(seed_value: int = 0):
    seed = SynthSeed(seed=seed_value, wiki_count=1, depth=1, pages_per_level=1, human_names=True)
    rteset = synthesize_rich_content(seed, community_uuid=COMMUNITY)
    return make_app(synthesize(seed), rteset=rteset), rteset.communities[0]


def _crawl(tmp_path, *, community=COMMUNITY, **kwargs):
    app, rte = _app_and_set()
    archive = Archive.open(tmp_path / "archive")
    events: list = []
    result = crawl_rich_content(
        config=Config(base_url="https://fake"),
        client=make_client(app),
        archive=archive,
        community_uuid=community,
        emit=events.append,
        **kwargs,
    )
    derived = [e for e in events if isinstance(e, RichContentDerived)]
    return result, derived, events, rte


def test_every_written_page_is_captured(tmp_path):
    result, derived, _events, rte = _crawl(tmp_path)

    assert result.ok
    assert len(derived) == len(rte.pages)


def test_the_body_arrives_inline(tmp_path):
    """No second fetch for content -- if this regressed to an empty body the
    export would be a list of page titles."""
    _result, derived, _events, _rte = _crawl(tmp_path)

    assert derived, "nothing captured"
    assert all(e.body_captured for e in derived)


def test_a_page_keeps_its_title_version_and_author(tmp_path):
    _result, derived, _events, rte = _crawl(tmp_path)

    first = next(e for e in derived if e.title == rte.pages[0].title)
    assert first.version_label == rte.pages[0].version_label
    assert first.author == rte.pages[0].author
    assert first.resource_id == rte.pages[0].resource_id


def test_other_applications_widgets_are_not_fetched_as_pages(tmp_path):
    """The layout lists a Forums and a Files widget, each with a resource id.
    Fetching those as rich content would 404 -- and, worse, would look like a
    failed capture of a page that never existed."""
    result, derived, events, rte = _crawl(tmp_path)

    assert len(derived) == len(rte.pages)
    assert not [e for e in events if isinstance(e, Failed)]
    assert result.ok


def test_the_run_reports_what_was_placed_not_only_what_it_got(tmp_path):
    """A widget placed and never written into has nothing to fetch. The count
    of those must survive into the run, or "3 captured" silently means "4
    existed"."""
    _result, derived, events, rte = _crawl(tmp_path)

    discovered = next(e for e in events if isinstance(e, RichContentDiscovered))
    assert discovered.placed == len(rte.pages) + rte.uninitialized
    assert discovered.initialized == len(rte.pages)
    assert discovered.placed > len(derived)


def test_the_gap_between_placed_and_captured_is_warned_about(tmp_path):
    _result, _derived, events, _rte = _crawl(tmp_path)

    warnings = [e for e in events if isinstance(e, CrawlWarning)]
    assert any("never written into" in (w.detail or "") for w in warnings)


def test_an_unwritten_widget_is_a_note_not_a_truncation(tmp_path):
    """A difference in the source is not a defect in the capture.

    This was emitted as `truncation` because no other kind existed, so the
    console titled it "Possible silent truncation" in red and every healthy run
    of a community with an empty widget ended as "needs attention". An alarm
    that fires on healthy runs is one people learn to ignore -- and it is the
    alarm this whole tool exists to raise.
    """
    _result, _derived, events, _rte = _crawl(tmp_path)

    gap = [
        e
        for e in events
        if isinstance(e, CrawlWarning) and "never written into" in (e.detail or "")
    ]
    assert gap, "the placed-but-unwritten gap must still be reported"
    for warning in gap:
        assert warning.kind == "placed_not_written", (
            f"reported as {warning.kind!r}; an unwritten widget is not lost data"
        )


def test_a_community_with_no_rich_content_is_not_a_failure(tmp_path):
    """Every community has a widget layout; most hold no RTE widgets. That is
    an empty capture, not a broken run."""
    result, derived, events, _rte = _crawl(tmp_path, community="community-without-rte")

    assert result.ok
    assert derived == []
    assert not [e for e in events if isinstance(e, Failed)]


def test_the_cap_limits_pages_captured(tmp_path):
    _result, derived, _events, _rte = _crawl(tmp_path, max_pages_captured=1)

    assert len(derived) == 1


def test_the_author_filter_keeps_only_that_authors_pages(tmp_path):
    _result, all_derived, _events, rte = _crawl(tmp_path)
    target = rte.pages[0].author

    _result, derived, _events, _rte = _crawl(tmp_path, author=target)

    assert derived, "the filter dropped everything"
    assert {e.author for e in derived} == {target}
    assert len(derived) < len(all_derived) or len({p.author for p in rte.pages}) == 1


def test_the_pages_are_archived_for_derive_to_read_back(tmp_path):
    """The crawl's job is the archive, not the events -- if the entries were
    not stored, derive would have nothing to assemble."""
    _result, _derived, _events, _rte = _crawl(tmp_path)

    archive = Archive.open(tmp_path / "archive")
    urls = [record.url for record in archive._read_records()]
    assert any("/connections/rte/community/" in url for url in urls)
    assert any("inline=true" in url for url in urls if "/connections/rte/community/" in url)
    assert any("/communities/service/atom/community/widgets" in url for url in urls)
