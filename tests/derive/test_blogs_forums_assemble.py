"""Phase 4 unit-level assembly: Blogs & Forums derived from small,
hand-built archives -- no crawl, no HTTP -- mirroring `test_assemble.py`.
Each archive is written directly at the exact `?ps=&page=1` URLs
`ArchiveIndex.paginate` re-derives, using `connections_export.fakeserver.atom`'s
serializers for realistic bytes.

These cover the pieces the crawl round-trip can't force through the real
crawler: a same-deployment body asset whose blob *is* archived (resolved
present=True), and a per-feed fault surfacing as a `Provenance.note`
rather than a dropped post/topic.
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from connections_export.archive.records import Outcome
from connections_export.archive.store import Archive
from connections_export.derive.assemble import derive
from connections_export.fakeserver import atom as fake_atom
from connections_export.fakeserver.model import (
    BlogSet,
    FakeBlog,
    FakeBlogComment,
    FakeBlogPost,
    FakeForum,
    FakeForumReply,
    FakeForumTopic,
    ForumSet,
)
from tests.archive.conftest import FakeResponse

BASE_URL = "https://fake"
PS = 500


class _ArchiveBuilder:
    def __init__(self, tmp_path):
        self.archive = Archive.open(tmp_path / "archive")

    def write(self, url, content, *, content_type="application/atom+xml"):
        self.archive.write_response(
            FakeResponse(
                url=url,
                method="GET",
                status=200,
                headers={"Content-Type": content_type},
                content=content,
            ),
            fetched_at="2026-01-01T00:00:00Z",
        )

    def fail(self, url, *, error="simulated failure"):
        self.archive.write_failure(
            url=url,
            method="GET",
            fetched_at="2026-01-01T00:00:00Z",
            outcome=Outcome.transport_error,
            error=error,
        )


def _p1(feed_base_url: str) -> str:
    """`feed_base_url` with `?ps=PS&page=1` added (preserving any existing
    query) -- the exact page-1 URL both the crawler archived and
    `ArchiveIndex.paginate` looks up."""
    split = urlsplit(feed_base_url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.update({"ps": str(PS), "page": "1"})
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


# --------------------------------------------------------------------
# Blogs
# --------------------------------------------------------------------


def _p0(feed_base_url: str) -> str:
    """`feed_base_url` with `?ps=PS&page=0` added -- the page-0 URL for
    HCL Connections blog entry feeds, which are 0-indexed."""
    split = urlsplit(feed_base_url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.update({"ps": str(PS), "page": "0"})
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _seed_blog(builder: _ArchiveBuilder, blogset: BlogSet, *, seed_comments=True) -> None:
    homepage = blogset.homepage
    builder.write(
        _p1(f"{BASE_URL}/blogs/{homepage}/feed/blogs/atom"),
        fake_atom.blogs_list_feed(blogset, base_url=BASE_URL, ps=PS, page=1),
    )
    for blog in blogset.blogs:
        # The list feed emits roller-ui URLs as self_url, so the
        # derive layer follows roller-ui URLs. Seed at the roller-ui path.
        # Blog entry feeds are 0-indexed, so this seeds at page=0
        # (what the real crawler now archives and derive now looks up).
        roller_url = f"{BASE_URL}/blogs/roller-ui/rendering/feed/{blog.uuid}/entries/atom"
        builder.write(
            _p0(roller_url),
            fake_atom.blog_entries_feed(blog, base_url=BASE_URL, ps=PS, page=0),
        )
        if seed_comments:
            for post in blog.posts:
                builder.write(
                    _p1(f"{BASE_URL}/blogs/{blog.handle}/feed/entrycomments/{post.slug}/atom"),
                    fake_atom.blog_comments_feed(blog, post, base_url=BASE_URL, ps=PS, page=1),
                )


def _blog_with_post(*, body_html: str, comments=()) -> BlogSet:
    post = FakeBlogPost(
        uuid="post-uuid",
        slug="post-slug",
        title="A Post",
        author="Blogger",
        author_userid="blogger0",
        body_html=body_html,
        published="2026-01-01T00:00:00Z",
        updated="2026-01-02T00:00:00Z",
        comments=list(comments),
    )
    blog = FakeBlog(
        uuid="a1b2c3d4-0000-0000-0000-000000000000",
        handle="blog0",
        title="Blog Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        posts=[post],
    )
    return BlogSet(homepage="blog0", blogs=[blog])


def test_blog_post_same_deployment_asset_resolves_to_an_archived_blob(tmp_path):
    # A post body referencing a same-host image whose blob IS in the
    # archive -> resolved present=True with the blob hash (the "same-
    # deployment body assets resolved to blobs" acceptance).
    img_path = "/blogs/blog0/resource/post-slug/cover.png"
    blogset = _blog_with_post(body_html=f'<p>hi</p><img src="{img_path}" alt="cover" />')

    builder = _ArchiveBuilder(tmp_path)
    _seed_blog(builder, blogset)
    builder.write(f"{BASE_URL}{img_path}", b"\x89PNG-bytes", content_type="image/png")

    interchange = derive(builder.archive)

    post = interchange.blogs[0].posts["post-uuid"]
    same = [a for a in post.assets if a.scope == "same"]
    assert len(same) == 1
    assert same[0].present is True
    assert same[0].blob_hash is not None
    assert same[0].resolved_url == f"{BASE_URL}{img_path}"


def test_blog_post_comment_thread_and_provenance(tmp_path):
    comments = [
        FakeBlogComment(
            uuid="c1", author="A", author_userid="a", content_html="<p>1</p>", published="p"
        ),
        FakeBlogComment(
            uuid="c2",
            author="B",
            author_userid="b",
            content_html="<p>2</p>",
            published="p",
            in_reply_to="c1",
        ),
    ]
    blogset = _blog_with_post(body_html="<p>body</p>", comments=comments)

    builder = _ArchiveBuilder(tmp_path)
    _seed_blog(builder, blogset)

    interchange = derive(builder.archive)
    post = interchange.blogs[0].posts["post-uuid"]

    by_id = {c.id: c for c in post.comments}
    assert by_id["c1"].parent_comment_id is None
    assert by_id["c2"].parent_comment_id == "c1"
    # LSID confined to provenance; the interchange id is the bare uuid.
    assert post.id == "post-uuid"
    assert "post-uuid" in post.provenance.hcl_id
    assert post.provenance.note is None


def test_blog_post_comments_feed_failure_is_noted_not_dropped(tmp_path):
    blogset = _blog_with_post(
        body_html="<p>body</p>",
        comments=[
            FakeBlogComment(
                uuid="c1", author="A", author_userid="a", content_html="<p>1</p>", published="p"
            )
        ],
    )

    builder = _ArchiveBuilder(tmp_path)
    # Seed everything except the comments feed, which is failed instead.
    _seed_blog(builder, blogset, seed_comments=False)
    builder.fail(_p1(f"{BASE_URL}/blogs/blog0/feed/entrycomments/post-slug/atom"), error="timeout")

    interchange = derive(builder.archive)
    post = interchange.blogs[0].posts["post-uuid"]

    assert post.comments == []  # gap, not a crash
    assert post.provenance.note is not None
    assert "comments" in post.provenance.note
    assert "timeout" in post.provenance.note


def test_blogs_only_archive_derives_with_empty_wikis(tmp_path):
    blogset = _blog_with_post(body_html="<p>body</p>")
    builder = _ArchiveBuilder(tmp_path)
    _seed_blog(builder, blogset)

    interchange = derive(builder.archive)

    assert interchange.wikis == []
    assert interchange.forums == []
    assert len(interchange.blogs) == 1
    assert interchange.blogs[0].handle is not None  # UUID from roller-ui URL


# --------------------------------------------------------------------
# Forums
# --------------------------------------------------------------------


def _seed_forum(builder: _ArchiveBuilder, forumset: ForumSet, *, seed_replies=True) -> None:
    builder.write(
        _p1(f"{BASE_URL}/forums/atom/forums"),
        fake_atom.forums_list_feed(forumset, base_url=BASE_URL, ps=PS, page=1),
    )
    for forum in forumset.forums:
        builder.write(
            _p1(f"{BASE_URL}/forums/atom/topics?forumUuid={forum.uuid}"),
            fake_atom.forum_topics_feed(forum, base_url=BASE_URL, ps=PS, page=1),
        )
        if seed_replies:
            for topic in forum.topics:
                builder.write(
                    _p1(f"{BASE_URL}/forums/atom/replies?topicUuid={topic.uuid}"),
                    fake_atom.forum_replies_feed(topic, base_url=BASE_URL, ps=PS, page=1),
                )


def _forum_with_tree() -> ForumSet:
    # Two top-level replies (ref = topic); a two-deep chain under r1.
    topic = FakeForumTopic(
        uuid="topic-uuid",
        forum_uuid="forum-uuid",
        title="Topic",
        author="Poster",
        content_html="<p>discuss</p>",
        published="p",
        flags=["question", "answered"],
        replies=[
            FakeForumReply(
                uuid="r1",
                author="A",
                content_html="<p>r1</p>",
                published="p",
                ref="topic-uuid",
                source_topic="topic-uuid",
            ),
            FakeForumReply(
                uuid="r2",
                author="B",
                content_html="<p>r2</p>",
                published="p",
                ref="topic-uuid",
                source_topic="topic-uuid",
            ),
            FakeForumReply(
                uuid="r1a",
                author="C",
                content_html="<p>r1a</p>",
                published="p",
                ref="r1",
                source_topic="topic-uuid",
            ),
            FakeForumReply(
                uuid="r1a1",
                author="D",
                content_html="<p>r1a1</p>",
                published="p",
                ref="r1a",
                source_topic="topic-uuid",
                flags=["answer"],
            ),
        ],
    )
    forum = FakeForum(
        uuid="forum-uuid",
        title="Forum Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        topics=[topic],
    )
    return ForumSet(forums=[forum])


def test_forum_reply_tree_flattens_to_id_keyed_model(tmp_path):
    forumset = _forum_with_tree()
    builder = _ArchiveBuilder(tmp_path)
    _seed_forum(builder, forumset)

    interchange = derive(builder.archive)
    topic = interchange.forums[0].topics["topic-uuid"]

    # Top-level (ordered) reply_ids are the tree roots.
    assert topic.reply_ids == ["r1", "r2"]
    # Every reply, any depth, flat in `replies` keyed by id.
    assert set(topic.replies) == {"r1", "r2", "r1a", "r1a1"}
    # child_ids reconstruct the arbitrary-depth chain under r1.
    assert topic.replies["r1"].child_ids == ["r1a"]
    assert topic.replies["r1a"].child_ids == ["r1a1"]
    assert topic.replies["r1a1"].child_ids == []
    assert topic.replies["r2"].child_ids == []
    # Flags preserved on topic and on the answer reply.
    assert topic.flags == ["answered", "question"]
    assert topic.replies["r1a1"].flags == ["answer"]
    # HCL id confined to provenance.
    assert "topic-uuid" in topic.provenance.hcl_id
    assert topic.replies["r1"].provenance.hcl_id == "r1"
    assert topic.provenance.note is None


def _topic(uuid: str) -> FakeForumTopic:
    return FakeForumTopic(
        uuid=uuid,
        forum_uuid="forum-uuid",
        title=f"Topic {uuid}",
        author="Poster",
        content_html=f"<p>{uuid}</p>",
        published="p",
        flags=[],
        replies=[],
    )


def test_preview_limit_keeps_only_crawled_topics_not_every_feed_entry(tmp_path):
    """A single topics-feed page lists every topic inline (body and all), but a
    Preview-N crawl only descends into the first N. The uncrawled remainder must
    not sneak into the model just because the feed enumerated them -- else
    "Preview 2" yields far more than 2 topics in the TOC/PDF (the reported
    surprise). A topic counts as crawled iff its replies feed was *attempted*
    (ok, or a recorded failure kept with a note)."""
    forum = FakeForum(
        uuid="forum-uuid",
        title="Forum Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        topics=[_topic("t0"), _topic("t1"), _topic("t2")],
    )
    forumset = ForumSet(forums=[forum])
    builder = _ArchiveBuilder(tmp_path)
    _seed_forum(builder, forumset, seed_replies=False)  # feed lists t0, t1, t2
    # Crawl descended into t0 (ok) and t1 (attempted, 500'd) only -- t2 was
    # scoped out by the Preview limit and its replies feed never touched.
    builder.write(
        _p1(f"{BASE_URL}/forums/atom/replies?topicUuid=t0"),
        fake_atom.forum_replies_feed(_topic("t0"), base_url=BASE_URL, ps=PS, page=1),
    )
    builder.fail(_p1(f"{BASE_URL}/forums/atom/replies?topicUuid=t1"), error="HTTP 500")

    interchange = derive(builder.archive)
    forum_model = interchange.forums[0]

    assert forum_model.topic_ids == ["t0", "t1"]  # t2 dropped, not silently included
    assert set(forum_model.topics) == {"t0", "t1"}
    assert forum_model.topics["t1"].provenance.note is not None  # failure still noted


def test_forum_replies_feed_failure_is_noted_not_dropped(tmp_path):
    forumset = _forum_with_tree()
    builder = _ArchiveBuilder(tmp_path)
    _seed_forum(builder, forumset, seed_replies=False)
    builder.fail(_p1(f"{BASE_URL}/forums/atom/replies?topicUuid=topic-uuid"), error="HTTP 500")

    interchange = derive(builder.archive)
    topic = interchange.forums[0].topics["topic-uuid"]

    assert topic.reply_ids == []  # gap, not a crash
    assert topic.replies == {}
    assert topic.provenance.note is not None
    assert "replies" in topic.provenance.note
    assert "HTTP 500" in topic.provenance.note


def test_forums_only_archive_derives_with_empty_wikis(tmp_path):
    forumset = _forum_with_tree()
    builder = _ArchiveBuilder(tmp_path)
    _seed_forum(builder, forumset)

    interchange = derive(builder.archive)

    assert interchange.wikis == []
    assert interchange.blogs == []
    assert len(interchange.forums) == 1
