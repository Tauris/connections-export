"""`parse_wiki_url` -- identify the HCL deployment + wiki
from a dropped/pasted wiki page URL.

A pure function, table-driven: the Atom API shape (basic /
basic-anonymous / oauth auth roots), the human-UI hash-route shape,
the bare `/wiki/{label}` shape, and unrecognised URLs -> `ok=False`
with a reason, never a wrong guess (An unrecognised URL is
reported, not guessed). Placeholder hosts only (`example.corp`,
`example.com`, `fake`) so the hygiene guard (tests/test_hygiene.py)
stays green.
"""

from __future__ import annotations

import dataclasses

import pytest

from connections_export.gui.wiki_url import ParsedTarget, parse_url, parse_wiki_url

# --- table-driven: recognised shapes ---------------------------------------

RECOGNISED = [
    pytest.param(
        "https://example.corp/wikis/basic/api/wiki/eng-handbook/page/Onboarding/entry",
        "https://example.corp",
        "eng-handbook",
        "basic",
        id="api-basic",
    ),
    pytest.param(
        "https://example.corp/wikis/basic/anonymous/api/wiki/eng-handbook/nav/feed",
        "https://example.corp",
        "eng-handbook",
        "basic/anonymous",
        id="api-basic-anonymous",
    ),
    pytest.param(
        "https://example.corp/wikis/oauth/api/wiki/eng-handbook/page/X/entry",
        "https://example.corp",
        "eng-handbook",
        "oauth",
        id="api-oauth",
    ),
    pytest.param(
        "https://example.corp/wikis/form/api/wiki/eng-handbook/nav/feed?tree=true",
        "https://example.corp",
        "eng-handbook",
        "form",
        id="api-form",
    ),
    pytest.param(
        "https://example.corp:9443/wikis/basic/api/wiki/eng-handbook/nav/feed",
        "https://example.corp:9443",
        "eng-handbook",
        "basic",
        id="api-with-port",
    ),
    pytest.param(
        "https://example.corp/connections/wikis/basic/api/wiki/eng-handbook/nav/feed",
        "https://example.corp/connections",
        "eng-handbook",
        "basic",
        id="api-with-context-root",
    ),
    pytest.param(
        "https://example.corp/wikis/home?lang=en_us#/wiki/eng-handbook/page/Onboarding",
        "https://example.corp",
        "eng-handbook",
        None,
        id="human-ui-hash-route",
    ),
    pytest.param(
        "https://example.corp/wikis/home/wiki/eng-handbook",
        "https://example.corp",
        "eng-handbook",
        None,
        id="bare-wiki-label",
    ),
]


def test_community_start_url_identifies_the_community():
    result = parse_url(
        "https://example.corp/communities/service/html/communitystart?communityUuid=comm-123"
    )
    assert result.ok is True
    assert result.app == "community"
    assert result.base_url == "https://example.corp"
    assert result.community_uuid == "comm-123"


def test_community_view_url_identifies_the_community():
    result = parse_url(
        "https://example.corp/communities/service/html/communityview"
        "?communityUuid=38d93db1-f34b-4ed4-83ae-dfcc5489fcce"
    )
    assert result.ok is True
    assert result.app == "community"
    assert result.base_url == "https://example.corp"
    assert result.community_uuid == "38d93db1-f34b-4ed4-83ae-dfcc5489fcce"


@pytest.mark.parametrize("url,base_url,wiki_label,auth_root", RECOGNISED)
def test_recognised_shapes_are_identified(url, base_url, wiki_label, auth_root):
    result = parse_wiki_url(url)

    assert result.ok is True
    assert result.base_url == base_url
    assert result.wiki_label == wiki_label
    assert result.auth_root == auth_root
    assert result.reason is None


# --- table-driven: unrecognised -> ok=False, never a wrong guess -----------

UNRECOGNISED = [
    pytest.param("https://example.com/some/other/page", id="unrelated-path"),
    pytest.param("https://example.corp/wikis/home", id="no-wiki-label-anywhere"),
    pytest.param(
        "https://example.corp/wikis/kerberos/api/wiki/eng-handbook/nav/feed",
        id="unknown-auth-root",
    ),
    pytest.param("not a url at all", id="not-a-url"),
    pytest.param("ftp://example.com/wikis/basic/api/wiki/eng-handbook/nav/feed", id="wrong-scheme"),
    pytest.param("https://example.corp/wikis/basic/api/wiki/", id="trailing-empty-label"),
]


@pytest.mark.parametrize("url", UNRECOGNISED)
def test_unrecognised_urls_are_reported_not_guessed(url):
    result = parse_wiki_url(url)

    assert result.ok is False
    assert result.base_url is None
    assert result.wiki_label is None
    assert result.reason  # a non-empty, human-readable reason is always given


# --- ParsedTarget's own shape ------------------------------------------------


def test_parsed_target_is_a_frozen_dataclass_with_the_documented_fields():
    target = ParsedTarget(
        base_url="https://example.corp", wiki_label="eng-handbook", auth_root="basic", ok=True
    )
    assert target.base_url == "https://example.corp"
    assert target.wiki_label == "eng-handbook"
    assert target.auth_root == "basic"
    assert target.ok is True
    assert target.reason is None

    with pytest.raises(dataclasses.FrozenInstanceError):
        target.ok = False  # frozen -- never mutated after construction


def test_page_url_extracts_the_page_label_for_targeted_capture():
    api = parse_url("https://example.corp/wikis/basic/api/wiki/eng-handbook/page/Onboarding/entry")
    assert api.app == "wiki" and api.wiki_label == "eng-handbook"
    assert api.page_label == "Onboarding" and api.scope == "single"

    human = parse_url("https://example.corp/wikis/home?lang=en#/wiki/eng-handbook/page/Onboarding")
    assert human.page_label == "Onboarding" and human.scope == "single"


def test_wiki_level_url_has_no_page_label():
    t = parse_url("https://example.corp/wikis/basic/api/wiki/eng-handbook/feed")
    assert t.app == "wiki" and t.page_label is None and t.scope == "all"


def test_parse_wiki_url_is_pure_and_deterministic():
    url = "https://example.corp/wikis/basic/api/wiki/eng-handbook/page/Onboarding/entry"
    assert parse_wiki_url(url) == parse_wiki_url(url)
