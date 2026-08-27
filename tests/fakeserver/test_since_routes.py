"""The fake honours `since` the way the deployment does -- split included.

The demo exists to exercise the real code path. A fake that accepted one tidy
uniform date format would let the adapters' split (RFC 3339 for blogs, epoch
milliseconds for forums) go untested everywhere except against the real system,
which is the one place we cannot test.

And the fake must keep IGNORING `since` on wikis, because the deployment does.
A demo where wikis filtered would prove something false, and the wiki rescan is
precisely the expensive decision this feature asks users to make.
"""

from __future__ import annotations

import httpx
import pytest

from connections_export.adapters.blogs import parse_blogs_feed  # noqa: F401
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_blogs,
    synthesize_forums,
)
from connections_export.gui.bridge import SyncASGIBridge

SEED = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=1, human_names=True)
#: Between the two waves: after everything in wave 0, before anything in wave 1.
BETWEEN_WAVES = "2023-01-01T00:00:00Z"


@pytest.fixture
def client():
    """A server revealing everything -- the default.

    `since` filtering and wave visibility are different mechanisms and these
    tests are about the first one: the content exists, and the only question is
    whether the server filters it correctly. `test_visible_wave.py` covers the
    other half.
    """
    blogset = synthesize_blogs(SEED)
    forumset = synthesize_forums(SEED)
    app = make_app(synthesize(SEED), blogset=blogset, forumset=forumset)
    with httpx.Client(transport=SyncASGIBridge(app), base_url="https://fake") as http:
        yield http, blogset, forumset


def _entries(body: bytes) -> int:
    return body.count(b"<entry")


def test_a_blog_feed_without_a_cutoff_returns_everything(client):
    http, blogset, _ = client
    blog = blogset.blogs[0]

    body = http.get(f"/blogs/roller-ui/rendering/feed/{blog.uuid}/entries/atom").content

    assert _entries(body) == len(blog.posts)


def test_a_blog_feed_with_a_cutoff_returns_only_later_posts(client):
    from connections_export.adapters.blogs import entries_feed_url

    http, blogset, _ = client
    blog = blogset.blogs[0]
    expected = len([p for p in blog.posts if p.wave == 1])

    url = entries_feed_url(base_url="", blog_uuid=blog.uuid, since=BETWEEN_WAVES)
    body = http.get(url).content

    assert _entries(body) == expected
    assert expected > 0, "the fixture has nothing later to find"


def test_a_future_cutoff_returns_nothing_from_a_blog(client):
    """What the live probe saw: HTTP 200, zero entries. Not a 4xx."""
    from connections_export.adapters.blogs import entries_feed_url

    http, blogset, _ = client
    url = entries_feed_url(
        base_url="", blog_uuid=blogset.blogs[0].uuid, since="2099-01-01T00:00:00Z"
    )

    response = http.get(url)

    assert response.status_code == 200
    assert _entries(response.content) == 0


def test_a_forum_feed_with_a_cutoff_returns_only_later_topics(client):
    from connections_export.adapters.forums import topics_url

    http, _, forumset = client
    forum = forumset.forums[0]
    expected = len([t for t in forum.topics if t.wave == 1])

    body = http.get(topics_url(base_url="", forum_uuid=forum.uuid, since=BETWEEN_WAVES)).content

    assert _entries(body) == expected
    assert expected > 0


def test_the_forum_route_reads_milliseconds_not_seconds(client):
    """A seconds value means 1970 and would return the whole forum. The fake
    must be as unforgiving as the adapters are careful."""
    http, _, forumset = client
    forum = forumset.forums[0]

    seconds = http.get(f"/forums/atom/topics?forumUuid={forum.uuid}&since=1672531200").content
    millis = http.get(f"/forums/atom/topics?forumUuid={forum.uuid}&since=1672531200000").content

    assert _entries(seconds) == len(forum.topics), "a seconds value filtered something"
    assert _entries(millis) < len(forum.topics)


def test_a_wiki_feed_ignores_a_cutoff_entirely(client):
    """Live-verified against the deployment: a far-future `since` still
    returned all 110 pages. The fake reproduces that, so the demo cannot
    accidentally prove wikis are cheap to update."""
    http, _, _ = client
    wikiset = synthesize(SEED)
    label = wikiset.wikis[0].label

    unfiltered = http.get(f"/wikis/basic/api/wiki/{label}/feed").content
    filtered = http.get(
        f"/wikis/basic/api/wiki/{label}/feed?since=2099-01-01T00:00:00Z&sortBy=modified"
    ).content

    assert _entries(filtered) == _entries(unfiltered)
    assert _entries(unfiltered) > 0
