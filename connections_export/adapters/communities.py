"""A community's own document: what can be read out of it.

Today that is one thing -- the community's picture -- and it is here rather
than inline in the crawler because where the picture comes from is not
something the API describes.

There is no documented endpoint for a community logo, so rather than
hard-code a URL shape, this reads the community document for a link that
*looks like* an image and follows it. The document is already fetched during
discovery, so a picture named there costs nothing extra to find, and a
deployment that names it differently still resolves.

Keeping that reading here, rather than inline in the crawler, means a
deployment whose document says it another way is one function to change.
"""

from __future__ import annotations

from dataclasses import dataclass

from connections_export.adapters import atom

#: `rel` values that name a picture even when the `type` does not. Some
#: deployments serve the logo as `application/octet-stream` and say what it is
#: in the relation instead.
_IMAGE_RELS = ("logo", "image", "thumbnail", "avatar", "photo")


def logo_href(data: bytes | str | None) -> str | None:
    """The community's picture, as a raw href, or None.

    None for a community that has none, and for a document that will not parse
    -- discovery runs against a live deployment where any request may come back
    as a login page, and a missing picture must never fail the capture around
    it.

    The first match wins, so two runs against the same deployment archive the
    same bytes.
    """
    if not data:
        return None
    try:
        root = atom.parse_xml(data)
    except Exception:  # noqa: BLE001 - an unreadable document has no picture in it
        return None

    for link in root.xpath("//*[local-name()='link'][@href]"):
        href = (link.get("href") or "").strip()
        if not href:
            continue
        content_type = (link.get("type") or "").lower()
        rel = (link.get("rel") or "").lower()
        # A typed image is unambiguous. `rel="alternate"` on its own is the
        # community's HTML page, and fetching that as a picture would put a web
        # page in the header.
        if content_type.startswith("image/"):
            return href
        if any(word in rel for word in _IMAGE_RELS):
            return href
    return None


@dataclass(frozen=True)
class SubCommunityRef:
    """One child community, as its parent's feed names it.

    A child is an ordinary community: it has its own wikis, blogs, forums,
    files and highlights, and component discovery treats it like any other. The parent/child edge is
    how a person recognises the set they meant -- it is not a containment
    rule, and nothing downstream should treat it as one.
    """

    uuid: str
    title: str | None = None
    updated: str | None = None


def subcommunities_url(*, base_url: str, community_uuid: str) -> str:
    """The feed naming a community's children.

    Verified against a real deployment: returns
    `200 application/atom+xml`, entries carrying title, community UUID and
    updated timestamp.
    """
    return (
        f"{base_url.rstrip('/')}/communities/service/atom/community/subcommunities"
        f"?communityUuid={community_uuid}"
    )


def parse_subcommunities(data: bytes | str | None) -> list[SubCommunityRef]:
    """Children named by a `subcommunities` feed.

    An empty list is a normal answer three times over: a community with no
    children, a deployment that does not serve the feed, and a request that
    came back as something other than a feed. None of those is a failure --
    a community can always be added by URL, which is the only way to add one
    that is merely *related* rather than a child.
    """
    if not data:
        return []
    try:
        root = atom.parse_xml(data)
    except Exception:  # noqa: BLE001 - an unreadable document names no children
        return []
    children: list[SubCommunityRef] = []
    for entry in atom.entries(root):
        uuid = atom.find_text(entry, "snx:communityUuid")
        if not uuid:
            # Without an id there is nothing to crawl, and a half-known child
            # in the picker is worse than an absent one.
            continue
        children.append(
            SubCommunityRef(
                uuid=uuid,
                title=atom.find_text(entry, "atom:title"),
                updated=atom.find_text(entry, "atom:updated"),
            )
        )
    return children
