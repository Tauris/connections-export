"""Stage 2 / Phase 4: the derive-level Blogs & Forums round-trip proof.

Mirrors `tests/derive/test_roundtrip.py` (the wiki round-trip): synthesize
Blogs + Forums, crawl them (real `HttpClient` over the in-process ASGI
bridge) into an `Archive` with a small page size to force pagination, then
`derive(archive)` and assert the assembled interchange model matches the
source `BlogSet`/`ForumSet` -- every blog/post/comment (with comment
threading via `parent_comment_id`) and every forum/topic/reply, the reply
tree rebuilt into the id-keyed model (top-level `reply_ids`, per-reply
`child_ids`, arbitrary depth), flags preserved, bodies present, and body
assets classified to the correct scope.

`derive(archive)` reads only the archive on disk -- `blogset`/`forumset`
are never passed to it, only used afterwards to build the oracle.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import crawl_blogs, crawl_forums
from connections_export.derive import derive
from connections_export.derive.assemble import DeriveError
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_blogs,
    synthesize_forums,
)
from tests.crawler.conftest import make_client

BASE_URL = "https://fake"


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url=BASE_URL, output_dir=tmp_path / "archive", **overrides)


def _seed(**kw) -> SynthSeed:
    base = dict(
        seed=71,
        wiki_count=1,
        depth=1,
        pages_per_level=1,
        blog_count=2,
        posts_per_blog=3,
        comments_per_post=3,
        forum_count=2,
        topics_per_forum=3,
        replies_per_topic=2,
        reply_tree_depth=3,
    )
    base.update(kw)
    return SynthSeed(**base)


def _crawl_blogs_and_forums(tmp_path: Path, seed: SynthSeed, *, page_size: int):
    """Crawl blogs + forums (no wikis) into one archive and return
    `(interchange, blogset, forumset)`. The fake needs a wikiset to build,
    but `crawl_blogs`/`crawl_forums` never fetch the wikis feed, so the
    archive holds no wiki starting point -- exactly the blogs/forums-only
    case the derive resilience change is about."""
    wikiset = synthesize(SynthSeed(seed=seed.seed, wiki_count=1, depth=1, pages_per_level=1))
    blogset = synthesize_blogs(seed)
    forumset = synthesize_forums(seed)
    app = make_app(wikiset, blogset=blogset, forumset=forumset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    r1 = crawl_blogs(
        config=_config(tmp_path, page_size=page_size),
        client=client,
        archive=archive,
        blogs_homepage=blogset.homepage,
    )
    r2 = crawl_forums(config=_config(tmp_path, page_size=page_size), client=client, archive=archive)
    assert r1.ok is True
    assert r2.ok is True

    return derive(archive), blogset, forumset


# --- Blogs round-trip --------------------------------------------------


def _assert_blogs_match(interchange, blogset) -> None:
    derived_by_id = {b.id: b for b in interchange.blogs}

    for blog in blogset.blogs:
        assert blog.uuid in derived_by_id, f"blog {blog.handle} missing from derived model"
        derived_blog = derived_by_id[blog.uuid]
        assert derived_blog.handle is not None  # UUID from roller-ui URL when no handle URL
        assert derived_blog.title == blog.title
        # post_ids are the posts in feed (source) order.
        assert derived_blog.post_ids == [p.uuid for p in blog.posts]

        for ordinal, post in enumerate(blog.posts):
            assert post.uuid in derived_blog.posts, f"post {post.slug} missing"
            dp = derived_blog.posts[post.uuid]
            assert dp.title == post.title
            assert dp.author == post.author
            assert dp.ordinal == ordinal
            assert dp.content_html == post.body_html  # body present, byte-identical
            assert dp.tags == post.tags
            assert dp.provenance.note is None  # nothing dropped
            # HCL id confined to provenance (the raw LSID text), never leaked
            # into the interchange shape.
            assert dp.provenance.hcl_id is not None
            assert post.uuid in dp.provenance.hcl_id

            # Comments recovered with one-level threading via parent_comment_id.
            assert len(dp.comments) == len(post.comments)
            derived_comment_by_id = {c.id: c for c in dp.comments}
            for src_comment in post.comments:
                assert src_comment.uuid in derived_comment_by_id
                assert (
                    derived_comment_by_id[src_comment.uuid].parent_comment_id
                    == src_comment.in_reply_to
                )

            # The same-host cover image is classified same-deployment (not
            # mis-flagged external for being an absolute path) and resolved.
            same_assets = [a for a in dp.assets if a.scope == "same"]
            assert same_assets, f"post {post.slug} lost its same-deployment cover image"
            assert same_assets[0].resolved_url.endswith("cover.png")


def test_blogs_roundtrip_matches_source(tmp_path):
    interchange, blogset, _ = _crawl_blogs_and_forums(tmp_path, _seed(), page_size=2)
    _assert_blogs_match(interchange, blogset)
    assert len(interchange.blogs) == len(blogset.blogs)


def test_blogs_comment_threading_reproduces_parent(tmp_path):
    interchange, blogset, _ = _crawl_blogs_and_forums(tmp_path, _seed(), page_size=2)
    # The synthesizer makes the second comment reply to the first; every
    # other comment stays top-level (Blogs is one-level threading).
    blog = blogset.blogs[0]
    post = blog.posts[0]
    derived = interchange.blogs[0].posts[post.uuid]
    by_id = {c.id: c for c in derived.comments}
    assert by_id[post.comments[0].uuid].parent_comment_id is None
    assert by_id[post.comments[1].uuid].parent_comment_id == post.comments[0].uuid


# --- Forums round-trip -------------------------------------------------


def _expected_tree(topic):
    """Independent oracle for a topic's reply tree, straight from the flat
    source replies (never via `build_reply_tree`): the top-level replies
    (ref == the topic) and each reply's ordered children (refs == that
    reply)."""
    top = [r.uuid for r in topic.replies if r.ref == topic.uuid]
    children = {r.uuid: [c.uuid for c in topic.replies if c.ref == r.uuid] for r in topic.replies}
    return top, children


def _assert_forums_match(interchange, forumset) -> None:
    derived_by_id = {f.id: f for f in interchange.forums}

    for forum in forumset.forums:
        assert forum.uuid in derived_by_id, f"forum {forum.uuid} missing"
        derived_forum = derived_by_id[forum.uuid]
        assert derived_forum.title == forum.title
        assert derived_forum.topic_ids == [t.uuid for t in forum.topics]

        for topic in forum.topics:
            assert topic.uuid in derived_forum.topics, f"topic {topic.uuid} missing"
            dt = derived_forum.topics[topic.uuid]
            assert dt.title == topic.title
            assert dt.author == topic.author
            assert dt.content_html == topic.content_html  # body present
            assert dt.flags == sorted(topic.flags)  # flags preserved
            assert dt.provenance.note is None
            assert dt.provenance.hcl_id is not None and topic.uuid in dt.provenance.hcl_id

            # Every reply present, keyed by id -- nothing dropped flattening.
            assert set(dt.replies) == {r.uuid for r in topic.replies}

            # The reply tree matches the independent oracle: top-level order,
            # and every reply's ordered child_ids -- arbitrary depth.
            expected_top, expected_children = _expected_tree(topic)
            assert dt.reply_ids == expected_top
            for reply_uuid, expected_kids in expected_children.items():
                assert dt.replies[reply_uuid].child_ids == expected_kids

            # Reply flags survive (e.g. the `answer` term on an answered
            # topic's last reply).
            for src_reply in topic.replies:
                assert dt.replies[src_reply.uuid].flags == sorted(src_reply.flags)


def test_forums_roundtrip_matches_source(tmp_path):
    interchange, _, forumset = _crawl_blogs_and_forums(tmp_path, _seed(), page_size=2)
    _assert_forums_match(interchange, forumset)
    assert len(interchange.forums) == len(forumset.forums)


def test_forums_reply_tree_has_the_expected_depth(tmp_path):
    seed = _seed()
    interchange, _, forumset = _crawl_blogs_and_forums(tmp_path, seed, page_size=2)
    # topic 0 carries the deep nested chain under its first top-level reply.
    topic = forumset.forums[0].topics[0]
    dt = interchange.forums[0].topics[topic.uuid]

    def depth(reply_id):
        kids = dt.replies[reply_id].child_ids
        return 1 + (max((depth(k) for k in kids), default=0))

    assert max(depth(rid) for rid in dt.reply_ids) >= 1 + seed.reply_tree_depth


# --- resilience: blogs/forums derive with no wikis ---------------------


def test_blogs_forums_only_archive_derives_with_empty_wikis(tmp_path):
    """CRITICAL resilience: an archive with blogs + forums but no wiki
    starting point derives cleanly -- `wikis == []`, NOT a DeriveError."""
    interchange, blogset, forumset = _crawl_blogs_and_forums(tmp_path, _seed(), page_size=2)
    assert interchange.wikis == []
    assert len(interchange.blogs) == len(blogset.blogs)
    assert len(interchange.forums) == len(forumset.forums)


def test_truly_empty_archive_still_raises_derive_error(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    try:
        derive(archive)
    except DeriveError:
        return
    raise AssertionError("empty archive must still raise DeriveError")
