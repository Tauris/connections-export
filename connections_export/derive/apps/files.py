"""Assembling a community's file library."""

from __future__ import annotations

import re
from collections.abc import Iterable

from connections_export.crawler.author_plan import Involvement, _norm
from connections_export.derive.assets import resolve_asset
from connections_export.derive.index import ArchiveIndex
from connections_export.derive.model import (
    DerivedFile,
    DerivedFileFolder,
    DerivedFileLibrary,
    Provenance,
)


class DeriveError(Exception):
    """Derivation could not even locate its starting point in the
    archive -- neither a `.../wikis/feed?ps=..&page=1` request nor any
    `.../wiki/{label}/nav/feed` was ever archived. Distinct from a
    per-page/per-artifact gap, which is always represented via
    `Provenance.note`, never raised."""


#: An archived community-library feed URL. Discovery works backwards from what
#: the crawler stored, so the model can be rebuilt from the archive alone.
_COMMUNITY_LIBRARY_RE = re.compile(
    r"/files/[a-z]+/api/communitylibrary/([^/?#]+)/feed", re.IGNORECASE
)


def _discover_community_libraries(index: ArchiveIndex) -> list[str]:
    """Community uuids whose file library was archived, in first-seen order.

    Files are discovered from the ARCHIVE rather than from the communities
    assembled out of wiki/blog/forum markers: a run may capture only files, in
    which case there are no such markers and no community to hang them off.
    """
    found: list[str] = []
    for url in index.ok_urls():
        match = _COMMUNITY_LIBRARY_RE.search(url)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def _assemble_file_library(
    index: ArchiveIndex,
    *,
    community_uuid: str,
    base_url: str,
    hcl_hosts: Iterable[str],
    page_size: int,
    author_filter: str | None = None,
) -> DerivedFileLibrary | None:
    """One community's file library, from the archived feeds.

    Returns None when the library feed was never archived -- a community
    without Files, or a run that did not select them, which is not an error.

    Content comes from the FLAT feed; folders are read separately and filtered
    against it, because the collection feed also names files the community does
    not own. That filtering happens in the adapter (`folders_limited_to`) and
    is not repeated here.
    """
    from connections_export.adapters import files as files_adapter  # noqa: PLC0415

    library_url = files_adapter.community_library_url(
        base_url=base_url, community_uuid=community_uuid
    )
    # The crawler page-walked both feeds, so the archived URLs carry
    # `?ps=..&page=..` -- a bare `index.get` never matches. `paginate`
    # reconstructs the same URLs the crawler used.
    entries = index.paginate(library_url, files_adapter.parse_library_feed, page_size=page_size)
    if not entries:
        return None

    collection_url = files_adapter.community_collection_url(
        base_url=base_url, community_uuid=community_uuid
    )
    folder_refs = index.paginate(
        collection_url, files_adapter.parse_collection_feed, page_size=page_size
    )
    folder_refs = files_adapter.folders_limited_to(folder_refs or [], entries)

    folders_by_file: dict[str, list[str]] = {}
    for folder in folder_refs:
        for file_id in folder.file_ids:
            folders_by_file.setdefault(file_id, []).append(folder.id)

    target = _norm(author_filter)
    files: dict[str, DerivedFile] = {}
    order: list[str] = []
    library_id = None
    for item in entries:
        library_id = library_id or item.library_id
        asset = None
        if item.download_url:
            asset = resolve_asset(
                item.download_url,
                join_base=base_url,
                boundary_base=base_url,
                hcl_hosts=hcl_hosts,
                index=index,
            )
        files[item.id] = DerivedFile(
            id=item.id,
            # Verbatim, as Connections holds it. The filesystem-safe name is
            # decided when a package is written, never stored here.
            name=item.name,
            title=item.title,
            author=item.author,
            author_userid=item.author_userid,
            created=item.published,
            modified=item.updated,
            size=item.size,
            content_type=item.content_type,
            version_label=item.version_label,
            folder_ids=folders_by_file.get(item.id, []),
            tags=list(item.tags),
            asset=asset,
            # Judged with the crawl's own rule -- name OR user id OR
            # contributor credit -- rather than a second one that would drift
            # from it. Only ever set when the bytes really are absent: a file
            # that was captured says nothing about a filter.
            excluded_by_author_filter=(
                (asset is None or not asset.present)
                and target is not None
                and not Involvement(
                    author=item.author,
                    author_userid=item.author_userid,
                    contributors=tuple(getattr(item, "contributors", ()) or ()),
                ).matches(target)
            ),
            provenance=Provenance(source_url=library_url, hcl_id=item.provenance),
            alternate_url=item.alternate_url,
        )
        order.append(item.id)

    return DerivedFileLibrary(
        id=library_id or community_uuid,
        title="Files",
        community_uuid=community_uuid,
        file_ids=order,
        files=files,
        folders=[
            DerivedFileFolder(id=f.id, name=f.name, file_ids=list(f.file_ids)) for f in folder_refs
        ],
    )
