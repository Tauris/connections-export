"""A minimal Search API results feed for the fake server.

Serves the person query over the demo data, shaped like a real Search results
feed (a `.../component` category, `rel="alternate"` link, `atom:author` with
`snx:userid`).

**Wikis** (`scope=wikis:page`, or no scope) are deliberately narrow —
author-indexed, so a page the person only *commented* on is NOT returned. That
mirrors an open question about the real person filter and lets the demo's
comparison show a concrete "missed by search" bucket.

**Forums** (`scope=forums:topic`) are the opposite, and deliberately so. A
topic comes back if the person wrote it OR replied anywhere in it, once per
topic however many replies they left, and the entry links to the topic —
never to a reply. That is the shape an author-filtered community capture
depends on: Search says which threads to read, and the crawl reads each one
whole. A fake that returned reply URLs, or one hit per contribution, would
let a client that mishandles either look correct here.
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
from connections_export.fakeserver.model import WikiSet, person_uid

_COMPONENT_SCHEME = "http://www.ibm.com/search/content/2010/component"


def person_userid(name: str) -> str:
    """A stable synthetic `snx:userid` for a display name (demo only)."""
    return name.strip().lower().replace(".", "").replace(" ", "")


def _matches(name: str | None, target: str) -> bool:
    """Does `target` name this person?

    A display name, the legacy demo slug, or -- the one that matters -- the
    directory GUID the profile service hands back for them. A real console
    asks the deployment who it is signed in as and gets that GUID, then puts
    it in the person query; a fake that only knew names would answer nothing
    for the very value the product actually sends, and the demo would
    silently take the fallback path instead of the one being demonstrated.
    """
    if not name:
        return False
    return (
        name.strip().lower() == target
        or person_userid(name) == target
        or person_uid(name) == target
    )


def search_results_feed(
    wikiset: WikiSet,
    *,
    userid: str,
    base_url: str,
    auth_root: str = "basic",
    scope: str | None = None,
    forumset=None,
    community_uuid: str | None = None,
) -> bytes:
    """The `/search/atom/mysearch/results` person feed.

    With `scope=forums:...` and a `forumset`, the forum topics the person is
    in (see the module docstring); otherwise the wiki pages they authored.
    `community_uuid` pins the answer to one community, which is the only
    sub-component scoping the real API documents -- and the pin a capture
    relies on to keep the query small.
    """
    if scope and scope.startswith("forums") and forumset is not None:
        return _forum_topic_results(
            forumset,
            userid=userid,
            base_url=base_url,
            community_uuid=community_uuid,
        )
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


def _forum_topic_results(
    forumset, *, userid: str, base_url: str, community_uuid: str | None
) -> bytes:
    """Topic hits for the person query, one entry per TOPIC.

    A person with forty replies in one thread gets that thread once, and a
    person who only ever replied still gets it -- both things a client can
    get wrong invisibly. The entry's
    `via` link is the topic's own Atom URL, which is what the real feed
    carries and what the selector prefers to read the id from.
    """
    target = userid.strip().lower()
    feed = ET.Element(_q(ATOM_NS, "feed"))
    _sub(feed, ATOM_NS, "title", "Search results")
    _sub(feed, ATOM_NS, "id", f"{base_url}/search/atom/mysearch/results")

    count = 0
    for forum in forumset.forums:
        # The community pin, applied server-side exactly as the real API does
        # -- a capture that reads it as advisory would fetch the deployment.
        if community_uuid and forum.community_uuid != community_uuid:
            continue
        for topic in forum.topics:
            involved = _matches(topic.author, target) or any(
                _matches(reply.author, target) for reply in topic.replies
            )
            if not involved:
                continue
            count += 1
            entry = _sub(feed, ATOM_NS, "entry")
            _sub(entry, ATOM_NS, "id", f"urn:uri:forum:{topic.uuid}")
            _sub(entry, ATOM_NS, "title", topic.title)
            alternate = _sub(entry, ATOM_NS, "link")
            alternate.set("rel", "alternate")
            alternate.set("type", "text/html")
            alternate.set("href", f"{base_url}/forums/html/topic?id={topic.uuid}")
            via = _sub(entry, ATOM_NS, "link")
            via.set("rel", "via")
            via.set("type", "application/atom+xml")
            via.set("href", f"{base_url}/forums/atom/topic?topicUuid={topic.uuid}")
            category = _sub(entry, ATOM_NS, "category")
            category.set("scheme", _COMPONENT_SCHEME)
            category.set("term", "forums")
            author = _sub(entry, ATOM_NS, "author")
            _sub(author, ATOM_NS, "name", topic.author)
            _sub(author, SNX_NS, "userid", person_userid(topic.author or ""))
    return _to_bytes(feed)
