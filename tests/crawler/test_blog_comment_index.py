"""An update asks one question per blog, not one per post.

A blog comment moves neither its post nor the dated entries feed, so the only
way to find new discussion was to re-read every post's comments on every
update -- one request per post, forever, to discover that almost none had
changed. That is what made a blog update the one that is never free.

The per-blog aggregate comments feed answers "which comments changed since"
in a single request, and a future cutoff returns an empty feed rather than an
error.

So an update reads that feed once, refreshes only the posts it names, and
falls back to the old behaviour whenever it cannot be sure -- a saved request
is never worth a lost conversation.
"""

from __future__ import annotations

import copy

import httpx
import pytest

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl_blogs
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize, synthesize_blogs
from connections_export.gui.bridge import SyncASGIBridge
from connections_export.http.client import HttpClient

SEED = SynthSeed(seed=0)


class _Counting(httpx.MockTransport):
    def __init__(self, app):
        self.urls: list[str] = []
        bridge = SyncASGIBridge(app)

        def handle(request):
            self.urls.append(str(request.url))
            return bridge.handle_request(request)

        super().__init__(handle)

    def matching(self, fragment):
        return [u for u in self.urls if fragment in u]


@pytest.fixture
def blogset():
    return synthesize_blogs(SEED)


def _crawl(blogs, root, transport, *, fetch="resume", since=None):
    crawl_blogs(
        config=Config(base_url="https://fake", output_dir=root, fetch=fetch),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=lambda _e: None,
        blogs_homepage=blogs.homepage,
        since=since,
    )


def _transport(wikis, blogs):
    return _Counting(make_app(wikis, blogset=blogs))


def test_an_update_asks_once_per_blog_which_comments_changed(tmp_path, blogset):
    wikis = synthesize(SEED)
    root = tmp_path / "archive"
    _crawl(blogset, root, _transport(wikis, blogset))

    transport = _transport(wikis, blogset)
    # A cutoff after everything in the dataset: nothing has changed, which is
    # what an update usually finds.
    _crawl(blogset, root, transport, fetch="update", since="2026-01-01T00:00:00Z")

    assert transport.matching("/comments/atom"), "the update never asked which comments changed"
    assert transport.matching("/feed/entrycomments/") == [], (
        "nothing changed, yet posts' comments were re-read anyway"
    )


def test_a_comment_on_an_old_post_survives_a_dated_update(tmp_path, blogset):
    """The gap underneath the optimisation, and the reason it is worth having.

    The dated entries feed reports posts that MOVED, and a comment moves
    nothing -- so a post whose only change is a new comment is never returned,
    never reached, and its discussion was lost with the run reporting success.
    Reproduced against the old behaviour before this was written.
    """
    wikis = synthesize(SEED)
    root = tmp_path / "archive"
    _crawl(blogset, root, _transport(wikis, blogset))

    grown = copy.deepcopy(blogset)
    old_post = grown.blogs[0].posts[-1]
    late = copy.deepcopy(old_post.comments[0])
    late.uuid = "comment-late"
    late.content_html = "<p>A LATE COMMENT</p>"
    late.published = "2026-08-24T10:00:00Z"
    old_post.comments.append(late)

    _crawl(grown, root, _transport(wikis, grown), fetch="update", since="2026-01-01T00:00:00Z")

    bodies = [
        c.content_html or ""
        for blog in derive(Archive.open(root)).blogs
        for entry in blog.posts.values()
        for c in entry.comments
    ]
    assert any("A LATE COMMENT" in b for b in bodies), (
        "a comment added to an old post was lost by a dated update"
    )


def test_a_comment_added_to_an_old_post_is_captured_with_a_wide_cutoff(tmp_path, blogset):
    """The same content, reached the ordinary way: with a cutoff older than
    everything, the dated feed returns the post itself."""
    wikis = synthesize(SEED)
    root = tmp_path / "archive"
    _crawl(blogset, root, _transport(wikis, blogset))

    grown = copy.deepcopy(blogset)
    post = grown.blogs[0].posts[-1]  # the oldest, untouched otherwise
    newest = copy.deepcopy(post.comments[0])
    newest.uuid = "comment-brand-new"
    newest.content_html = "<p>A LATE COMMENT</p>"
    newest.published = "2030-01-01T00:00:00Z"
    post.comments.append(newest)

    _crawl(grown, root, _transport(wikis, grown), fetch="update", since="2019-01-01T00:00:00Z")

    bodies = [
        c.content_html or ""
        for blog in derive(Archive.open(root)).blogs
        for entry in blog.posts.values()
        for c in entry.comments
    ]
    assert any("A LATE COMMENT" in b for b in bodies), "the shortcut lost a new comment"


def test_a_full_capture_still_reads_every_post_s_comments(tmp_path, blogset):
    """With no cutoff there is nothing to be incremental about, and the
    aggregate feed answers a question nobody asked."""
    wikis = synthesize(SEED)
    transport = _transport(wikis, blogset)
    _crawl(blogset, tmp_path / "archive", transport)

    posts = sum(len(b.posts) for b in blogset.blogs)
    assert len(transport.matching("/feed/entrycomments/")) >= posts


def test_a_reference_to_something_the_blog_does_not_hold_re_reads_everything(tmp_path, blogset):
    """The chain ends somewhere unrecognisable -- a comment on a post that is
    not in this blog's entries feed at all. Rather than decide which
    conversation to leave behind, re-read the lot, which is what it did before
    the index existed."""
    import connections_export.crawler.apps.blogs as crawl_module
    from connections_export.adapters.blogs import ChangedComment

    wikis = synthesize(SEED)
    root = tmp_path / "archive"
    _crawl(blogset, root, _transport(wikis, blogset))

    original = crawl_module._changed_comments
    try:
        crawl_module._changed_comments = lambda *a, **k: [
            ChangedComment(id="c-unknown", parent_ref="not-a-post-here", parent_source=None)
        ]
        transport = _transport(wikis, blogset)
        _crawl(blogset, root, transport, fetch="update", since="2026-01-01T00:00:00Z")
    finally:
        crawl_module._changed_comments = original

    posts = sum(len(b.posts) for b in blogset.blogs)
    assert len(transport.matching("/feed/entrycomments/")) >= posts, (
        "an unplaceable reference must fall back to re-reading every post"
    )


def test_an_index_naming_one_post_refreshes_only_that_one(tmp_path, blogset):
    import connections_export.crawler.apps.blogs as crawl_module
    from connections_export.adapters.blogs import ChangedComment

    wikis = synthesize(SEED)
    root = tmp_path / "archive"
    _crawl(blogset, root, _transport(wikis, blogset))

    target = blogset.blogs[0].posts[0]
    original = crawl_module._changed_comments
    try:
        # Only the blog that actually holds the post: answering for every
        # blog would make the others fall back, correctly, and hide what this
        # test is measuring.
        def index(fetcher, session, blogref, since):
            if blogref.uuid != blogset.blogs[0].uuid:
                return []
            return [ChangedComment(id="c-new", parent_ref=target.uuid, parent_source=None)]

        crawl_module._changed_comments = index
        transport = _transport(wikis, blogset)
        _crawl(blogset, root, transport, fetch="update", since="2026-01-01T00:00:00Z")
    finally:
        crawl_module._changed_comments = original

    per_post = transport.matching("/feed/entrycomments/")
    assert len(per_post) == 1, per_post
    assert target.slug in per_post[0]


# --- resolving a reply to a reply ------------------------------------------
#
# A comment's `ref` is its IMMEDIATE parent, which for a nested reply is a
# sibling comment. Treating that id as a post id is silently wrong: no post
# matches it, the post is never refreshed, and the reply is lost -- the very
# thing the index exists to prevent.
#
# The chain always ends at a post. Either the intermediate comment is in this
# feed too (it changed), or it is already in the archive (it did not), so it
# can always be followed rather than given up on.


def _nested(blogset):
    """A reply to an existing comment, on an old post."""
    grown = copy.deepcopy(blogset)
    post = grown.blogs[0].posts[-1]
    parent = post.comments[0]
    reply = copy.deepcopy(parent)
    reply.uuid = "comment-nested"
    reply.content_html = "<p>A NESTED LATE REPLY</p>"
    reply.published = "2026-08-24T10:00:00Z"
    reply.in_reply_to = parent.uuid
    post.comments.append(reply)
    return grown, post


def test_a_reply_to_an_existing_comment_is_captured(tmp_path, blogset):
    wikis = synthesize(SEED)
    root = tmp_path / "archive"
    _crawl(blogset, root, _transport(wikis, blogset))

    grown, _post = _nested(blogset)
    _crawl(grown, root, _transport(wikis, grown), fetch="update", since="2026-01-01T00:00:00Z")

    bodies = [
        c.content_html or ""
        for blog in derive(Archive.open(root)).blogs
        for entry in blog.posts.values()
        for c in entry.comments
    ]
    assert any("A NESTED LATE REPLY" in b for b in bodies), (
        "a reply to a comment was lost: its ref names the sibling, not the post"
    )


def test_a_sibling_comment_id_is_never_mistaken_for_a_post(tmp_path, blogset):
    """The failure mode this replaced: an unresolvable ref was put into the
    set of changed posts, where it matched nothing and quietly refreshed
    nothing."""
    from connections_export.adapters.blogs import ChangedComment
    from connections_export.crawler.apps.blogs import _posts_from_changed_comments

    changed = [
        ChangedComment(id="c2", parent_ref="c1", parent_source=None),  # reply to a comment
        ChangedComment(id="c1", parent_ref="post-a", parent_source=None),
    ]
    resolved, unresolved = _posts_from_changed_comments(changed, known_posts={"post-a"})
    assert resolved == {"post-a"}
    assert not unresolved


def test_a_parent_outside_this_feed_is_reported_rather_than_guessed(tmp_path):
    """When the chain ends somewhere we cannot identify, say so: the caller
    goes and looks rather than filing the comment against a made-up post."""
    from connections_export.adapters.blogs import ChangedComment
    from connections_export.crawler.apps.blogs import _posts_from_changed_comments

    changed = [ChangedComment(id="c9", parent_ref="older-comment", parent_source=None)]
    resolved, unresolved = _posts_from_changed_comments(changed, known_posts={"post-a"})
    assert resolved == set()
    assert unresolved == {"older-comment"}


def test_placing_a_reply_costs_the_archive_not_the_deployment(tmp_path, blogset):
    """ "Either we have it, and then we have the rest of the story."

    A reply names its sibling, and that sibling is already in the archive from
    the last capture -- so finding which post holds it is a local lookup, not
    a conversation with the deployment. Only the post that actually gained the
    reply is asked for again.

    The lookup must ask the way the capture STORED it: these feeds are
    archived under their paginated urls, and asking for the bare href missed
    every cached record and turned the lookup into a request per post.
    """
    wikis = synthesize(SEED)
    root = tmp_path / "archive"
    _crawl(blogset, root, _transport(wikis, blogset))

    grown, target = _nested(blogset)
    events: list = []
    transport = _transport(wikis, grown)
    crawl_blogs(
        config=Config(base_url="https://fake", output_dir=root, fetch="update"),
        client=HttpClient(transport=transport, sleep=lambda _s: None),
        archive=Archive.open(root),
        emit=events.append,
        blogs_homepage=grown.homepage,
        since="2026-01-01T00:00:00Z",
    )

    reads = [e for e in events if type(e).__name__ == "Fetched" and "entrycomments" in e.url]
    over_the_wire = [e for e in reads if not e.from_cache]
    assert len(reads) > len(over_the_wire), "the archive answered nothing"
    assert len(over_the_wire) == 1, [e.url for e in over_the_wire]
    assert target.slug in over_the_wire[0].url
