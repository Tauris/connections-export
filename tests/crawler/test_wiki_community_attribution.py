"""A scoped wiki crawl records which community it was captured for.

The wikis LIST feed carries `snx:communityUuid`; a scoped crawl deliberately
skips that feed (skip the wikis-feed enumeration) and builds its
`WikiRef` from the label, so the community was simply absent from the archive.
The reader then filed a community's own wiki under "Not in a community", and
the verdict's per-community row showed no pages.

The run knows the answer without asking anything: a community capture crawls
that community's components, so the community is an input to the crawl rather
than something to rediscover. That invents no deployment behaviour -- unlike
assuming a wiki's own feed carries the marker, which no capture has shown.
"""

from __future__ import annotations

import httpx

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import DEMO_COMMUNITY_TITLE, DEMO_COMMUNITY_UUID
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(seed=0, wiki_count=1, depth=2, pages_per_level=2)


def _crawl(tmp_path, **kwargs):
    wikiset = synthesize(SEED)
    label = wikiset.wikis[0].label
    transport = httpx.MockTransport(SyncASGIBridge(make_app(wikiset)).handle_request)
    archive = Archive.open(tmp_path / "archive")
    crawl(
        config=Config(base_url="https://fake", output_dir=tmp_path / "archive"),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=archive,
        emit=lambda _e: None,
        wiki_labels=[label],
        **kwargs,
    )
    return derive(archive)


def test_a_scoped_wiki_records_the_community_it_was_captured_for(tmp_path):
    model = _crawl(
        tmp_path,
        community_uuid=DEMO_COMMUNITY_UUID,
        community_title=DEMO_COMMUNITY_TITLE,
    )

    assert model.wikis, "the wiki was not captured at all"
    assert model.wikis[0].community_uuid == DEMO_COMMUNITY_UUID
    assert model.wikis[0].community_title == DEMO_COMMUNITY_TITLE


def test_a_scoped_wiki_captured_for_no_community_records_none(tmp_path):
    """A wiki crawled on its own belongs to no community, and saying so is
    the honest answer -- not a guess at which one it might be in."""
    model = _crawl(tmp_path)

    assert model.wikis
    assert model.wikis[0].community_uuid is None
