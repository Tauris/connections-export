"""The Search API person-query adapter: URL builder + results-feed parse."""

from connections_export.adapters.search import parse_search_results, search_results_url

_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:snx="http://www.ibm.com/xmlns/prod/sn">
  <title>Search results</title>
  <entry>
    <id>urn:uri:1547735</id>
    <title>Onboarding</title>
    <link rel="alternate" type="text/html" href="https://fake/wikis/x/page/onboarding"/>
    <category scheme="http://www.ibm.com/search/content/2010/type" term="search"/>
    <category scheme="http://www.ibm.com/search/content/2010/component" term="wikis"/>
    <author><name>J. Weber</name><snx:userid>jweber</snx:userid></author>
    <contributor><name>A. Okafor</name><snx:userid>aokafor</snx:userid></contributor>
  </entry>
  <entry>
    <id>urn:uri:1547736</id>
    <title>Vision 2026</title>
    <link rel="alternate" type="text/html" href="https://fake/blogs/team/entry/vision"/>
    <category scheme="http://www.ibm.com/search/content/2010/component" term="blogs"/>
    <author><name>A. Okafor</name><snx:userid>aokafor</snx:userid></author>
  </entry>
</feed>"""


def test_search_results_url_person_query():
    url = search_results_url(base_url="https://fake", userid="jweber", scope="wikis:page")
    assert url.startswith("https://fake/search/atom/mysearch/results?")
    # Person filter via the `social` clause (query omitted -- a bare query=* is
    # rejected by the Search API), personUserId + the uuid, scope pinned.
    assert "query=%2A" not in url  # the rejected bare wildcard is gone
    assert "social=" in url and "personUserId" in url and "jweber" in url
    assert "scope=wikis%3Apage" in url
    # anonymous variant
    assert "/search/atom/search/results" in search_results_url(
        base_url="https://fake", userid="jweber", authenticated=False
    )


def test_search_results_url_by_email():
    url = search_results_url(base_url="https://fake", email="me@example.com")
    assert "social=" in url and "personEmail" in url


def test_search_results_url_community_pin_composes_with_person():
    url = search_results_url(base_url="https://fake", userid="jweber", community_uuid="comm-123")
    # Two social clauses AND-combine: the person pin + the community pin.
    assert url.count("social=") == 2
    assert "personUserId" in url and "community" in url and "comm-123" in url


def test_forum_search_is_scoped_separately_from_blog_search():
    forum_url = search_results_url(
        base_url="https://fake", userid="jweber", community_uuid="comm-123", scope="forums:topic"
    )
    blog_url = search_results_url(
        base_url="https://fake", userid="jweber", community_uuid="comm-123", scope="blogs:entry"
    )
    assert "scope=forums%3Atopic" in forum_url
    assert "scope=blogs%3Aentry" in blog_url
    assert "scope=blogs%3Aentry" not in forum_url
    assert "scope=forums%3Atopic" not in blog_url


def test_community_uuid_from_feed():
    from connections_export.adapters.search import community_uuid_from_feed

    feed = (
        b'<feed xmlns="http://www.w3.org/2005/Atom" '
        b'xmlns:snx="http://www.ibm.com/xmlns/prod/sn">'
        b"<entry><snx:communityUuid>b4a83f35-d7e0-4633-8880-eab4c8d78d6d"
        b"</snx:communityUuid></entry></feed>"
    )
    assert community_uuid_from_feed(feed) == "b4a83f35-d7e0-4633-8880-eab4c8d78d6d"
    # A standalone container (no community element) -> None.
    assert community_uuid_from_feed(b'<feed xmlns="http://www.w3.org/2005/Atom"/>') is None


def test_parse_search_results():
    results = parse_search_results(_FEED)
    assert len(results) == 2

    r0 = results[0]
    assert r0.result_id == "urn:uri:1547735"  # a search-result id, not the item uuid
    assert r0.title == "Onboarding" and r0.component == "wikis"
    assert r0.alternate_url == "https://fake/wikis/x/page/onboarding"
    assert r0.author == "J. Weber" and r0.author_userid == "jweber"
    assert r0.contributors == ["A. Okafor"] and r0.contributor_userids == ["aokafor"]

    r1 = results[1]
    assert r1.component == "blogs" and r1.author_userid == "aokafor"
    assert r1.contributors == [] and r1.contributor_userids == []
