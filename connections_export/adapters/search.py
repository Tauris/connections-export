"""The cross-component **Search API** adapter.

We use one facet of it: the person query
`GET /search/atom/mysearch/results?userid=<uid>`, which returns, in one
paginated Atom feed, the items "associated with" a person across wikis, blogs,
forums, etc. That association is a **superset** of authorship (author OR
contributor OR community membership) -- so this adapter parses each result's
`atom:author`/`atom:contributor` (both with `snx:userid`) and its
`rel="alternate"` HTML link, and the exact "authored by X" decision is made
downstream (`compare.author_compare`), keyed on the alternate link.

The result entry `atom:id` is a *search-result* id, NOT the item's Connections
uuid (doc caveat) -- dereference via `alternate_url`.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from connections_export.adapters import atom
from connections_export.adapters.model import _Model


class SearchResult(_Model):
    result_id: str
    title: str | None = None
    #: The app the hit is from (`wikis`, `blogs`, `forums`, …), from the
    #: `.../component` category term.
    component: str | None = None
    #: `link[rel="alternate" type="text/html"]` -- the item's browser URL and
    #: the stable key we join search results to derived items on.
    alternate_url: str | None = None
    #: `link[rel="via"]` -- the "api" link `ibm-connections-search`'s own
    #: entry parser reads (
    #: "Response shape"). When present it's a direct Atom API URL (e.g. a
    #: forum topic's `/forums/atom/topic?topicUuid=...`), a far more
    #: reliable source for a container/topic uuid than parsing the HTML
    #: `alternate_url`, whose UI shape varies by deployment.
    via_url: str | None = None
    author: str | None = None
    author_userid: str | None = None
    contributors: list[str] = []
    contributor_userids: list[str] = []


def _community_marker_from_feed(data: bytes | str, local_name: str) -> str | None:
    """First `local_name` element anywhere in `data`, namespace-agnostic.
    Shared by the uuid and title readers below, which differ only in which
    element they are after."""
    try:
        root = atom.parse_xml(data)
    except Exception:  # noqa: BLE001 - a non-feed / malformed body is just "no community"
        return None
    for el in root.iter():
        tag = el.tag
        if isinstance(tag, str) and tag.rsplit("}", 1)[-1] == local_name:
            if el.text and el.text.strip():
                return el.text.strip()
    return None


def community_title_from_feed(data: bytes | str) -> str | None:
    """Best-effort: the owning community's display name, read from a
    `snx:communityTitle` marker beside `snx:communityUuid`. Lets a community
    be named without fetching a community document, which would otherwise be
    the only way to turn a uuid into something a reader can display.
    EXPERIMENTAL and undocumented, exactly as `community_uuid_from_feed`: the
    element name is not confirmed."""
    return _community_marker_from_feed(data, "communityTitle")


def community_uuid_from_feed(data: bytes | str) -> str | None:
    """Best-effort: the `snx:communityUuid` that a **community-owned**
    container's feed carries (documented as a Search *result* element,
    `Search_result_entry_content_80`; community-hosted content surfaces it).
    Namespace-agnostic local-name scan; `None` for a standalone container.
    EXPERIMENTAL — the exact element on a forum/blog feed is unconfirmed."""
    return _community_marker_from_feed(data, "communityUuid")


def search_results_url(
    *,
    base_url: str,
    userid: str | None = None,
    email: str | None = None,
    community_uuid: str | None = None,
    scope: str | None = None,
    page: int = 1,
    page_size: int = 50,
    authenticated: bool = True,
    newest_first: bool = False,
) -> str:
    """The person-query results feed URL.

    `/search/atom/mysearch/results` (authenticated — adds private content the
    user can see) or `/search/atom/search/results` (anonymous/public).

    The person filter uses the **`social`** clause -- `{"type":"personUserId",
    "id":<uuid>}` (or `personEmail`) -- with **`query` omitted**. This matters:
    the docs state a bare wildcard-only `query=*` is REJECTED, so the previous
    `query=*&userid=` form returned nothing; `social` is the documented
    query-optional person filter. `scope` (e.g. `wikis:page`) pins a component;
    `page`/`pageSize` paginate (max 150).

    `newest_first` adds `sortKey=date`. Search has no `since` parameter, and
    does not need one: with results in date order an update pages newest-first
    and STOPS at the first hit older than the cutoff. That is a different
    mechanism from the Blogs/Forums `since`, and a cheaper one -- it costs
    about two requests per scope when nothing has changed."""
    path = "mysearch" if authenticated else "search"
    params: list[tuple[str, str]] = []
    if userid:
        params.append(("social", json.dumps({"type": "personUserId", "id": userid})))
    elif email:
        params.append(("social", json.dumps({"type": "personEmail", "id": email})))
    # Community pin -- the only documented sub-component scoping. A
    # second `social` clause AND-combines with the person pin, narrowing to a
    # community-owned container. Repeatable `social` is how they compose.
    if community_uuid:
        params.append(("social", json.dumps({"type": "community", "id": community_uuid})))
    params.append(("page", str(page)))
    params.append(("pageSize", str(page_size)))
    if scope:
        params.append(("scope", scope))
    if newest_first:
        params.append(("sortKey", "date"))
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in params)
    return f"{base_url}/search/atom/{path}/results?{query}"


def _component(entry) -> str | None:
    """The hit's app, from the `atom:category scheme=".../component"` term
    (sub-values like `forums:topic` also appear -- keep the whole term)."""
    for term, scheme in atom.categories(entry):
        if term and scheme and scheme.rstrip("/").endswith("component"):
            return term
    return None


def parse_search_results(data: bytes | str) -> list[SearchResult]:
    """Parse a Search API results feed into `SearchResult`s. Tolerant: every
    field but the result id degrades to None/[] on an unexpected shape."""
    root = atom.parse_xml(data, expected_root="feed")
    results: list[SearchResult] = []
    for entry in atom.entries(root):
        result_id = atom.find_text(entry, "atom:id")
        if not result_id:
            continue
        alternate = atom.links_by_rel(entry).get("alternate")
        via = atom.links_by_rel(entry).get("via")
        results.append(
            SearchResult(
                result_id=result_id,
                title=atom.find_text(entry, "atom:title"),
                component=_component(entry),
                alternate_url=(alternate.get("href") if alternate is not None else None),
                via_url=(via.get("href") if via is not None else None),
                author=atom.author_name(entry),
                author_userid=atom.author_userid(entry),
                contributors=atom.contributor_names(entry),
                contributor_userids=atom.contributor_userids(entry),
            )
        )
    return results
