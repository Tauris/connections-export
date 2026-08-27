"""End to end over the demo: the fake Search feed → search adapter → the
comparison, joined against the naive-filtered derived model on `alternate_url`.
Exercises the whole search-checker chain deterministically, no HTTP.
"""

from connections_export.adapters.search import parse_search_results
from connections_export.compare.author_compare import compare_author
from connections_export.fakeserver.prototype import build_prototype_wikiset
from connections_export.fakeserver.searchfeed import search_results_feed
from connections_export.gui.demo import run_demo

PERSON = "A. Okafor"  # a demo author (fakeserver/prototype _AUTHORS[0])


def test_search_vs_naive_over_the_demo(tmp_path):
    # The derived model (naive side). Pages now carry alternate_url (the join key).
    result = run_demo((lambda _e: None), archive_dir=tmp_path / "a", delay=0)
    interchange = result.interchange

    # The Search side: the fake person query, over the same deterministic dataset,
    # with the base_url/auth_root the demo crawl used (so alternate_urls match).
    feed = search_results_feed(
        build_prototype_wikiset(), userid=PERSON, base_url="https://fake", auth_root="basic"
    )
    results = parse_search_results(feed)

    cmp = compare_author(interchange, results, author=PERSON)

    # Search here is author-indexed: everything it returns is authored by the
    # person, so it's all agreed, with no extras and no noise.
    assert cmp.search_authored == len(results) > 0
    assert cmp.extra_from_search == [] and cmp.superset_noise == []
    assert len(cmp.agreed) == cmp.search_authored

    # The naive scan keeps at least as much (authored + anything the person only
    # commented on); those comment-only pages are exactly what Search missed.
    assert cmp.naive_count >= cmp.search_authored
    assert len(cmp.missed_by_search) == cmp.naive_count - cmp.search_authored
    assert cmp.search_agrees == (cmp.naive_count == cmp.search_authored)
    # the join really matched (a base_url/auth_root drift would zero this out)
    assert cmp.agreed
