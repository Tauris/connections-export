"""The aggregate comment index resolves affected posts by ID, not by feed URL.

Hand-over #4: the aggregate feed's `parent_source` is a FEED URL (the entries
feed for a top-level comment, the post's `entrycomments` feed for a reply),
never a post ID. The resolver used to try it first, so it matched no known post,
every reference fell through unplaced, and the repair dropped to re-reading
almost every post's comment feed (thousands of requests for a 316-post blog).

The fix walks `parent_ref` -- the real parent-ID chain -- and consults
`parent_source` only as a last resort. These tests pin that: a reply resolves to
its post through the ID chain even when its `parent_source` is a useless feed
value, and the invariant `affected posts <= posts named by the aggregate feed`
holds.
"""

from __future__ import annotations

from connections_export.adapters.blogs import ChangedComment
from connections_export.crawler.apps.blogs import _posts_from_changed_comments

POST = "post-uuid-1"
# What `parent_source` actually reduces to on a real deployment: a value that is
# NOT a post ID (a blog uuid from the entries feed, or nothing from a slug URL).
FEED_ISH = "blog-uuid-not-a-post"


def test_a_top_level_comment_resolves_to_its_post():
    changed = [ChangedComment(id="c1", parent_ref=POST, parent_source=FEED_ISH)]

    posts, unplaced = _posts_from_changed_comments(changed, {POST})

    assert posts == {POST}
    assert unplaced == set()


def test_a_reply_resolves_through_the_parent_ref_chain_not_the_feed_url():
    # c2 (a reply) -> c1 (its sibling, top-level) -> POST. Both replies carry a
    # feed-derived parent_source that names no post; only the ref chain works.
    changed = [
        ChangedComment(id="c1", parent_ref=POST, parent_source=FEED_ISH),
        ChangedComment(id="c2", parent_ref="c1", parent_source=FEED_ISH),
    ]

    posts, unplaced = _posts_from_changed_comments(changed, {POST})

    assert posts == {POST}, "the reply must reach its post via parent_ref"
    assert unplaced == set()


def test_a_reply_whose_parent_is_not_in_the_change_set_is_reported_unplaced():
    # Only the reply changed; its parent comment is not in this feed. It cannot
    # be placed here, so it is handed on to be looked up in the archive -- not
    # silently dropped, and not a reason to re-read every post.
    changed = [ChangedComment(id="c2", parent_ref="c1-unknown", parent_source=FEED_ISH)]

    posts, unplaced = _posts_from_changed_comments(changed, {POST})

    assert posts == set()
    assert unplaced == {"c1-unknown"}


def test_affected_posts_never_exceed_the_posts_the_feed_names():
    changed = [
        ChangedComment(id="c1", parent_ref=POST, parent_source=FEED_ISH),
        ChangedComment(id="c2", parent_ref="c1", parent_source=FEED_ISH),
        ChangedComment(id="c3", parent_ref=POST, parent_source=FEED_ISH),
    ]

    posts, _ = _posts_from_changed_comments(changed, {POST, "post-uuid-2"})

    assert posts <= {POST, "post-uuid-2"}
    assert posts == {POST}  # only the post actually named
