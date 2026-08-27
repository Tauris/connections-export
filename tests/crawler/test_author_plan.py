"""Pure keep-set planning for the two-pass author-filtered crawl: a matched
page keeps its ancestors as context; a comment match keeps the whole page; any
matched reply promotes the whole thread. Mirrors derive.author_filter."""

from connections_export.crawler.author_plan import (
    Involvement,
    PageFacts,
    ThreadFacts,
    format_identities,
    plan_author_keep,
)

ME = "J. Weber"
MY_UID = "jweber"
OTHER = "A. Okafor"


def test_matched_page_keeps_its_ancestors_as_context():
    # root(other) -> mid(other) -> leaf(me): keep leaf + ancestors, mid/root context.
    pages = [
        PageFacts(id="root", involvement=Involvement(author=OTHER)),
        PageFacts(id="mid", involvement=Involvement(author=OTHER), ancestor_ids=("root",)),
        PageFacts(id="leaf", involvement=Involvement(author=ME), ancestor_ids=("root", "mid")),
        PageFacts(id="sib", involvement=Involvement(author=OTHER), ancestor_ids=("root",)),
    ]
    keep = plan_author_keep(ME, pages=pages)
    assert keep.page_ids == {"leaf", "mid", "root"}  # sib dropped
    assert keep.context_ids == {"mid", "root"}  # ancestors kept only as context
    assert "leaf" not in keep.context_ids


def test_contributor_match_by_name():
    pages = [
        PageFacts(id="p2", involvement=Involvement(author=OTHER, contributors=(ME,))),
        PageFacts(id="p3", involvement=Involvement(author=OTHER)),
    ]
    assert plan_author_keep(ME, pages=pages).page_ids == {"p2"}


def test_userid_match_when_filtering_by_userid():
    # Filtering by the snx:userid matches entities credited by that userid --
    # a name filter wouldn't (name != userid string), which is correct.
    pages = [
        PageFacts(id="p1", involvement=Involvement(author=OTHER, author_userid=MY_UID)),
        PageFacts(id="p3", involvement=Involvement(author=OTHER, author_userid="other")),
    ]
    assert plan_author_keep(MY_UID, pages=pages).page_ids == {"p1"}


def test_comment_match_keeps_the_whole_page():
    pages = [
        PageFacts(
            id="p",
            involvement=Involvement(author=OTHER),
            comments=(Involvement(author=OTHER), Involvement(author=ME)),
        )
    ]
    keep = plan_author_keep(ME, pages=pages)
    assert keep.keeps_page("p")  # matched via a comment


def test_single_matched_reply_promotes_the_whole_thread():
    # The "fill back later" case: the author only replies deep in the thread,
    # but we keep the ENTIRE thread (topic + every reply) for context.
    threads = [
        ThreadFacts(
            topic_id="t1",
            topic=Involvement(author=OTHER),
            replies=(Involvement(author=OTHER), Involvement(author=ME), Involvement(author=OTHER)),
        ),
        ThreadFacts(topic_id="t2", topic=Involvement(author=OTHER), replies=()),
    ]
    keep = plan_author_keep(ME, threads=threads)
    assert keep.thread_topic_ids == {"t1"}  # t2 (no involvement) dropped
    assert keep.keeps_thread("t1")


def test_post_match_by_author():
    posts = [
        PageFacts(id="b1", involvement=Involvement(author=ME)),
        PageFacts(id="b2", involvement=Involvement(author=OTHER)),
    ]
    keep = plan_author_keep(ME, posts=posts)
    assert keep.post_ids == {"b1"}


def test_nothing_matched_is_empty():
    keep = plan_author_keep(
        ME,
        pages=[PageFacts(id="p", involvement=Involvement(author=OTHER))],
        threads=[ThreadFacts(topic_id="t", topic=Involvement(author=OTHER))],
    )
    assert not keep.page_ids and not keep.thread_topic_ids


def test_empty_author_keeps_everything():
    # A blank filter is a no-op: Pass 2 fetches all assets, as an unfiltered crawl.
    pages = [PageFacts(id="p1"), PageFacts(id="p2")]
    threads = [ThreadFacts(topic_id="t1")]
    keep = plan_author_keep("   ", pages=pages, threads=threads)
    assert keep.page_ids == {"p1", "p2"}
    assert keep.thread_topic_ids == {"t1"}


def test_matching_is_case_insensitive():
    pages = [PageFacts(id="p", involvement=Involvement(author="J. WEBER"))]
    assert plan_author_keep("j. weber", pages=pages).keeps_page("p")


def test_format_identities_shows_name_and_userid_distinct():
    # The "matched nothing -- here's what's actually stored" diagnostic.
    invs = [
        Involvement(author="Jane Doe", author_userid="jdoe"),
        Involvement(author="Jane Doe", author_userid="jdoe"),  # dup collapses
        Involvement(author=None, author_userid="99999"),  # userid-only
        Involvement(author="A. Okafor"),  # name-only
    ]
    out = format_identities(invs)
    assert "Jane Doe (uid: jdoe)" in out
    assert "(uid: 99999)" in out
    assert "A. Okafor" in out
    assert len(out) == 3  # the duplicate was collapsed


def test_format_identities_caps_the_list():
    invs = [Involvement(author=f"Person {i}") for i in range(50)]
    assert len(format_identities(invs, limit=10)) == 10
