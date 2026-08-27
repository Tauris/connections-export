"""Two-pass author-filtered wiki crawl: with
`author=X`, Pass 1 reads structure + text and Pass 2 fetches images/attachments
ONLY for pages X authored -- non-matching pages are crawled for text but their
assets are never fetched, and each is surfaced as a `Pruned` event."""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import events
from connections_export.crawler.crawl import crawl, crawl_blogs, crawl_forums
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import (
    BlogSet,
    FakeBlog,
    FakeBlogPost,
    FakeForum,
    FakeForumReply,
    FakeForumTopic,
    FakePage,
    FakeWiki,
    ForumSet,
    WikiSet,
)
from tests.crawler.conftest import make_client

ME = "J. Weber"
OTHER = "A. Okafor"
MY_IMG = "/wikis/basic/api/wiki/wiki0/page/mine/media/img/mine.png"
THEIR_IMG = "/wikis/basic/api/wiki/wiki0/page/theirs/media/img/theirs.png"


def _config(tmp_path: Path) -> Config:
    return Config(base_url="https://fake", output_dir=tmp_path / "archive")


def _two_author_wikiset() -> WikiSet:
    mine = FakePage(
        uuid="mine",
        label="mine",
        title="Mine",
        parent_uuid=None,
        ordinal=0,
        body_html=f'<p><img src="{MY_IMG}"/></p>',
        author=ME,
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        version_label=1,
    )
    theirs = FakePage(
        uuid="theirs",
        label="theirs",
        title="Theirs",
        parent_uuid=None,
        ordinal=1,
        body_html=f'<p><img src="{THEIR_IMG}"/></p>',
        author=OTHER,
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        version_label=1,
    )
    wiki = FakeWiki(
        uuid="wiki-uuid-1",
        label="wiki0",
        title="Wiki Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        pages=[mine, theirs],
    )
    return WikiSet(wikis=[wiki], base_timestamp_ms=1_700_000_000_000)


def test_filtered_crawl_fetches_assets_only_for_authored_pages(tmp_path):
    app = make_app(_two_author_wikiset())
    archive = Archive.open(tmp_path / "archive")
    seen_events: list = []
    crawl(
        config=_config(tmp_path),
        client=make_client(app),
        archive=archive,
        emit=seen_events.append,
        author=ME,
    )

    seen = archive.seen_urls()
    # My page's image WAS fetched; the other author's image was NOT.
    assert any(url.endswith(MY_IMG) for url in seen), "authored page's image should be fetched"
    assert not any(url.endswith(THEIR_IMG) for url in seen), "pruned page's image must be skipped"

    # The other author's page is surfaced as pruned (seen, not kept), by its id.
    pruned = [e for e in seen_events if isinstance(e, events.Pruned)]
    assert [p.id for p in pruned] == ["theirs"]
    assert pruned[0].kind == "page"

    # Only the matching page enters the hierarchy -- a non-matching page never
    # emits a PageDerived (tree node), so the ingest list shows only matches
    # from the start (no flash-then-remove).
    derived = [e.page_id for e in seen_events if isinstance(e, events.PageDerived)]
    assert derived == ["mine"]

    # Both pages were still crawled for text/structure (faithful archive) --
    # both page bodies are present.
    assert any(url.endswith("/page/mine") or "page/mine/" in url for url in seen)
    assert any("theirs" in url for url in seen)


# Same-deployment media paths (the fakeserver serves these under /files/...).
TOPIC_IMG = "/files/basic/api/library/lib1/document/doc1/media/topic-mine.png"
REPLY_IMG = "/files/basic/api/library/lib1/document/doc2/media/reply-mine.png"
OTHER_TOPIC_IMG = "/files/basic/api/library/lib1/document/doc3/media/topic-other.png"


def _empty_wikiset() -> WikiSet:
    return WikiSet(wikis=[], base_timestamp_ms=1_700_000_000_000)


def _two_thread_forumset() -> ForumSet:
    # t-mine: authored by OTHER, but ME replies -> whole thread kept.
    t_mine = FakeForumTopic(
        uuid="t-mine",
        forum_uuid="forum-uuid",
        title="Mine thread",
        author=OTHER,
        content_html=f'<p><img src="{TOPIC_IMG}"/></p>',
        published="2026-01-01T00:00:00Z",
        replies=[
            FakeForumReply(
                uuid="r1",
                author=ME,
                content_html=f'<p><img src="{REPLY_IMG}"/></p>',
                published="2026-01-01T00:00:00Z",
                ref="t-mine",
                source_topic="t-mine",
            )
        ],
    )
    # t-other: no involvement from ME -> pruned, its image never fetched.
    t_other = FakeForumTopic(
        uuid="t-other",
        forum_uuid="forum-uuid",
        title="Other thread",
        author=OTHER,
        content_html=f'<p><img src="{OTHER_TOPIC_IMG}"/></p>',
        published="2026-01-01T00:00:00Z",
        replies=[],
    )
    forum = FakeForum(
        uuid="forum-uuid",
        title="Forum Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        topics=[t_mine, t_other],
    )
    return ForumSet(forums=[forum], base_timestamp_ms=1_700_000_000_000)


def test_filtered_forum_crawl_keeps_the_whole_thread_i_replied_in(tmp_path):
    app = make_app(_empty_wikiset(), forumset=_two_thread_forumset())
    archive = Archive.open(tmp_path / "archive")
    seen_events: list = []
    crawl_forums(
        config=_config(tmp_path),
        client=make_client(app),
        archive=archive,
        emit=seen_events.append,
        author=ME,
    )
    seen = archive.seen_urls()
    # I only replied in t-mine, but the WHOLE thread's images are kept (topic +
    # my reply) -- the "fill back later" case.
    assert any(url.endswith(TOPIC_IMG) for url in seen), "topic image of my thread kept"
    assert any(url.endswith(REPLY_IMG) for url in seen), "my reply's image kept"
    # The thread I'm not in is pruned; its image is never fetched.
    assert not any(url.endswith(OTHER_TOPIC_IMG) for url in seen)
    pruned = [e.id for e in seen_events if isinstance(e, events.Pruned)]
    assert pruned == ["t-other"]


MY_POST_IMG = "/files/basic/api/library/lib1/document/docp1/media/mine.png"
OTHER_POST_IMG = "/files/basic/api/library/lib1/document/docp2/media/theirs.png"


def _two_author_blogset() -> BlogSet:
    mine = FakeBlogPost(
        uuid="post-mine",
        slug="post-mine",
        title="Mine",
        author=ME,
        author_userid="me",
        body_html=f'<p><img src="{MY_POST_IMG}"/></p>',
        published="2026-01-01T00:00:00Z",
        updated="2026-01-01T00:00:00Z",
    )
    theirs = FakeBlogPost(
        uuid="post-theirs",
        slug="post-theirs",
        title="Theirs",
        author=OTHER,
        author_userid="ao",
        body_html=f'<p><img src="{OTHER_POST_IMG}"/></p>',
        published="2026-01-01T00:00:00Z",
        updated="2026-01-01T00:00:00Z",
    )
    blog = FakeBlog(
        uuid="blog-uuid-1",
        handle="blog0",
        title="Blog Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        posts=[mine, theirs],
    )
    return BlogSet(homepage="blog0", blogs=[blog], base_timestamp_ms=1_700_000_000_000)


def test_filtered_blog_crawl_fetches_assets_only_for_authored_posts(tmp_path):
    app = make_app(
        WikiSet(wikis=[], base_timestamp_ms=1_700_000_000_000), blogset=_two_author_blogset()
    )
    archive = Archive.open(tmp_path / "archive")
    seen_events: list = []
    crawl_blogs(
        config=_config(tmp_path),
        client=make_client(app),
        archive=archive,
        blogs_homepage="blog0",
        emit=seen_events.append,
        author=ME,
    )
    seen = archive.seen_urls()
    assert any(url.endswith(MY_POST_IMG) for url in seen)
    assert not any(url.endswith(OTHER_POST_IMG) for url in seen)
    assert [e.id for e in seen_events if isinstance(e, events.Pruned)] == ["post-theirs"]


def test_zero_match_emits_a_diagnostic_summary_with_identities(tmp_path):
    # The real-world failure: the filter value matches nothing, so nothing is
    # kept. The crawl must say so AND surface the author identities present so
    # the user can copy the exact stored form.
    app = make_app(_two_author_wikiset())  # authors: ME and OTHER
    archive = Archive.open(tmp_path / "archive")
    seen_events: list = []
    crawl(
        config=_config(tmp_path),
        client=make_client(app),
        archive=archive,
        emit=seen_events.append,
        author="Nobody Matching",
    )
    summaries = [e for e in seen_events if isinstance(e, events.AuthorFilterSummary)]
    assert len(summaries) == 1
    s = summaries[0]
    assert s.kept == 0 and s.total == 2
    # The identities actually present are surfaced (name form), so the user
    # can see what to type.
    joined = " ".join(s.identities)
    assert ME in joined and OTHER in joined


def test_no_author_filter_fetches_every_page_image(tmp_path):
    # Regression guard: author=None is the unfiltered single pass -- every
    # image is fetched, no Pruned events.
    app = make_app(_two_author_wikiset())
    archive = Archive.open(tmp_path / "archive")
    seen_events: list = []
    crawl(
        config=_config(tmp_path),
        client=make_client(app),
        archive=archive,
        emit=seen_events.append,
    )
    seen = archive.seen_urls()
    assert any(url.endswith(MY_IMG) for url in seen)
    assert any(url.endswith(THEIR_IMG) for url in seen)
    assert not [e for e in seen_events if isinstance(e, events.Pruned)]


def test_unfiltered_run_still_reports_the_authors_seen(tmp_path):
    # So the user can run once (unfiltered) and PICK the exact author to filter
    # by from the list -- no guessing the name format.
    app = make_app(_two_author_wikiset())
    archive = Archive.open(tmp_path / "archive")
    seen_events: list = []
    crawl(
        config=_config(tmp_path),
        client=make_client(app),
        archive=archive,
        emit=seen_events.append,  # no author -> unfiltered
    )
    (summary,) = [e for e in seen_events if isinstance(e, events.AuthorFilterSummary)]
    assert summary.author == ""  # unfiltered
    assert summary.kept == summary.total == 2  # nothing pruned
    joined = " ".join(summary.identities)
    assert ME in joined and OTHER in joined  # both authors offered as choices
