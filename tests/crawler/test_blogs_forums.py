"""Stage 2: the Blogs & Forums traversal round-trip proof.

Mirrors `test_roundtrip.py`: synthesize Blogs + Forums, serve them from
the fake, crawl them (real `HttpClient` over the in-process ASGI bridge)
into an `Archive`, then RECONSTRUCT independently from the archive --
re-read manifest + blobs, re-parse the stored feed bytes with the
adapters, walking every feed page -- and assert every blog/post/comment
and every forum/topic/reply survives, with the reply tree rebuilt by
`build_reply_tree`. Pagination is forced (small page_size). A fault is
injected for one resource to prove failures are recorded, never silent
gaps. And the new derived events (`BlogPostDerived`/`ForumTopicDerived`)
are asserted on the emitted stream.
"""

from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from connections_export.adapters import atom
from connections_export.adapters.blogs import (
    blogs_list_url,
    parse_blogs_feed,
    parse_entries_feed,
    parse_entry_comments_feed,
)
from connections_export.adapters.forums import (
    build_reply_tree,
    forums_list_url,
    parse_forums_feed,
    parse_replies_feed,
    parse_topics_feed,
    replies_url,
)
from connections_export.archive.blobs import read_blob
from connections_export.archive.records import ManifestRecord, Outcome
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.crawl import crawl_blogs, crawl_forums
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_blogs,
    synthesize_forums,
)
from connections_export.http.client import HttpClient, RetryPolicy
from tests.crawler.conftest import (
    OverrideTransport,
    SyncASGIBridge,
    make_client,
    path_is,
    static_response,
)

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


def _app(seed: SynthSeed):
    wikiset = synthesize(SynthSeed(seed=seed.seed, wiki_count=1, depth=1, pages_per_level=1))
    blogset = synthesize_blogs(seed)
    forumset = synthesize_forums(seed)
    # default_page_size stays None so the crawler's own ps= drives paging.
    app = make_app(wikiset, blogset=blogset, forumset=forumset)
    return app, blogset, forumset


# --- independent archive reconstruction (no crawler internals) --------


def _read_manifest(archive: Archive) -> list[ManifestRecord]:
    path = archive.root / "manifest.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(ManifestRecord.model_validate_json(line))
    return out


def _latest_ok_by_url(archive: Archive) -> dict[str, ManifestRecord]:
    by_url: dict[str, ManifestRecord] = {}
    for record in _read_manifest(archive):
        if record.outcome == Outcome.ok:
            by_url[record.url] = record
    return by_url


def _body(archive: Archive, record: ManifestRecord) -> bytes:
    return read_blob(archive.root, record.body_hash.split(":", 1)[-1])


def _paginated_url(base_url: str, *, ps: int, page: int) -> str:
    split = urlsplit(base_url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query["ps"] = str(ps)
    query["page"] = str(page)
    query["pageSize"] = str(ps)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _read_all_pages(by_url, archive, base_url, *, page_size, parser) -> list:
    """Walk `base_url` page by page exactly as `Fetcher.paginate` does,
    stopping at the first page missing from the archive or shorter than
    `page_size`. Independent of the crawler -- plain stdlib + adapters."""
    items: list = []
    page = 1
    while True:
        url = _paginated_url(base_url, ps=page_size, page=page)
        record = by_url.get(url)
        if record is None:
            break
        page_items = parser(_body(archive, record))
        items.extend(page_items)
        if len(page_items) < page_size:
            break
        page += 1
    return items


def _entries_and_comment_urls(content: bytes) -> list[tuple]:
    """Re-derive `(BlogPost, comments_feed_url)` from an entries feed --
    the comments URL is the entry's `rel="replies"` link, read here from
    the raw bytes (not the model) so this stays independent of the
    crawler's own private helper of the same shape."""
    posts = parse_entries_feed(content)
    root = atom.parse_xml(content, expected_root="feed")
    hrefs = []
    for entry in atom.entries(root):
        link = atom.links_by_rel(entry).get("replies")
        hrefs.append(link.get("href") if link is not None else None)
    return list(zip(posts, hrefs, strict=True))


def _reconstruct_blogs(archive: Archive, homepage: str, *, page_size: int) -> dict:
    """{blog_uuid: {post_uuid: {title, comment_count}}} from the archive."""
    by_url = _latest_ok_by_url(archive)
    blogs_url = blogs_list_url(base_url=BASE_URL, homepage=homepage)
    blogrefs = _read_all_pages(
        by_url, archive, blogs_url, page_size=page_size, parser=parse_blogs_feed
    )

    out: dict = {}
    for blogref in blogrefs:
        posts: dict = {}
        pairs = _read_all_pages(
            by_url, archive, blogref.self_url, page_size=page_size, parser=_entries_and_comment_urls
        )
        for post, comments_href in pairs:
            comments = (
                _read_all_pages(
                    by_url,
                    archive,
                    comments_href,
                    page_size=page_size,
                    parser=parse_entry_comments_feed,
                )
                if comments_href
                else []
            )
            posts[post.id] = {"title": post.title, "comment_count": len(comments)}
        out[blogref.uuid] = posts
    return out


def _reconstruct_forums(archive: Archive, *, page_size: int) -> dict:
    """{forum_uuid: {topic_uuid: {title, reply_count, tree_ids}}}."""
    by_url = _latest_ok_by_url(archive)
    forums_url = forums_list_url(base_url=BASE_URL)
    forumrefs = _read_all_pages(
        by_url, archive, forums_url, page_size=page_size, parser=parse_forums_feed
    )

    out: dict = {}
    for forumref in forumrefs:
        topics: dict = {}
        parsed_topics = _read_all_pages(
            by_url, archive, forumref.self_url, page_size=page_size, parser=parse_topics_feed
        )
        for topic in parsed_topics:
            feed_url = replies_url(base_url=BASE_URL, topic_uuid=topic.id)
            replies = _read_all_pages(
                by_url, archive, feed_url, page_size=page_size, parser=parse_replies_feed
            )
            tree = build_reply_tree(replies)
            topics[topic.id] = {
                "title": topic.title,
                "reply_count": len(replies),
                "tree_ids": _tree_ids(tree),
                "flags": topic.flags,
            }
        out[forumref.uuid] = topics
    return out


def _tree_ids(nodes) -> set:
    ids: set = set()
    for node in nodes:
        ids.add(node.reply.id)
        ids |= _tree_ids(node.children)
    return ids


def _max_depth(nodes) -> int:
    return 0 if not nodes else 1 + max(_max_depth(n.children) for n in nodes)


# --- Blogs round-trip --------------------------------------------------


def test_blogs_roundtrip_recovers_every_post_and_comment(tmp_path):
    seed = _seed()
    app, blogset, _ = _app(seed)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    result = crawl_blogs(
        config=_config(tmp_path, page_size=2),
        client=client,
        archive=archive,
        blogs_homepage=blogset.homepage,
    )
    assert result.ok is True

    got = _reconstruct_blogs(archive, blogset.homepage, page_size=2)
    for blog in blogset.blogs:
        assert blog.uuid in got, f"blog {blog.handle} missing from reconstruction"
        posts = got[blog.uuid]
        for post in blog.posts:
            assert post.uuid in posts, f"post {post.slug} missing"
            assert posts[post.uuid]["title"] == post.title
            assert posts[post.uuid]["comment_count"] == len(post.comments)

    # Manifest itself shows the page-by-page walk of a paginated feed.
    entries_paths = [
        r.url
        for r in _read_manifest(archive)
        if "entries/atom" in r.url and r.outcome == Outcome.ok
    ]
    assert any("page=1" in u for u in entries_paths)
    assert any(
        "page=2" in u for u in entries_paths
    )  # 3 posts / ps=2 -> page=0, page=1 (dedup), page=2


def test_blogs_traversal_emits_blogpostderived_with_counts(tmp_path):
    seed = _seed()
    app, blogset, _ = _app(seed)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen: list = []
    crawl_blogs(
        config=_config(tmp_path, page_size=2),
        client=client,
        archive=archive,
        blogs_homepage=blogset.homepage,
        emit=seen.append,
    )

    assert isinstance(seen[0], events.RunStarted)
    assert isinstance(seen[-1], events.RunComplete)

    derived = {e.post_id: e for e in seen if isinstance(e, events.BlogPostDerived)}
    total_posts = sum(len(b.posts) for b in blogset.blogs)
    assert len(derived) == total_posts
    for blog in blogset.blogs:
        for post in blog.posts:
            event = derived[post.uuid]
            assert event.blog == blog.uuid
            assert event.blog_title == blog.title
            assert event.title == post.title
            assert event.comment_count == len(post.comments)
            assert event.comments_enabled is post.comments_enabled


# --- Forums round-trip -------------------------------------------------


def test_forums_roundtrip_recovers_every_topic_and_reply_and_tree(tmp_path):
    seed = _seed()
    app, _, forumset = _app(seed)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    result = crawl_forums(config=_config(tmp_path, page_size=2), client=client, archive=archive)
    assert result.ok is True

    got = _reconstruct_forums(archive, page_size=2)
    for forum in forumset.forums:
        assert forum.uuid in got, f"forum {forum.uuid} missing"
        topics = got[forum.uuid]
        for topic in forum.topics:
            assert topic.uuid in topics, f"topic {topic.uuid} missing"
            info = topics[topic.uuid]
            assert info["title"] == topic.title
            assert info["reply_count"] == len(topic.replies)
            # Every reply the topic has is present as a node in the rebuilt
            # tree -- nothing dropped when flattening/reconstructing.
            assert info["tree_ids"] == {r.uuid for r in topic.replies}

    # The first topic carries the deep nested chain -> tree depth > 1.
    first_forum = forumset.forums[0]
    deep_topic = first_forum.topics[0]
    by_url = _latest_ok_by_url(archive)
    replies = _read_all_pages(
        by_url,
        archive,
        replies_url(base_url=BASE_URL, topic_uuid=deep_topic.uuid),
        page_size=2,
        parser=parse_replies_feed,
    )
    assert _max_depth(build_reply_tree(replies)) >= 1 + seed.reply_tree_depth


def test_forums_traversal_emits_forumtopicderived_with_flags(tmp_path):
    seed = _seed()
    app, _, forumset = _app(seed)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen: list = []
    crawl_forums(
        config=_config(tmp_path, page_size=2), client=client, archive=archive, emit=seen.append
    )

    derived = {e.topic_id: e for e in seen if isinstance(e, events.ForumTopicDerived)}
    total_topics = sum(len(f.topics) for f in forumset.forums)
    assert len(derived) == total_topics
    for forum in forumset.forums:
        for topic in forum.topics:
            event = derived[topic.uuid]
            assert event.forum == forum.uuid
            assert event.forum_title == forum.title
            assert event.title == topic.title
            assert event.reply_count == len(topic.replies)
            assert set(event.flags) == set(topic.flags)


# --- search-driven fast path: community/forum narrowing -----------------
# ( follow-up: the Search API has no server-side
# per-forum filter, only a community-level one (docs/reference/
# hcl-search-api.md) -- so seeded topics from a forum OTHER than the one
# the user identified must be dropped client-side, cheaply (before their
# replies are ever fetched), never silently kept.)


def test_search_seeded_fast_path_restricts_to_one_forum(tmp_path):
    """Two seeded topics from two DIFFERENT forums, `restrict_to_forum_uuids`
    pinned to one: only that forum's topic is derived/kept; the other is
    pruned (`reason="wrong-forum"`) BEFORE its replies feed (or anything
    else about it) is ever fetched -- the whole point of restricting
    client-side is to avoid paying for threads that get thrown away."""
    seed = _seed()
    app, _, forumset = _app(seed)
    forum_a, forum_b = forumset.forums[0], forumset.forums[1]
    topic_a, topic_b = forum_a.topics[0], forum_b.topics[0]
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen: list = []
    result = crawl_forums(
        config=_config(tmp_path, page_size=2),
        client=client,
        archive=archive,
        emit=seen.append,
        topic_ids=[topic_a.uuid, topic_b.uuid],
        forum_uuids=None,
        direct_topic_only=True,
        restrict_to_forum_uuids=[forum_a.uuid],
    )
    assert result.ok is True

    derived = [e for e in seen if isinstance(e, events.ForumTopicDerived)]
    assert [e.topic_id for e in derived] == [topic_a.uuid]
    # The lightweight per-forum title prefetch (direct_topic_only=True) gives
    # the KEPT forum a real name, not just its bare uuid.
    assert derived[0].forum_title == forum_a.title

    pruned = [e for e in seen if isinstance(e, events.Pruned)]
    assert len(pruned) == 1
    assert pruned[0].id == topic_b.uuid and pruned[0].kind == "topic"
    assert pruned[0].reason == "wrong-forum"

    # The dropped topic's replies feed was never fetched at all -- pruning
    # happens BEFORE that fetch (only its own single-topic document, needed
    # to learn its forum_uuid in the first place, was fetched).
    all_urls = archive.seen_urls()
    assert not any(
        "/forums/atom/replies" in u and f"topicUuid={topic_b.uuid}" in u for u in all_urls
    )
    # The kept topic's replies WERE fetched.
    assert any("/forums/atom/replies" in u and f"topicUuid={topic_a.uuid}" in u for u in all_urls)


# --- fault injection: nothing is silently lost -------------------------


def test_forums_fault_is_recorded_not_a_silent_gap(tmp_path):
    seed = _seed()
    app, _, forumset = _app(seed)
    forum = forumset.forums[0]
    faulted_topic = forum.topics[0]

    # Force the one topic's replies feed to 500 (matched by path + the
    # topicUuid query), leaving every other resource served normally.
    inner = SyncASGIBridge(app)
    transport = OverrideTransport(
        inner,
        overrides=[
            (
                path_is("/forums/atom/replies", query_contains=f"topicUuid={faulted_topic.uuid}"),
                static_response(500, b"boom"),
            )
        ],
    )
    client = HttpClient(
        transport=transport, retry_policy=RetryPolicy(max_attempts=1), sleep=lambda _s: None
    )
    archive = Archive.open(tmp_path / "archive")

    seen: list = []
    result = crawl_forums(
        config=_config(tmp_path, page_size=2), client=client, archive=archive, emit=seen.append
    )

    # A hard failure flags the run and is an explicit Failed event + record.
    assert result.ok is False
    failed = [e for e in seen if isinstance(e, events.Failed)]
    assert any(
        f"topicUuid={faulted_topic.uuid}" in e.url and e.kind == "http_error" for e in failed
    )
    all_urls = {r.url for r in _read_manifest(archive)}
    assert any(f"topicUuid={faulted_topic.uuid}" in u for u in all_urls)
    assert not any(
        f"topicUuid={faulted_topic.uuid}" in u for u in archive.seen_urls()
    )  # never counted ok

    # The faulted topic reconstructs with zero replies (an explicit gap
    # backed by the failure above), while its sibling topics are intact.
    got = _reconstruct_forums(archive, page_size=2)[forum.uuid]
    assert got[faulted_topic.uuid]["reply_count"] == 0
    for topic in forum.topics[1:]:
        assert got[topic.uuid]["reply_count"] == len(topic.replies)


def test_a_blog_reached_by_uuid_alone_still_reports_its_name(tmp_path):
    """A community ingest selects every component by id, and a blog that is
    not in the homepage list feed got a synthetic ref with `title=None` -- so
    the live console had nothing to call it but the uuid."""
    from connections_export.crawler.events import BlogPostDerived

    seed = _seed()
    app, blogset, _forumset = _app(seed)
    target = blogset.blogs[0]
    seen = []

    crawl_blogs(
        config=_config(tmp_path),
        client=HttpClient(transport=SyncASGIBridge(app)),
        archive=Archive.open(tmp_path / "archive"),
        # A homepage that lists nothing forces the uuid-only fallback path.
        blogs_homepage="nonexistent-homepage",
        emit=seen.append,
        blog_uuids=[target.uuid],
    )

    titles = {e.blog_title for e in seen if isinstance(e, BlogPostDerived)}
    assert titles, "no blog post events were emitted"
    assert target.title in titles, f"expected the blog's name, got {titles}"
    assert target.uuid not in titles


def test_a_forum_reached_by_uuid_alone_still_reports_its_name(tmp_path):
    """Scoping by uuid skips the forums list feed deliberately (it can hold
    tens of thousands of entries) -- but that list was the only source of the
    forum's name, so every scoped run showed uuids."""
    from connections_export.crawler.events import ForumTopicDerived

    seed = _seed()
    app, _blogset, forumset = _app(seed)
    target = forumset.forums[0]
    seen = []

    crawl_forums(
        config=_config(tmp_path),
        client=HttpClient(transport=SyncASGIBridge(app)),
        archive=Archive.open(tmp_path / "archive"),
        emit=seen.append,
        forum_uuids=[target.uuid],
    )

    titles = {e.forum_title for e in seen if isinstance(e, ForumTopicDerived)}
    assert titles, "no forum topic events were emitted"
    assert target.title in titles, f"expected the forum's name, got {titles}"
    assert target.uuid not in titles


def test_a_scoped_forum_crawl_captures_every_topic(tmp_path):
    """The forum-title prefetch requests the SAME url pagination fetches
    first. If that interferes -- via the fetcher's url cache, or by consuming
    the page -- a scoped forum would come back with fewer topics than it has,
    or none, which renders as a heading with nothing under it."""
    from connections_export.crawler.events import ForumTopicDerived

    seed = _seed(topics_per_forum=8)
    app, _blogset, forumset = _app(seed)
    seen = []

    crawl_forums(
        config=_config(tmp_path),
        client=HttpClient(transport=SyncASGIBridge(app)),
        archive=Archive.open(tmp_path / "archive"),
        emit=seen.append,
        forum_uuids=[f.uuid for f in forumset.forums],
    )

    got = {}
    for event in seen:
        if isinstance(event, ForumTopicDerived):
            got.setdefault(event.forum_title or event.forum, set()).add(event.topic_id)

    for forum in forumset.forums:
        captured = got.get(forum.title, set())
        assert len(captured) == len(forum.topics), (
            f"{forum.title}: captured {len(captured)} of {len(forum.topics)} topics"
        )


def test_a_wiki_scoped_by_label_reports_its_real_title(tmp_path):
    """A scoped crawl skips the wikis-list enumeration, so the only name it
    had for the wiki was the label out of the URL -- and `title=label` reached
    the reader, the TOC and the PDF body as the wiki's name."""
    from connections_export.crawler import crawl
    from connections_export.derive import derive
    from connections_export.fakeserver import make_app as make_fake
    from connections_export.fakeserver.prototype import build_prototype_wikiset

    wikiset = build_prototype_wikiset()
    target = wikiset.wikis[0]
    assert target.title != target.label, "fixture must distinguish the two"

    archive = Archive.open(tmp_path / "archive")
    crawl(
        config=_config(tmp_path),
        client=HttpClient(transport=SyncASGIBridge(make_fake(wikiset))),
        archive=archive,
        emit=lambda _e: None,
        wiki_labels=[target.label],
    )
    model = derive(archive)

    assert [w.title for w in model.wikis] == [target.title], (
        f"expected {target.title!r}, got {[w.title for w in model.wikis]}"
    )


# --- search-selected community capture ---------------------------------
# For an author-filtered community capture, the community-scoped person
# Search feed picks the root topics and the crawl fetches each of those as a
# whole thread. Search is documented as a SUPERSET of authorship (author OR
# contributor OR community member), so the seeded path has to keep applying
# the author filter -- otherwise a selector that is occasionally too generous
# becomes a capture that is wrong.


def test_seeded_topics_still_pass_the_author_filter(tmp_path):
    """A seeded topic nobody asked for is pruned, not kept because Search
    named it. Before this, the seeded fast path emitted every topic it
    fetched: an "only me" capture seeded from a person query would have kept
    whatever the superset dragged in, under a filter that said otherwise."""
    seed = _seed()
    app, _, forumset = _app(seed)
    forum = forumset.forums[0]
    mine, theirs = forum.topics[0], forum.topics[1]
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen: list = []
    crawl_forums(
        config=_config(tmp_path, page_size=2),
        client=client,
        archive=archive,
        emit=seen.append,
        topic_ids=[mine.uuid, theirs.uuid],
        forum_uuids=None,
        direct_topic_only=True,
        author=mine.author,
    )

    derived = [e for e in seen if isinstance(e, events.ForumTopicDerived)]
    kept = {e.topic_id for e in derived}
    assert mine.uuid in kept
    assert theirs.uuid not in kept or theirs.author == mine.author
    # And the run says what it filtered, exactly as the full walk does --
    # a capture that quietly drops threads is the same defect either way.
    summaries = [e for e in seen if isinstance(e, events.AuthorFilterSummary)]
    assert len(summaries) == 1
    assert summaries[0].total == 2
    assert summaries[0].author == mine.author


def test_seeded_topics_honour_the_preview_limit(tmp_path):
    """The Preview limit is the console's "just show me a bit of it" control.
    The seeded path ignored `max_topics` entirely, so a preview of a
    search-selected community capture fetched every selected thread."""
    seed = _seed()
    app, _, forumset = _app(seed)
    forum = forumset.forums[0]
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen: list = []
    result = crawl_forums(
        config=_config(tmp_path, page_size=2),
        client=client,
        archive=archive,
        emit=seen.append,
        topic_ids=[t.uuid for t in forum.topics],
        forum_uuids=None,
        direct_topic_only=True,
        max_topics=1,
    )
    assert result.ok is True
    derived = [e for e in seen if isinstance(e, events.ForumTopicDerived)]
    assert len(derived) == 1
    # The topics past the limit were never fetched at all.
    urls = archive.seen_urls()
    for topic in forum.topics[1:]:
        assert not any(f"topicUuid={topic.uuid}" in u for u in urls)


def test_seeded_topics_restrict_to_every_selected_forum(tmp_path):
    """A community capture selects a SET of forums, not one. Search is pinned
    to the community, so it returns topics from forums the user did not tick;
    restricting to a single uuid could not express "these two, not that
    third one"."""
    seed = _seed(forum_count=3)
    app, _, forumset = _app(seed)
    kept_forums = forumset.forums[:2]
    dropped = forumset.forums[2]
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    seen: list = []
    crawl_forums(
        config=_config(tmp_path, page_size=2),
        client=client,
        archive=archive,
        emit=seen.append,
        topic_ids=[f.topics[0].uuid for f in forumset.forums],
        forum_uuids=None,
        direct_topic_only=True,
        restrict_to_forum_uuids=[f.uuid for f in kept_forums],
    )

    derived = {e.topic_id for e in seen if isinstance(e, events.ForumTopicDerived)}
    assert derived == {f.topics[0].uuid for f in kept_forums}
    pruned = [e for e in seen if isinstance(e, events.Pruned) and e.reason == "wrong-forum"]
    assert [e.id for e in pruned] == [dropped.topics[0].uuid]
