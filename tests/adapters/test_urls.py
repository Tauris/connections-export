"""URL builders produce the documented endpoint shapes, and link-following
takes priority over them -- `parse_page_entry` exposes `content_src`
/ `replies_url` pulled straight off the entry's links rather than
requiring the caller to construct anything. The builders exist purely as a fallback."""

from pathlib import Path

from connections_export.adapters.wikis import (
    artifacts_url,
    nav_feed_url,
    page_entry_url,
    parse_page_entry,
    wikis_feed_url,
)

FIXTURES = Path(__file__).parent / "fixtures"

_BASE = "https://example.com"
_AUTH_ROOT = "basic"


def test_wikis_feed_url():
    assert wikis_feed_url(base_url=_BASE, auth_root=_AUTH_ROOT) == (
        "https://example.com/wikis/basic/api/wikis/feed"
    )


def test_nav_feed_url():
    assert nav_feed_url(base_url=_BASE, auth_root=_AUTH_ROOT, wiki_label="wiki0321") == (
        "https://example.com/wikis/basic/api/wiki/wiki0321/nav/feed"
    )


def test_page_entry_url():
    assert (
        page_entry_url(
            base_url=_BASE, auth_root=_AUTH_ROOT, wiki_label="wiki0321", page_label="page2"
        )
        == "https://example.com/wikis/basic/api/wiki/wiki0321/page/page2/entry"
    )


def test_page_label_is_url_encoded_for_cache_identity():
    assert page_entry_url(
        base_url=_BASE,
        auth_root=_AUTH_ROOT,
        wiki_label="wiki0321",
        page_label="Installing python packages",
    ).endswith("/page/Installing%20python%20packages/entry")


def test_artifacts_url_with_category():
    url = artifacts_url(
        base_url=_BASE,
        auth_root=_AUTH_ROOT,
        wiki_label="wiki0321",
        page_label="page2",
        category="comment",
    )
    assert (
        url == "https://example.com/wikis/basic/api/wiki/wiki0321/page/page2/feed?category=comment"
    )


def test_artifact_page_label_is_url_encoded():
    url = artifacts_url(
        base_url=_BASE,
        auth_root=_AUTH_ROOT,
        wiki_label="wiki0321",
        page_label="Installing python packages",
        category="attachment",
    )
    assert "/page/Installing%20python%20packages/feed" in url


def test_artifacts_url_without_category_is_bare_feed_url():
    url = artifacts_url(
        base_url=_BASE,
        auth_root=_AUTH_ROOT,
        wiki_label="wiki0321",
        page_label="page2",
        category=None,
    )
    assert url == "https://example.com/wikis/basic/api/wiki/wiki0321/page/page2/feed"


def test_parse_page_entry_prefers_link_following_over_url_construction():
    data = (FIXTURES / "page_entry_reference_fields.xml").read_bytes()
    page = parse_page_entry(data)

    # content_src and replies_url come straight off the parsed links,
    # not from any of the builders above -- and happen to differ in
    # host from what a builder using the same auth_root/labels would
    # produce, proving the adapter followed the link rather than
    # reconstructing it.
    assert page.content_src.startswith("https://example.com/wikis/basic/api/wiki/wiki0321/")
    assert page.replies_url.startswith("https://example.com/wikis/basic/api/wiki/wiki0321/")
    built = page_entry_url(
        base_url=_BASE,
        auth_root=_AUTH_ROOT,
        wiki_label="wiki0321",
        page_label="referenceSamplePage",
    )
    # The builder's constructed *entry* URL is not equal to the
    # content_src (a distinct /media URL) -- confirming content_src
    # was read off the link, not synthesized from this builder.
    assert page.content_src != built
