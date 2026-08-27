"""Assembling wikis: nav feed -> pages -> body, comments, versions,
attachments, assets and links.

Hierarchy comes from the nav feed, never a page entry's own `parentUuid`
(The navigation feed is the hierarchy authority). Where the two
disagree the page records it in `parent_discrepancy` rather than picking one
silently.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote

from connections_export.adapters.errors import AdapterError
from connections_export.adapters.model import (
    Attachment,
    Comment,
    NavNode,
    Tag,
    Version,
)
from connections_export.adapters.wikis import (
    parse_attachments_feed,
    parse_comments_feed,
    parse_nav_feed,
    parse_page_entry,
    parse_tags_feed,
    parse_versions_feed,
)
from connections_export.derive.apps._shared import (
    _derive_attachments,
    _derive_versions,
    _feed_note,
    thread_comments,
)
from connections_export.derive.assets import resolve_body_assets, strip_avatars
from connections_export.derive.index import ArchiveIndex
from connections_export.derive.links import build_page_lookup, resolve_body_links
from connections_export.derive.model import (
    DerivedPage,
    DerivedWiki,
    Provenance,
    ResolvedAsset,
)


class DeriveError(Exception):
    """Derivation could not even locate its starting point in the
    archive -- neither a `.../wikis/feed?ps=..&page=1` request nor any
    `.../wiki/{label}/nav/feed` was ever archived. Distinct from a
    per-page/per-artifact gap, which is always represented via
    `Provenance.note`, never raised."""


def _assemble_page(
    index: ArchiveIndex,
    node: NavNode,
    *,
    api_root: str,
    wiki_label: str,
    page_size: int,
    base_url: str,
    hcl_hosts: Iterable[str],
    page_lookup: dict[str, str],
) -> DerivedPage:
    notes: list[str] = []
    provenance = Provenance(hcl_id=node.id)
    page_kwargs: dict = dict(
        id=node.id, label=node.label, title=node.title, parent_id=node.parent, ordinal=node.ordinal
    )

    page_entry_obj = None
    if not node.label:
        # The crawler itself never fetches a page with no nav label
        # (crawl.py: `if not node.label: return`) -- nothing to look
        # up, represent the skeleton with a note rather than guessing.
        notes.append("navigation node carries no label; its page entry was never crawled")
    else:
        # HCL Connections URL-encodes spaces in page labels but leaves
        # RFC 3986 sub-delimiters (commas, parentheses, etc.) unencoded.
        #: labels containing
        # commas and parentheses appear with raw commas/parens in the
        # archived URLs (not %2C/%28/%29).
        _SUB_DELIMS = ",()!$&'*+;="
        entry_url = f"{api_root}/wiki/{wiki_label}/page/{quote(node.label, safe=_SUB_DELIMS)}/entry"
        entry_response = index.get(entry_url)
        if entry_response is None:
            failure = index.failure(entry_url)
            detail = f": {failure.error}" if failure and failure.error else ""
            notes.append(f"page entry not archived{detail}")
        else:
            provenance.source_url = entry_url
            provenance.blob_hash = entry_response.body_hash
            provenance.discovered_from = entry_response.discovered_from
            try:
                page_entry_obj = parse_page_entry(entry_response.content)
            except AdapterError as exc:
                notes.append(f"page entry unparsed: {exc}")

    body_html = ""
    resolved_assets: list[ResolvedAsset] = []
    resolved_links: list = []
    comments_items: list[Comment] = []
    versions_items: list[Version] = []
    attachments_items: list[Attachment] = []
    tags_items: list[Tag] = []

    if page_entry_obj is not None:
        page_kwargs["title"] = page_entry_obj.title or node.title
        page_kwargs["label"] = page_entry_obj.label or node.label
        page_kwargs["author"] = page_entry_obj.author
        page_kwargs["author_userid"] = page_entry_obj.author_userid
        page_kwargs["contributors"] = list(page_entry_obj.contributors)
        page_kwargs["created"] = page_entry_obj.created
        page_kwargs["modified"] = page_entry_obj.modified
        if page_entry_obj.uuid:
            provenance.hcl_id = page_entry_obj.uuid
        if page_entry_obj.alternate_url:
            page_kwargs["alternate_url"] = page_entry_obj.alternate_url

        # --- Hierarchy cross-check (soft, the design/the design; Q2) ---
        entry_parent = page_entry_obj.parent_uuid or None
        nav_parent = node.parent or None
        if entry_parent is not None and entry_parent != nav_parent:
            page_kwargs["parent_discrepancy"] = (
                f"page entry td:parentUuid={entry_parent!r} disagrees with the "
                f"navigation feed's parent={nav_parent!r}; the navigation parent was "
                "kept (page-entry parentUuid is unconfirmed, open-questions Q2)"
            )

        if page_entry_obj.content_src:
            body_response = index.get(page_entry_obj.content_src)
            if body_response is None:
                failure = index.failure(page_entry_obj.content_src)
                detail = f": {failure.error}" if failure and failure.error else ""
                notes.append(f"page body not archived{detail}")
            else:
                body_html = strip_avatars(body_response.content.decode("utf-8", errors="replace"))
                resolved_assets = resolve_body_assets(
                    body_html,
                    page_url=page_entry_obj.content_src,
                    base_url=base_url,
                    hcl_hosts=hcl_hosts,
                    index=index,
                )
                resolved_links = resolve_body_links(
                    body_html,
                    page_url=page_entry_obj.content_src,
                    self_page_id=node.id,
                    base_url=base_url,
                    hcl_hosts=hcl_hosts,
                    page_lookup=page_lookup,
                )

        comments_url = page_entry_obj.replies_url or (
            f"{api_root}/wiki/{wiki_label}/page/{node.label}/feed?category=comment"
        )
        versions_url = f"{api_root}/wiki/{wiki_label}/page/{node.label}/feed?category=version"
        attachments_url = f"{api_root}/wiki/{wiki_label}/page/{node.label}/feed?category=attachment"
        tags_url = f"{api_root}/wiki/{wiki_label}/page/{node.label}/feed?category=tag"

        comments_items = index.paginate(comments_url, parse_comments_feed, page_size=page_size)
        versions_items = index.paginate(versions_url, parse_versions_feed, page_size=page_size)
        attachments_items = index.paginate(
            attachments_url, parse_attachments_feed, page_size=page_size
        )
        tags_items = index.paginate(tags_url, parse_tags_feed, page_size=page_size)

        # After the walks, never before: a feed that stopped at the safety cap
        # only becomes known once it has been walked.
        for label, url in (
            ("comments", comments_url),
            ("versions", versions_url),
            ("attachments", attachments_url),
            ("tags", tags_url),
        ):
            note = _feed_note(index, label, url, page_size=page_size)
            if note:
                notes.append(note)

    if notes:
        provenance.note = "; ".join(notes)

    return DerivedPage(
        **page_kwargs,
        content_html=body_html,
        comments=thread_comments(comments_items),
        versions=_derive_versions(versions_items, index=index),
        attachments=_derive_attachments(
            attachments_items, base_url=base_url, hcl_hosts=hcl_hosts, index=index
        ),
        assets=resolved_assets,
        links=resolved_links,
        tags=[tag.term for tag in tags_items if tag.term],
        acls=[],
        provenance=provenance,
    )


def _assemble_wiki(
    index: ArchiveIndex,
    wikiref,
    *,
    api_root: str,
    page_size: int,
    base_url: str,
    hcl_hosts: Iterable[str],
) -> DerivedWiki:
    nav_url = f"{api_root}/wiki/{wikiref.label}/nav/feed?tree=true&acls=true"
    nav_response = index.get(nav_url)
    if nav_response is None:
        return DerivedWiki(
            id=wikiref.uuid,
            label=wikiref.label,
            title=wikiref.title,
            community_uuid=wikiref.community_uuid,
            community_title=wikiref.community_title,
        )
    try:
        nav_tree = parse_nav_feed(nav_response.content)
    except AdapterError:
        return DerivedWiki(
            id=wikiref.uuid,
            label=wikiref.label,
            title=wikiref.title,
            community_uuid=wikiref.community_uuid,
            community_title=wikiref.community_title,
        )

    def entry_url(node: NavNode) -> str:
        return f"{api_root}/wiki/{wikiref.label}/page/{quote(node.label, safe=",()!$&'*+=")}/entry"

    # A reduced crawl may archive the complete navigation feed but only the
    # first N page entries. Do not turn the unvisited remainder into empty
    # DerivedPage placeholders; retain explicit entry failures so those remain
    # inspectable as failed pages.
    crawled_nodes = [
        node
        for node in nav_tree.nodes
        if node.label and (index.get(entry_url(node)) is not None or index.failure(entry_url(node)))
    ]

    page_lookup = build_page_lookup(
        [(node.id, entry_url(node), node.label) for node in crawled_nodes if node.label]
    )

    children_by_parent: dict[str | None, list[NavNode]] = {}
    for node in crawled_nodes:
        children_by_parent.setdefault(node.parent, []).append(node)

    pages: dict[str, DerivedPage] = {}
    for node in crawled_nodes:
        pages[node.id] = _assemble_page(
            index,
            node,
            api_root=api_root,
            wiki_label=wikiref.label,
            page_size=page_size,
            base_url=base_url,
            hcl_hosts=hcl_hosts,
            page_lookup=page_lookup,
        )

    for node in crawled_nodes:
        # Children ordered by sibling ordinal (the design, "Tree
        # reproduced from nav"); `sorted` is stable, so pages sharing
        # a duplicate ordinal (the nasty-case toggle) keep their
        # original feed-relative order, mirroring the fake dataset's
        # own `FakeWiki.children_of`.
        kids = sorted(children_by_parent.get(node.id, []), key=lambda n: n.ordinal)
        pages[node.id].child_ids = [kid.id for kid in kids]

    root_nodes = sorted(children_by_parent.get(None, []), key=lambda n: n.ordinal)

    return DerivedWiki(
        id=wikiref.uuid,
        label=wikiref.label,
        title=wikiref.title,
        community_uuid=wikiref.community_uuid,
        community_title=wikiref.community_title,
        root_page_ids=[node.id for node in root_nodes],
        pages=pages,
    )


# --------------------------------------------------------------------
# Blogs assembly -- mirrors `_assemble_wiki`/
# `_assemble_page`: list feed -> per blog: entries feed (page-walked)
# -> per post: body assets/links + comments feed (page-walked, threaded).
# --------------------------------------------------------------------
