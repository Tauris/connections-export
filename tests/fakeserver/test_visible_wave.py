"""What the fake server reveals, as opposed to what the dataset holds.

The dataset always contains both waves -- it is a pure function of the seed.
What a *request* can see is server state, so "a week passes on the live system"
is something the demo does deliberately at a moment of its choosing, rather
than something that happens while you watch.

Keeping these separate is what lets one demo run capture, advance, and update,
with the same seed producing the same content every time.
"""

from __future__ import annotations

import httpx
import pytest

from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_blogs,
    synthesize_forums,
)
from connections_export.gui.bridge import SyncASGIBridge

SEED = SynthSeed(
    seed=0, wiki_count=1, depth=1, pages_per_level=1, human_names=True, late_reply=True
)


@pytest.fixture
def served():
    blogset = synthesize_blogs(SEED)
    forumset = synthesize_forums(SEED)
    # Start with only the first wave revealed -- the state a demo of
    # extend/update begins from.
    app = make_app(synthesize(SEED), blogset=blogset, forumset=forumset, visible_wave=0)
    with httpx.Client(transport=SyncASGIBridge(app), base_url="https://fake") as http:
        yield http, blogset, forumset


def _entries(http, url: str) -> int:
    return http.get(url).content.count(b"<entry")


def _blog_feed(blogset) -> str:
    return f"/blogs/roller-ui/rendering/feed/{blogset.blogs[0].uuid}/entries/atom"


def test_only_the_first_wave_is_visible_to_begin_with(served):
    """A demo capture must see what a first capture would see -- not the
    content that is meant to arrive later."""
    http, blogset, _ = served
    blog = blogset.blogs[0]

    visible = _entries(http, _blog_feed(blogset))

    assert visible == len([p for p in blog.posts if p.wave == 0])
    assert visible < len(blog.posts)


def test_advancing_reveals_the_later_wave(served):
    http, blogset, _ = served
    before = _entries(http, _blog_feed(blogset))

    http.post("/_demo/advance")

    assert _entries(http, _blog_feed(blogset)) > before


def test_advancing_is_explicit_and_nothing_advances_on_its_own(served):
    """Two identical requests must answer identically. If time passed by
    itself, a demo would be unrepeatable and every assertion about it a race."""
    http, blogset, _ = served

    first = _entries(http, _blog_feed(blogset))
    second = _entries(http, _blog_feed(blogset))

    assert first == second


def test_a_hidden_item_is_not_reachable_by_direct_fetch_either(served):
    """Absent from feeds is not enough: an update that already knew an id --
    from a live PDF, say -- would otherwise reach content the demo says does
    not exist yet."""
    http, blogset, _ = served
    later = next(p for p in blogset.blogs[0].posts if p.wave == 1)

    hidden = http.get(f"/blogs/{blogset.blogs[0].handle}/feed/entrycomments/{later.slug}/atom")

    assert hidden.status_code == 404


def test_the_later_wave_reaches_forums_too(served):
    http, _, forumset = served
    forum = forumset.forums[0]
    url = f"/forums/atom/topics?forumUuid={forum.uuid}"
    before = _entries(http, url)

    http.post("/_demo/advance")

    assert _entries(http, url) > before


def test_the_late_reply_appears_only_after_advancing(served):
    """The reply that lands on an otherwise-unchanged topic: the whole point
    of the comments-hole demonstration is that it was not there at capture
    time."""
    http, _, forumset = served
    topic = next(
        t for f in forumset.forums for t in f.topics if any(r.wave == 1 for r in t.replies)
    )
    url = f"/forums/atom/replies?topicUuid={topic.uuid}"
    before = _entries(http, url)

    http.post("/_demo/advance")

    assert _entries(http, url) == before + 1
