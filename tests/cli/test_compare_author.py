"""`connections-export compare-author --demo`: the search-vs-naive trust check."""

from connections_export.adapters.search import SearchResult
from connections_export.cli import _filter_search_results_to_topics, compare_author_main


def test_compare_author_demo_reports_disagreement(capsys):
    rc = compare_author_main(["--author", "A. Okafor", "--demo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Author-filter comparison for: A. Okafor" in out
    assert "naive scan" in out and "search authored" in out
    assert "MISSED by search" in out
    # the demo's Search is author-indexed, so it misses comment-only involvement
    assert "DISAGREES" in out


def test_compare_author_requires_demo():
    assert compare_author_main(["--author", "someone"]) == 2


def test_forum_comparison_filters_community_search_to_crawled_topic_ids():
    results = [
        SearchResult(result_id="selected", via_url="https://fake/forums/atom/topic?topicUuid=t1"),
        SearchResult(
            result_id="selected-browser-url",
            alternate_url="https://fake/forums/html/threadTopic?id=t2",
        ),
        SearchResult(
            result_id="other-forum", via_url="https://fake/forums/atom/topic?topicUuid=t3"
        ),
        SearchResult(result_id="unidentified"),
    ]

    selected = _filter_search_results_to_topics(results, {"t1", "t2"})

    assert [result.result_id for result in selected] == ["selected", "selected-browser-url"]
