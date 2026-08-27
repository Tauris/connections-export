"""Stage 2: the fake server synthesizes and serves Blogs and Forums at
the exact URLs the adapters' URL builders produce, so a fetch -> parse
round-trips back to the synthesized data.

Everything served here is not documented (no real XML sample exists for any
Blogs/Forums shape); these round-trips are the validation the adapter
fixtures never had a server for. All offline, in-process via
`httpx.ASGITransport` (conftest `get`), and deterministic.
"""

from connections_export.adapters.blogs import (
    blogs_list_url,
    entries_feed_url,
    entry_comments_url,
    parse_blogs_feed,
    parse_entries_feed,
    parse_entry_comments_feed,
)
from connections_export.adapters.forums import (
    build_reply_tree,
    forums_list_url,
    parse_replies_feed,
    parse_topics_feed,
    replies_url,
    topics_url,
)
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_blogs,
    synthesize_forums,
)

from .conftest import get

# base_url="" so the adapter URL builders return bare paths that the
# in-process client (base_url="https://fake") resolves.
_PATH = ""


def _seed(**kw):
    base = dict(
        seed=71,
        blog_count=2,
        posts_per_blog=3,
        comments_per_post=3,
        forum_count=2,
        topics_per_forum=2,
        replies_per_topic=2,
        reply_tree_depth=3,
    )
    base.update(kw)
    return SynthSeed(**base)


def _app(seed=None):
    seed = seed or _seed()
    # A minimal wikiset is always required by make_app; blogs/forums are
    # additive.
    wikiset = synthesize(SynthSeed(seed=seed.seed, wiki_count=1, depth=1, pages_per_level=1))
    blogset = synthesize_blogs(seed)
    forumset = synthesize_forums(seed)
    app = make_app(wikiset, blogset=blogset, forumset=forumset)
    return app, blogset, forumset


# --------------------------------------------------------------------
# Blogs round-trip: fetch each adapter URL, parse it back, assert the
# synthesized entities survive.
# --------------------------------------------------------------------


def test_blogs_list_feed_roundtrips():
    app, blogset, _ = _app()
    url = blogs_list_url(base_url=_PATH, homepage=blogset.homepage)
    r = get(app, url)
    assert r.status_code == 200
    assert "atom+xml" in r.headers["content-type"]
    refs = parse_blogs_feed(r.content)
    assert [ref.uuid for ref in refs] == [b.uuid for b in blogset.blogs]
    assert [ref.title for ref in refs] == [b.title for b in blogset.blogs]
    # self link points at that blog's own entries feed (roller-ui shape,
    # -confirmed as the real one; not the documented handle-in-path form).
    assert refs[0].self_url.endswith(
        f"/blogs/roller-ui/rendering/feed/{blogset.blogs[0].uuid}/entries/atom"
    )


def test_blogs_list_only_served_at_homepage_handle():
    app, blogset, _ = _app()
    # A non-homepage blog handle does not serve the "list all blogs" feed.
    other = next(b for b in blogset.blogs if b.handle != blogset.homepage)
    r = get(app, blogs_list_url(base_url=_PATH, homepage=other.handle))
    assert r.status_code == 404


def test_blog_entries_feed_roundtrips():
    app, blogset, _ = _app()
    blog = blogset.blogs[0]
    url = entries_feed_url(base_url=_PATH, blog_uuid=blog.uuid)
    posts = parse_entries_feed(get(app, url).content)

    assert [p.id for p in posts] == [p.uuid for p in blog.posts]
    for parsed, synth in zip(posts, blog.posts, strict=True):
        assert parsed.title == synth.title
        assert parsed.author == synth.author
        assert parsed.author_userid == synth.author_userid
        assert parsed.published == synth.published
        assert parsed.updated == synth.updated
        assert parsed.tags == synth.tags
        assert parsed.comments_enabled is synth.comments_enabled
        assert parsed.content_html == synth.body_html
        assert parsed.ranks["comment"] == len(synth.comments)
        assert parsed.ranks["recommendations"] == synth.recommendations
        assert parsed.provenance == f"urn:lsid:ibm.com:blogs:entry-{synth.uuid}"


def test_blog_entry_comments_feed_one_level_threading_roundtrips():
    app, blogset, _ = _app()
    blog = blogset.blogs[0]
    # posts_per_blog=3, comments_per_post=3 -> the second comment on each
    # post replies to the first (one level); the rest are top-level.
    post = blog.posts[0]
    url = entry_comments_url(base_url=_PATH, handle=blog.handle, entry_slug=post.slug)
    comments = parse_entry_comments_feed(get(app, url).content)

    assert [c.id for c in comments] == [c.uuid for c in post.comments]
    assert comments[0].in_reply_to is None
    assert comments[1].in_reply_to == comments[0].id  # normalized bare-uuid parent
    assert comments[2].in_reply_to is None
    assert comments[0].author == post.comments[0].author
    assert comments[0].content_html == post.comments[0].content_html


# --------------------------------------------------------------------
# Forums round-trip.
# --------------------------------------------------------------------


def test_forums_list_feed_is_wellformed_atom():
    # forums.py has forums_list_url but NO parse_forums_feed (unlike
    # Blogs). The feed is served as well-formed Atom carrying the shared
    # fields; there is no adapter parser to assert against, so parse it
    # generically.
    app, _, forumset = _app()
    from connections_export.adapters import atom as adapter_atom

    r = get(app, forums_list_url(base_url=_PATH))
    assert r.status_code == 200
    root = adapter_atom.parse_xml(r.content, expected_root="feed")
    ids = [
        adapter_atom.entry_uuid(adapter_atom.find_text(e, "atom:id"))
        for e in adapter_atom.entries(root)
    ]
    assert ids == [f.uuid for f in forumset.forums]


def test_forum_topics_feed_roundtrips_with_flags_and_type():
    app, _, forumset = _app()
    from connections_export.adapters import atom as adapter_atom
    from connections_export.adapters.forums import entity_type

    forum = forumset.forums[0]
    url = topics_url(base_url=_PATH, forum_uuid=forum.uuid)
    content = get(app, url).content
    topics = parse_topics_feed(content)

    assert [t.id for t in topics] == [t.uuid for t in forum.topics]
    for parsed, synth in zip(topics, forum.topics, strict=True):
        assert parsed.title == synth.title
        assert parsed.author == synth.author
        assert parsed.content_html == synth.content_html
        # forum_uuid comes off the topic's own thr:in-reply-to (the TRAP:
        # data, not a type signal) and matches the parent forum.
        assert parsed.forum_uuid == forum.uuid
        assert parsed.flags == set(synth.flags)
        assert parsed.provenance == f"urn:lsid:ibm.com:forum:{synth.uuid}"

    # entity_type is read from the sn/type category term, never from the
    # (present!) thr:in-reply-to.
    root = adapter_atom.parse_xml(content, expected_root="feed")
    for entry in adapter_atom.entries(root):
        assert adapter_atom.in_reply_to(entry) is not None  # trap present...
        assert entity_type(entry) == "forum-topic"  # ... type still correct


def test_forum_replies_feed_is_flat_and_carries_ref_source():
    app, _, forumset = _app()
    forum = forumset.forums[0]
    topic = forum.topics[0]
    url = replies_url(base_url=_PATH, topic_uuid=topic.uuid)
    replies = parse_replies_feed(get(app, url).content)

    assert [r.id for r in replies] == [r.uuid for r in topic.replies]
    for parsed, synth in zip(replies, topic.replies, strict=True):
        assert parsed.ref == synth.ref
        assert parsed.source_topic == topic.uuid
        assert parsed.flags == set(synth.flags)
    # every reply's source_topic is the topic; at least one reply is the
    # answer.
    assert all(r.source_topic == topic.uuid for r in replies)
    assert any("answer" in r.flags for r in replies)


def test_build_reply_tree_over_served_flat_feed_reconstructs_depth():
    app, _, forumset = _app()
    forum = forumset.forums[0]
    topic = forum.topics[0]
    replies = parse_replies_feed(
        get(app, replies_url(base_url=_PATH, topic_uuid=topic.uuid)).content
    )

    tree = build_reply_tree(replies)

    # replies_per_topic=2 top-level roots (each refs the topic directly).
    assert len(tree) == 2
    reply_ids = {r.uuid for r in topic.replies}
    assert all(node.reply.id in reply_ids for node in tree)

    # The nested chain (reply_tree_depth=3 extra levels under the first
    # root) reconstructs to full depth.
    def max_depth(nodes):
        return 0 if not nodes else 1 + max(max_depth(n.children) for n in nodes)

    assert max_depth(tree) >= 4  # 1 root + 3 nested levels


# --------------------------------------------------------------------
# Determinism and safety.
# --------------------------------------------------------------------


def test_determinism_same_seed_same_bytes():
    from connections_export.fakeserver import atom

    a = synthesize_blogs(_seed())
    b = synthesize_blogs(_seed())
    fa = synthesize_forums(_seed())
    fb = synthesize_forums(_seed())
    base = "https://fake"

    assert atom.blogs_list_feed(a, base_url=base) == atom.blogs_list_feed(b, base_url=base)
    assert atom.blog_entries_feed(a.blogs[0], base_url=base) == atom.blog_entries_feed(
        b.blogs[0], base_url=base
    )
    post_a, post_b = a.blogs[0].posts[0], b.blogs[0].posts[0]
    assert atom.blog_comments_feed(a.blogs[0], post_a, base_url=base) == atom.blog_comments_feed(
        b.blogs[0], post_b, base_url=base
    )
    assert atom.forums_list_feed(fa, base_url=base) == atom.forums_list_feed(fb, base_url=base)
    assert atom.forum_topics_feed(fa.forums[0], base_url=base) == atom.forum_topics_feed(
        fb.forums[0], base_url=base
    )
    topic_a, topic_b = fa.forums[0].topics[0], fb.forums[0].topics[0]
    assert atom.forum_replies_feed(topic_a, base_url=base) == atom.forum_replies_feed(
        topic_b, base_url=base
    )


def test_different_seed_produces_different_uuids():
    a = synthesize_blogs(_seed(seed=1))
    b = synthesize_blogs(_seed(seed=2))
    assert a.blogs[0].posts[0].uuid != b.blogs[0].posts[0].uuid


def test_body_html_never_contains_script():
    blogset = synthesize_blogs(_seed())
    forumset = synthesize_forums(_seed())
    for blog in blogset.blogs:
        for post in blog.posts:
            assert "<script" not in post.body_html.lower()
    for forum in forumset.forums:
        for topic in forum.topics:
            assert "<script" not in topic.content_html.lower()
            for reply in topic.replies:
                assert "<script" not in reply.content_html.lower()


def test_pagination_clamps_to_app_profile_ceiling():
    # Blogs ps_max=50: a request for ps=999 returns at most one page's
    # worth, never more than the ceiling would allow (here fewer posts
    # exist than the ceiling, so all posts come back on one page with no
    # next link -- the clamp must not error).
    app, blogset, _ = _app(_seed(posts_per_blog=3))
    blog = blogset.blogs[0]
    r = get(app, entries_feed_url(base_url=_PATH, blog_uuid=blog.uuid) + "?ps=999")
    posts = parse_entries_feed(r.content)
    assert len(posts) == len(blog.posts)


def test_ps_and_page_slice_the_blog_entries_feed():
    app, blogset, _ = _app(_seed(posts_per_blog=5))
    blog = blogset.blogs[0]
    url = entries_feed_url(base_url=_PATH, blog_uuid=blog.uuid)
    page1 = parse_entries_feed(get(app, url + "?ps=2&page=1").content)
    page2 = parse_entries_feed(get(app, url + "?ps=2&page=2").content)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {p.id for p in page1}.isdisjoint({p.id for p in page2})


# --- demo content quality ---------------------------------------------------
#
# A demo is how this tool is judged without a deployment, so its entries have
# to read as different entries. One shared opening sentence per app -- or one
# shared closing sentence -- makes a whole archive read as the same entry
# repeated, so the tests below assert against exactly that.


def _visible_paragraphs(html_text):
    import re

    return [
        part.strip()
        for part in re.sub(r"<[^>]+>", "\n", html_text).split("\n")
        if len(part.strip()) > 25
    ]


def test_no_paragraph_is_repeated_across_demo_posts_and_topics():
    import collections

    from connections_export.fakeserver.synth import (
        SynthSeed,
        synthesize_blogs,
        synthesize_forums,
    )

    seed = SynthSeed(seed=0, human_names=True)
    paragraphs = []
    for blog in synthesize_blogs(seed).blogs:
        for post in blog.posts:
            paragraphs += _visible_paragraphs(post.body_html)
    for forum in synthesize_forums(seed).forums:
        for topic in forum.topics:
            paragraphs += _visible_paragraphs(topic.content_html)

    repeated = {p: n for p, n in collections.Counter(paragraphs).items() if n > 1}
    assert not repeated, f"demo content repeats itself: {list(repeated)[:3]}"


def test_the_old_filler_sentences_are_gone():
    from connections_export.fakeserver.synth import SynthSeed, synthesize_blogs, synthesize_forums

    seed = SynthSeed(seed=0, human_names=True)
    blob = "".join(p.body_html for b in synthesize_blogs(seed).blogs for p in b.posts)
    blob += "".join(t.content_html for f in synthesize_forums(seed).forums for t in f.topics)

    assert "a quick update from the" not in blob
    assert "couldn't find an answer elsewhere" not in blob


def test_every_blog_post_gets_its_own_cover_image():
    """The cover route keyed its gradient off the FILENAME, and every post
    embeds `cover.png`, so all covers resolved to one shared blob."""
    from connections_export.fakeserver.prototype import cover_svg
    from connections_export.fakeserver.synth import SynthSeed, synthesize_blogs

    blogs = synthesize_blogs(SynthSeed(seed=0, human_names=True)).blogs
    covers = {
        cover_svg(title=post.title, kicker=blog.title, index=i + len(blog.handle))
        for blog in blogs
        for i, post in enumerate(blog.posts)
    }
    total = sum(len(blog.posts) for blog in blogs)

    assert len(covers) == total, f"{total} posts share only {len(covers)} distinct covers"


def test_a_cover_names_the_post_it_belongs_to():
    from connections_export.fakeserver.prototype import cover_svg

    svg = cover_svg(
        title="Postmortem: the Friday outage", kicker="Engineering Blog", index=1
    ).decode()

    assert "Postmortem" in svg
    assert "ENGINEERING BLOG" in svg


def test_a_thread_answers_its_own_question_in_order():
    """Replies came from one generic pool picked by hash, so a thread could
    open with "This worked for me, thanks for asking" before anyone had
    answered anything."""
    from connections_export.fakeserver.synth import SynthSeed, synthesize_forums

    forums = synthesize_forums(SynthSeed(seed=0, human_names=True)).forums
    topic = next(
        t for f in forums for t in f.topics if t.title == "Export is stuck at 90%, any ideas?"
    )
    bodies = [r.content_html for r in topic.replies]

    assert "attachments start" in bodies[0], "the first reply must engage the question"
    assert any("That worked" in b for b in bodies[-2:]), "the thread should reach a resolution"


def test_the_person_reporting_the_fix_is_the_one_who_asked():
    from connections_export.fakeserver.synth import SynthSeed, synthesize_forums

    forums = synthesize_forums(SynthSeed(seed=0, human_names=True)).forums
    topic = next(
        t for f in forums for t in f.topics if t.title == "Export is stuck at 90%, any ideas?"
    )
    resolution = next(r for r in topic.replies if "That worked" in r.content_html)

    assert resolution.author == topic.author


def test_no_op_marker_leaks_into_rendered_reply_text():
    from connections_export.fakeserver.synth import SynthSeed, synthesize_forums

    blob = "".join(
        r.content_html
        for f in synthesize_forums(SynthSeed(seed=0, human_names=True)).forums
        for t in f.topics
        for r in t.replies
    )

    assert "OP|" not in blob


# --- demo content quality: every pool, and comments ------------------------
#
# The tests above check the two pools the demo happens to show. Everything
# below sweeps ALL EIGHT pools of each kind, because an unexamined pool is
# exactly where filler survives. Comments are covered too: one shared
# pool for every wiki page and every blog post is exactly that failure.


def _all_pools_seed():
    """The demo's own per-container shape (`SynthSeed` defaults: 3 posts a
    blog with 3 comments each, 3 topics a forum with 2 + 3 replies, 6 pages
    a wiki) with every pool switched on."""
    return SynthSeed(
        seed=0,
        human_names=True,
        blog_count=8,
        forum_count=8,
        wiki_count=8,
        comments_per_page=5,
    )


def _demo_paragraph_groups():
    """Every visible paragraph the demo content can produce -- bodies,
    comments and replies, from all eight pools of each kind -- grouped by
    where it came from.

    The synthesized wikis and the curated demo wikis are separate groups:
    they share page titles (and therefore comment threads) by design, and
    only one of the two is ever served at a time."""
    from connections_export.fakeserver.prototype import build_prototype_wikiset

    seed = _all_pools_seed()
    groups = {"blogs": [], "forums": [], "synth-wikis": [], "demo-wikis": []}
    for blog in synthesize_blogs(seed).blogs:
        for post in blog.posts:
            groups["blogs"] += _visible_paragraphs(post.body_html)
            groups["blogs"] += _visible_paragraphs("".join(c.content_html for c in post.comments))
    for forum in synthesize_forums(seed).forums:
        for topic in forum.topics:
            groups["forums"] += _visible_paragraphs(topic.content_html)
            groups["forums"] += _visible_paragraphs("".join(r.content_html for r in topic.replies))
    for key, wikiset in (
        ("synth-wikis", synthesize(seed)),
        ("demo-wikis", build_prototype_wikiset()),
    ):
        for wiki in wikiset.wikis:
            for page in wiki.pages:
                groups[key] += _visible_paragraphs("".join(c.content_html for c in page.comments))
    return groups


def _served_together(groups):
    """The two paragraph sets a running demo can actually contain."""
    return (
        groups["blogs"] + groups["forums"] + groups["synth-wikis"],
        groups["blogs"] + groups["forums"] + groups["demo-wikis"],
    )


def test_no_paragraph_repeats_anywhere_in_the_demo_content():
    """Repetition is what makes generated content read as filler: hundreds
    of paragraphs drawn from a handful of distinct lines."""
    import collections

    for paragraphs in _served_together(_demo_paragraph_groups()):
        repeated = {p: n for p, n in collections.Counter(paragraphs).items() if n > 1}
        assert not repeated, f"{len(repeated)} repeated, e.g. {list(repeated)[:3]}"


def test_the_shared_ten_line_comment_pool_is_gone():
    """Wiki pages and blog posts drew comments from one pool of ten lines,
    so a page with no screenshot collected "The screenshot no longer matches
    the current UI" and a post about dark mode collected "Should this live
    under Operations instead?"."""
    blob = "".join(p for group in _demo_paragraph_groups().values() for p in group)

    assert "The screenshot no longer matches the current UI" not in blob
    assert "Should this live under Operations instead?" not in blob
    assert "the fallback procedure changed in CR14" not in blob


def test_every_comment_on_a_page_is_about_that_page():
    """A comment is either part of this page's own written thread or -- on a
    page with more comments than the thread has lines -- names the page it
    is on."""
    import html

    from connections_export.fakeserver.content import _OP_MARK, PAGE_COMMENTS
    from connections_export.fakeserver.prototype import build_prototype_wikiset

    stray = []
    for wiki in build_prototype_wikiset().wikis:
        for page in wiki.pages:
            written = {
                html.escape(line.removeprefix(_OP_MARK))
                for line in PAGE_COMMENTS.get(page.title, ())
            }
            for comment in page.comments:
                body = comment.content_html
                if any(line in body for line in written):
                    continue
                if html.escape(page.title) in body:
                    continue
                stray.append((page.title, body))

    assert not stray, f"comments not about their page: {stray[:3]}"


def test_the_page_author_answers_corrections_on_their_own_page():
    from connections_export.fakeserver.prototype import build_prototype_wikiset

    page = next(
        p for w in build_prototype_wikiset().wikis for p in w.pages if p.title == "Onboarding"
    )
    # "...points at the old ticket queue" -> "Fixed, thank you", by the author.
    assert "old ticket queue" in page.comments[0].content_html
    assert page.comments[0].author != page.author
    assert "Fixed, thank you" in page.comments[1].content_html
    assert page.comments[1].author == page.author


def test_a_blog_comment_reply_is_by_the_author_being_replied_to():
    """The synthesizer threads the second comment under the first. The
    written threads put the post author's answer there, so the one comment
    that IS a reply reads as one."""
    blogs = synthesize_blogs(_all_pools_seed()).blogs
    posts = [p for b in blogs for p in b.posts if len(p.comments) > 1]

    assert len(posts) == 24
    for post in posts:
        reply = post.comments[1]
        assert reply.in_reply_to == post.comments[0].uuid
        assert reply.author == post.author
        assert reply.author_userid == post.author_userid


def test_every_thread_in_every_pool_is_written_end_to_end():
    """At the demo's reply count, no topic falls through to the generic
    pool. A thread that ends on "Following this" answers nothing."""
    from connections_export.fakeserver.content import _GENERIC_REPLIES

    for forum in synthesize_forums(_all_pools_seed()).forums:
        for topic in forum.topics:
            for reply in topic.replies:
                filler = [g for g in _GENERIC_REPLIES if g in reply.content_html]
                assert not filler, (topic.title, filler)


def test_somebody_answers_before_the_asker_speaks_again():
    """Written threads run in order, so the topic's author never gets the
    first reply to their own question."""
    from connections_export.fakeserver.content import topic_reply

    for forum in synthesize_forums(_all_pools_seed()).forums:
        for topic in forum.topics:
            by_op = [topic_reply(topic.title, i)[1] for i in range(len(topic.replies))]
            if True in by_op:
                assert by_op.index(True) > 0, f"{topic.title}: the asker replies to nobody"


def test_only_the_asker_speaks_as_the_asker():
    """Authors are picked by hashing a key against a pool of eight people,
    so a bystander could be dealt the topic author's name and appear to
    answer their own question. Only a reply written as theirs (`OP| `) may
    carry that name."""
    from connections_export.fakeserver.content import topic_reply

    for forum in synthesize_forums(_all_pools_seed()).forums:
        for topic in forum.topics:
            for i, reply in enumerate(topic.replies):
                if not topic_reply(topic.title, i)[1]:
                    assert reply.author != topic.author, (topic.title, i)


def test_only_the_page_or_post_author_speaks_as_the_author():
    from connections_export.fakeserver.content import page_comment, post_comment
    from connections_export.fakeserver.prototype import build_prototype_wikiset

    seed = _all_pools_seed()
    for blog in synthesize_blogs(seed).blogs:
        for post in blog.posts:
            for i, comment in enumerate(post.comments):
                if not post_comment(post.title, i)[1]:
                    assert comment.author != post.author, (post.title, i)
    for wikiset in (synthesize(seed), build_prototype_wikiset()):
        for wiki in wikiset.wikis:
            for page in wiki.pages:
                for i, comment in enumerate(page.comments):
                    if not page_comment(page.title, i)[1]:
                        assert comment.author != page.author, (page.title, i)


def test_no_op_marker_leaks_into_rendered_comment_text():
    from connections_export.fakeserver.prototype import build_prototype_wikiset

    seed = _all_pools_seed()
    blob = "".join(
        c.content_html for b in synthesize_blogs(seed).blogs for p in b.posts for c in p.comments
    )
    for wikiset in (synthesize(seed), build_prototype_wikiset()):
        blob += "".join(c.content_html for w in wikiset.wikis for p in w.pages for c in p.comments)

    assert "OP|" not in blob


def test_comment_fallback_exhausts_every_pair_before_repeating():
    """The busiest demo page carries 41 comments. The fallback pairs an
    opener with a closing clause, and has to walk every pair -- varying both
    halves as it goes -- before any line comes round again."""
    from connections_export.fakeserver import content

    for varied, openers, tails in (
        (content.varied_page_comment, content._PAGE_COMMENT_OPENERS, content._PAGE_COMMENT_TAILS),
        (content.varied_post_comment, content._POST_COMMENT_OPENERS, content._POST_COMMENT_TAILS),
    ):
        pairs = len(openers) * len(tails)
        produced = [varied("Some Page", i) for i in range(pairs)]
        assert len(set(produced)) == pairs
        # Never the same closing clause twice running -- which is what a
        # lap-at-a-time walk produces, and what makes a long thread read as
        # generated.
        for tail in tails[1:]:
            for a, b in zip(produced, produced[1:], strict=False):
                assert not (a.endswith(tail) and b.endswith(tail))


def test_every_pooled_title_has_written_content():
    from connections_export.fakeserver import synth
    from connections_export.fakeserver.content import (
        BLOG_BODIES,
        PAGE_COMMENTS,
        POST_COMMENTS,
        TOPIC_BODIES,
        TOPIC_REPLIES,
    )

    posts = [t for pool in synth._BLOG_POST_TITLES for t in pool]
    topics = [t for pool in synth._FORUM_TOPIC_TITLES for t in pool]
    # The three wiki pools the demo shows: eng-handbook, product-wiki, ops-kb.
    pages = [t for pool in synth._WIKI_PAGE_POOLS[:3] for t in pool]

    assert [t for t in posts if t not in BLOG_BODIES] == []
    assert [t for t in posts if t not in POST_COMMENTS] == []
    assert [t for t in topics if t not in TOPIC_BODIES] == []
    assert [t for t in topics if t not in TOPIC_REPLIES] == []
    assert [t for t in pages if t not in PAGE_COMMENTS] == []


def test_demo_entries_span_a_plausible_stretch_of_time():
    """The seeded clock ticked one hour for everything, so a blog's entire
    history fitted in an afternoon and every entry carried effectively the
    same date -- nothing that orients by time had anything to show."""
    from connections_export.fakeserver.synth import (
        SynthSeed,
        synthesize_blogs,
        synthesize_forums,
    )

    seed = SynthSeed(seed=0, human_names=True)
    for blog in synthesize_blogs(seed).blogs:
        dates = sorted(post.published for post in blog.posts)
        assert dates[0][:10] != dates[-1][:10], f"{blog.title} posts share one day"
    for forum in synthesize_forums(seed).forums:
        dates = sorted(topic.published for topic in forum.topics)
        assert dates[0][:10] != dates[-1][:10], f"{forum.title} topics share one day"


def test_spacing_entries_out_stays_deterministic():
    """Determinism is load-bearing: the same seed must give the same dataset,
    so the spacing is derived from the index, never a random draw."""
    from connections_export.fakeserver.synth import SynthSeed, synthesize_blogs

    a = synthesize_blogs(SynthSeed(seed=0, human_names=True)).blogs[0]
    b = synthesize_blogs(SynthSeed(seed=0, human_names=True)).blogs[0]

    assert [p.published for p in a.posts] == [p.published for p in b.posts]
