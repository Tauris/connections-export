"""Assembling the community grouping layer.

A community holds no copies of content -- it records which containers belong to
it, and the ids point into the other lists.
"""

from __future__ import annotations

from collections.abc import Iterable

from connections_export.derive.assets import resolve_asset
from connections_export.derive.index import ArchiveIndex
from connections_export.derive.model import (
    DerivedBlog,
    DerivedCommunity,
    DerivedForum,
    DerivedWiki,
    ResolvedAsset,
)


class DeriveError(Exception):
    """Derivation could not even locate its starting point in the
    archive -- neither a `.../wikis/feed?ps=..&page=1` request nor any
    `.../wiki/{label}/nav/feed` was ever archived. Distinct from a
    per-page/per-artifact gap, which is always represented via
    `Provenance.note`, never raised."""


def _community_logo(
    index: ArchiveIndex, *, community_uuid: str, base_url: str, hcl_hosts: Iterable[str]
) -> ResolvedAsset | None:
    """A community's captured picture, if the archive holds one.

    Read back through the same link the crawl followed, so the two sides
    cannot disagree about which image belongs to which community.
    """
    from connections_export.adapters.communities import logo_href  # noqa: PLC0415

    instance_url = (
        f"{base_url}/communities/service/atom/community/instance?communityUuid={community_uuid}"
    )
    document = index.get(instance_url)
    href = logo_href(document.content) if document is not None else None
    if not href:
        return None
    return resolve_asset(
        href,
        join_base=base_url,
        boundary_base=base_url,
        hcl_hosts=hcl_hosts,
        index=index,
    )


def _assemble_communities(
    wikis: list[DerivedWiki],
    blogs: list[DerivedBlog],
    forums: list[DerivedForum],
) -> list[DerivedCommunity]:
    """Group derived containers by the community marker in their feeds.

    A community is only ever a grouping here: which wiki(s), blog(s), and
    forum(s) belong together. It owns no content of its own, so it is
    assembled entirely from markers the containers already carry rather than
    from any community document.
    """
    grouped: dict[str, DerivedCommunity] = {}

    def community_for(uuid: str, title: str | None) -> DerivedCommunity:
        community = grouped.setdefault(uuid, DerivedCommunity(id=uuid))
        # First container to supply a name wins; the rest agree or are silent.
        if title and not community.title:
            community.title = title
        return community

    for wiki in wikis:
        if not wiki.community_uuid:
            continue
        community = community_for(wiki.community_uuid, wiki.community_title)
        # A community holds at most one wiki, so first marked wins; a second
        # would mean the source disagrees with that assumption, and silently
        # dropping it is better than growing a field the model does not have.
        if community.wiki_id is None:
            community.wiki_id = wiki.id
    for forum in forums:
        if not forum.community_uuid:
            continue
        community = community_for(forum.community_uuid, forum.community_title)
        if forum.id not in community.forum_ids:
            community.forum_ids.append(forum.id)
    for blog in blogs:
        if not blog.community_uuid:
            continue
        community = community_for(blog.community_uuid, blog.community_title)
        if blog.kind == "ideation_blog":
            community.ideation_blog_id = blog.id
        else:
            community.blog_id = blog.id
    return list(grouped.values())


# --------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------
