"""Assembling a community's Rich Content (Highlights) pages.

Reports how many widgets were PLACED against how many were INITIALIZED, so
"we captured three" can never quietly mean "there were four".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import unquote

from connections_export.adapters.errors import AdapterError
from connections_export.derive.assets import resolve_body_assets
from connections_export.derive.index import ArchiveIndex
from connections_export.derive.links import resolve_body_links
from connections_export.derive.model import (
    DerivedRichContent,
    DerivedRichContentPage,
)


class DeriveError(Exception):
    """Derivation could not even locate its starting point in the
    archive -- neither a `.../wikis/feed?ps=..&page=1` request nor any
    `.../wiki/{label}/nav/feed` was ever archived. Distinct from a
    per-page/per-artifact gap, which is always represented via
    `Provenance.note`, never raised."""


#: An archived widget-layout URL -- the community's Rich Content discovery
#: document.
_RTE_LAYOUT_RE = re.compile(r"/communities/service/atom/community/widgets\?communityUuid=([^&#]+)")


def _discover_rich_content(index: ArchiveIndex) -> list[str]:
    """Community uuids whose widget layout was archived, in first-seen order.

    Read from the ARCHIVE rather than from assembled communities, for the same
    reason file libraries are: a run may capture only rich content, in which
    case there are no wiki/blog/forum markers and no community to hang it off.

    Discovery keys off the LAYOUT, not off the pages: a community whose
    Highlights area holds only uninitialized widgets archived a layout and no
    pages, and it should still derive -- as an empty container that says so,
    rather than vanishing.
    """
    found: list[str] = []
    for url in index.ok_urls():
        match = _RTE_LAYOUT_RE.search(url)
        if match:
            uuid = unquote(match.group(1))
            if uuid not in found:
                found.append(uuid)
    return found


def _assemble_rich_content(
    index: ArchiveIndex,
    *,
    community_uuid: str,
    base_url: str,
    hcl_hosts: Iterable[str],
) -> DerivedRichContent | None:
    """One community's Rich Content pages, from the archived layout + entries.

    Returns None when the layout was never archived -- a run that did not
    select rich content, which is not an error.
    """
    from connections_export.adapters import rte as rte_adapter  # noqa: PLC0415

    layout_url = rte_adapter.widget_layout_url(base_url=base_url, community_uuid=community_uuid)
    layout = index.get(layout_url)
    if layout is None:
        return None
    try:
        widgets = rte_adapter.parse_widget_layout(layout.content, community_uuid=community_uuid)
    except AdapterError:
        # A layout that will not parse means no pages can be discovered. Narrow
        # on purpose: a broad `except` here swallowed a plain TypeError (an
        # IndexedResponse passed where bytes were wanted) and turned a real bug
        # into an empty container that looked like a community with no content.
        widgets = []

    pages: dict[str, DerivedRichContentPage] = {}
    order: list[str] = []
    for widget in widgets:
        if not widget.initialized:
            # Nothing was ever written into it, so nothing was fetched. It is
            # still counted below, in `placed`.
            continue
        page_url = rte_adapter.rich_content_page_url(
            base_url=base_url, community_uuid=community_uuid, resource_id=widget.resource_id
        )
        body = index.get(page_url)
        if body is None:
            continue
        try:
            page = rte_adapter.parse_page_entry(body.content, resource_id=widget.resource_id)
        except AdapterError:
            # One unreadable entry must not lose the rest of the community's
            # pages. Narrow for the same reason as above.
            continue
        html = page.content_html or ""
        # A rich content page is fetched as an entry, not rendered at a page
        # URL of its own, so relative hrefs resolve against the deployment
        # root -- the same choice `_assemble_post` makes for a blog post.
        resolved_assets = resolve_body_assets(
            html, page_url=base_url, base_url=base_url, hcl_hosts=hcl_hosts, index=index
        )
        resolved_links = resolve_body_links(
            html,
            page_url=base_url,
            self_page_id=page.resource_id,
            base_url=base_url,
            hcl_hosts=hcl_hosts,
            page_lookup={},
        )
        pages[page.resource_id] = DerivedRichContentPage(
            id=page.resource_id,
            resource_id=page.resource_id,
            title=page.title or widget.title,
            content_html=page.content_html,
            author=page.author,
            author_userid=page.author_userid,
            created=page.published,
            modified=page.updated,
            version_label=page.version_label,
            alternate_url=page.alternate_url,
            assets=resolved_assets,
            links=resolved_links,
        )
        order.append(page.resource_id)

    return DerivedRichContent(
        id=community_uuid,
        title="Highlights",
        community_uuid=community_uuid,
        page_ids=order,
        pages=pages,
        placed=len(widgets),
        initialized=len([w for w in widgets if w.initialized]),
    )
