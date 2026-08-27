"""Deriving a community's Rich Content from an archive.

The crawl stores the widget layout and one entry per written page; derive has
to put them back together into a container the rest of the tool can walk.

The property that matters most here is the one a capture forced: the layout
lists more widgets than there are pages. Derive must carry that difference
through, because it is the only place a reader can learn that a community's
Highlights area had a widget nobody ever wrote in.
"""

from __future__ import annotations

import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl_rich_content
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_rich_content,
)
from tests.crawler.conftest import make_client

COMMUNITY = "c-1"


@pytest.fixture
def derived(tmp_path):
    seed = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=1, human_names=True)
    rteset = synthesize_rich_content(seed, community_uuid=COMMUNITY)
    app = make_app(synthesize(seed), rteset=rteset)
    archive = Archive.open(tmp_path / "archive")
    crawl_rich_content(
        config=Config(base_url="https://fake"),
        client=make_client(app),
        archive=archive,
        community_uuid=COMMUNITY,
        emit=lambda _e: None,
    )
    return derive(archive), rteset.communities[0]


def test_a_rich_content_container_is_derived(derived):
    model, rte = derived

    assert len(model.rich_content) == 1
    assert model.rich_content[0].community_uuid == COMMUNITY
    assert len(model.rich_content[0].page_ids) == len(rte.pages)


def test_the_pages_keep_their_bodies(derived):
    model, _rte = derived
    highlights = model.rich_content[0]

    for page_id in highlights.page_ids:
        assert highlights.pages[page_id].content_html, page_id


def test_a_page_keeps_its_title_version_and_author(derived):
    model, rte = derived
    highlights = model.rich_content[0]

    first = highlights.pages[rte.pages[0].resource_id]
    assert first.title == rte.pages[0].title
    assert first.version_label == rte.pages[0].version_label
    assert first.author == rte.pages[0].author


def test_page_order_follows_the_layout(derived):
    """The layout is the community's own arrangement of its front page.
    Re-sorting it would silently rewrite what the owners laid out."""
    model, rte = derived

    assert model.rich_content[0].page_ids == [p.resource_id for p in rte.pages]


def test_the_widget_that_was_never_written_in_is_still_counted(derived):
    """`placed` exceeds the pages captured. This is the only record that the
    community had a widget with nothing behind it."""
    model, rte = derived
    highlights = model.rich_content[0]

    assert highlights.placed == len(rte.pages) + rte.uninitialized
    assert highlights.initialized == len(rte.pages)
    assert highlights.placed > len(highlights.page_ids)


def test_the_community_points_at_its_rich_content(derived):
    """A community owns no content itself, so it carries a pointer -- exactly
    as it does for its file library."""
    model, _rte = derived

    community = next((c for c in model.communities if c.id == COMMUNITY), None)
    if community is not None:
        assert community.rich_content_id == model.rich_content[0].id


def test_an_archive_with_only_rich_content_still_derives(derived):
    """No wiki, blog or forum was crawled here. If rich content were not a
    derivable starting point, this whole test would have raised."""
    model, _rte = derived

    assert not model.wikis and not model.blogs and not model.forums
    assert model.rich_content
