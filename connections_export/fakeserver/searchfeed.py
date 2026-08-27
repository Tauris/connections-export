"""A minimal Search API results feed for the fake server.

Serves the person query (`?userid=<person>`) over the demo `WikiSet`: the wiki
pages that person **authored**, shaped like a real Search results feed (a
`.../component` category, `rel="alternate"` link, `atom:author` with
`snx:userid`). Deliberately narrow — author-indexed, so it does NOT surface
pages the person only *commented* on. That mirrors the live open question
(does the real person filter include commenters/members?
the published API reference) and lets the comparison show a concrete
"missed by search" bucket in the demo.
"""

from xml.etree import ElementTree as ET

from connections_export.fakeserver.atom import (
    ATOM_NS,
    SNX_NS,
    _q,
    _sub,
    _to_bytes,
    page_browser_url,
)
from connections_export.fakeserver.model import WikiSet

_COMPONENT_SCHEME = "http://www.ibm.com/search/content/2010/component"


def person_userid(name: str) -> str:
    """A stable synthetic `snx:userid` for a display name (demo only)."""
    return name.strip().lower().replace(".", "").replace(" ", "")


def _matches(name: str | None, target: str) -> bool:
    if not name:
        return False
    return name.strip().lower() == target or person_userid(name) == target


def search_results_feed(
    wikiset: WikiSet, *, userid: str, base_url: str, auth_root: str = "basic"
) -> bytes:
    """The `/search/atom/mysearch/results?userid=` feed: pages authored by the
    person named/id'd by `userid` (a display name or its synthetic userid)."""
    target = userid.strip().lower()
    feed = ET.Element(_q(ATOM_NS, "feed"))
    _sub(feed, ATOM_NS, "title", "Search results")
    _sub(feed, ATOM_NS, "id", f"{base_url}/search/atom/mysearch/results")

    count = 0
    for wiki in wikiset.wikis:
        for page in wiki.pages:
            if not _matches(page.author, target):
                continue
            count += 1
            entry = _sub(feed, ATOM_NS, "entry")
            _sub(entry, ATOM_NS, "id", f"urn:uri:{count}")
            _sub(entry, ATOM_NS, "title", page.title)
            link = _sub(entry, ATOM_NS, "link")
            link.set("rel", "alternate")
            link.set("type", "text/html")
            link.set(
                "href",
                page_browser_url(
                    base_url=base_url,
                    auth_root=auth_root,
                    wiki_label=wiki.label,
                    page_label=page.label,
                ),
            )
            category = _sub(entry, ATOM_NS, "category")
            category.set("scheme", _COMPONENT_SCHEME)
            category.set("term", "wikis")
            author = _sub(entry, ATOM_NS, "author")
            _sub(author, ATOM_NS, "name", page.author)
            _sub(author, SNX_NS, "userid", person_userid(page.author or ""))
    return _to_bytes(feed)
