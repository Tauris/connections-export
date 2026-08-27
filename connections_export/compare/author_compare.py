"""Compare the **naive scan** and the **Search person query** for one author.

The naive filter (`derive.author_filter`) is authoritative: it keeps every item
the user authored or participated in, from a complete crawl. The Search API's
person query is a cheaper *superset* (author OR contributor OR community
membership) that we don't yet trust. This module diffs the two so a human can
decide whether Search can later be trusted to drive collection.

- **N** — items the naive filter keeps (page/post/topic; context-only ancestors
  excluded — they're path, not authorship).
- **S** — every `SearchResult` the person query returned (the superset).
- **S'** — S filtered to an exact item-level author/contributor match.

Both are keyed on `alternate_url` (the item's browser URL). The diff:
`agreed = N ∩ S'`, `missed_by_search = N − S'` (the trust-breakers),
`extra_from_search = S' − N`, `superset_noise = S − S'`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from connections_export.adapters.search import SearchResult
from connections_export.derive.author_filter import filter_by_author
from connections_export.derive.model import Interchange
from connections_export.identity import norm_identity as _norm


def _result_involves(target: str, result: SearchResult) -> bool:
    identities = {_norm(result.author), _norm(result.author_userid)}
    identities.update(_norm(x) for x in result.contributors)
    identities.update(_norm(x) for x in result.contributor_userids)
    return target in (identities - {None})


@dataclass
class Comparison:
    author: str
    naive_count: int
    search_returned: int
    search_authored: int
    #: item labels (title / url) per bucket, keyed for a human report
    agreed: list[str] = field(default_factory=list)
    missed_by_search: list[str] = field(default_factory=list)
    extra_from_search: list[str] = field(default_factory=list)
    superset_noise: list[str] = field(default_factory=list)

    @property
    def search_agrees(self) -> bool:
        """True when Search returned exactly the authored set (no misses, no
        extras) -- the condition under which it could be trusted to drive
        collection. Noise (superset) does not break agreement."""
        return not self.missed_by_search and not self.extra_from_search


def _url_key(url: str | None) -> str | None:
    if not url:
        return None
    split = urlsplit(url)
    query = parse_qs(split.query)
    for name in ("topicUuid", "topicId"):
        value = (query.get(name) or [None])[0]
        if value:
            return f"topic:{value}"
    if "threadTopic" in split.path:
        value = (query.get("id") or [None])[0]
        if value:
            return f"topic:{value}"
    return url


def _item_key(item) -> str:
    """Join key: the item's browser URL when present, else its id (which won't
    match a search result but keeps the item visible in N)."""
    if item.alternate_url:
        return _url_key(item.alternate_url) or f"id:{item.id}"
    if item.__class__.__name__ == "DerivedForumTopic":
        return f"topic:{item.id}"
    return f"id:{item.id}"


def compare_author(
    interchange: Interchange, results: list[SearchResult], *, author: str
) -> Comparison:
    """Diff the naive author filter over `interchange` against the Search
    `results` for the same `author`. `interchange` is the full (unfiltered)
    derived model; the naive set is computed here with `filter_by_author`."""
    target = _norm(author)
    filtered = filter_by_author(interchange, author=author) if target else interchange

    # N: authored items (skip context-only ancestor pages).
    naive: dict[str, str] = {}
    for wiki in filtered.wikis:
        for page in wiki.pages.values():
            if not page.is_context:
                naive[_item_key(page)] = page.title or page.label or page.id
    for blog in filtered.blogs:
        for post in blog.posts.values():
            naive[_item_key(post)] = post.title or post.id
    for forum in filtered.forums:
        for topic in forum.topics.values():
            naive[_item_key(topic)] = topic.title or topic.id

    # S (everything returned) and S' (the exact-authored subset).
    s_all: dict[str, str] = {}
    s_prime: dict[str, str] = {}
    for result in results:
        label = result.title or result.alternate_url or result.result_id
        key = (
            _url_key(result.alternate_url)
            or _url_key(result.via_url)
            or f"result:{result.result_id}"
        )
        s_all[key] = label
        if target and _result_involves(target, result):
            s_prime[key] = label

    n_keys, sp_keys, sa_keys = set(naive), set(s_prime), set(s_all)
    return Comparison(
        author=author,
        naive_count=len(naive),
        search_returned=len(results),
        search_authored=len(s_prime),
        agreed=sorted(naive[k] for k in (n_keys & sp_keys)),
        # search surfaced NOTHING for these -- the true false negatives
        missed_by_search=sorted(naive[k] for k in (n_keys - sa_keys)),
        extra_from_search=sorted(s_prime[k] for k in (sp_keys - n_keys)),
        # returned, but not attributable to the person's authorship
        superset_noise=sorted(s_all[k] for k in (sa_keys - sp_keys)),
    )
