"""`compare_author`: diff the naive author filter against Search person-query
results into agreed / missed / extra / noise buckets."""

from connections_export.adapters.search import SearchResult
from connections_export.compare.author_compare import compare_author
from connections_export.derive.model import (
    DerivedComment,
    DerivedForum,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
)

ME = "J. Weber"
OTHER = "A. Okafor"


def _interchange() -> Interchange:
    # authored by me
    onboarding = DerivedPage(id="p1", title="Onboarding", author=ME, alternate_url="u1")
    # authored by someone else, but I commented -> naive keeps it; search can't attribute it
    commented = DerivedPage(
        id="p2",
        title="Commented",
        author=OTHER,
        alternate_url="u2",
        comments=[DerivedComment(id="c", author=ME)],
    )
    # nothing to do with me -> dropped by the naive filter
    theirs = DerivedPage(id="p3", title="Theirs", author=OTHER, alternate_url="u3")
    wiki = DerivedWiki(
        id="w",
        label="w",
        title="W",
        root_page_ids=["p1", "p2", "p3"],
        pages={"p1": onboarding, "p2": commented, "p3": theirs},
    )
    return Interchange(wikis=[wiki])


def _results() -> list[SearchResult]:
    return [
        SearchResult(result_id="r1", title="Onboarding", alternate_url="u1", author=ME),  # agreed
        SearchResult(result_id="r2", title="Extra Post", alternate_url="u9", author=ME),  # extra
        SearchResult(  # noise: returned for me but I'm only a contributor/member
            result_id="r3",
            title="Someone Elses",
            alternate_url="u8",
            author=OTHER,
            contributors=["S. Nakamura"],
        ),
    ]


def test_comparison_buckets():
    cmp = compare_author(_interchange(), _results(), author=ME)

    assert cmp.naive_count == 2  # Onboarding + Commented (theirs dropped)
    assert cmp.search_returned == 3
    assert cmp.search_authored == 2  # Onboarding + Extra Post (noise excluded)

    assert cmp.agreed == ["Onboarding"]
    # I only commented on "Commented" -> search never returned it: a trust-breaker
    assert cmp.missed_by_search == ["Commented"]
    assert cmp.extra_from_search == ["Extra Post"]
    assert cmp.superset_noise == ["Someone Elses"]
    assert cmp.search_agrees is False


def test_search_agrees_when_sets_match():
    onboarding = DerivedPage(id="p1", title="Onboarding", author=ME, alternate_url="u1")
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=["p1"], pages={"p1": onboarding})
    results = [SearchResult(result_id="r1", title="Onboarding", alternate_url="u1", author=ME)]
    cmp = compare_author(Interchange(wikis=[wiki]), results, author=ME)
    assert cmp.search_agrees is True
    assert cmp.agreed == ["Onboarding"] and not cmp.missed_by_search


def test_forum_via_topic_uuid_joins_archive_topic():
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=[], pages={})
    forum = Interchange(wikis=[wiki])
    from connections_export.derive.model import DerivedForum, DerivedForumTopic

    forum.forums = [
        DerivedForum(
            id="forum-1",
            topics={"topic-1": DerivedForumTopic(id="topic-1", title="Forum topic", author=ME)},
            topic_ids=["topic-1"],
        )
    ]
    result = SearchResult(
        result_id="r1",
        title="Forum topic",
        via_url="https://fake/forums/atom/topic?topicUuid=topic-1",
        author=ME,
    )
    comparison = compare_author(forum, [result], author=ME)
    assert comparison.agreed == ["Forum topic"]
    assert comparison.search_agrees


def test_human_forum_thread_url_joins_api_topic_uuid():
    forum = Interchange(
        forums=[
            DerivedForum(
                id="forum-1",
                topic_ids=["topic-1"],
                topics={
                    "topic-1": DerivedForumTopic(
                        id="topic-1",
                        title="Forum topic",
                        author=ME,
                        alternate_url="https://fake/forums/html/threadTopic?id=topic-1",
                    )
                },
            )
        ]
    )
    result = SearchResult(
        result_id="r1",
        title="Forum topic",
        via_url="https://fake/forums/atom/topic?topicUuid=topic-1",
        author=ME,
    )
    comparison = compare_author(forum, [result], author=ME)
    assert comparison.search_agrees
