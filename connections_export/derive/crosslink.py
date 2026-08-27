"""Resolve links between everything a single export captured.

Each container resolved its own links while it was being assembled, against
whatever it knew at the time -- which for a wiki meant its own pages, and for a
blog or a forum meant nothing at all: they were assembled with an empty lookup,
so a post linking to another post *in the same blog you just ingested* was
classified as "somewhere on the deployment" and left pointing at the live
system.

Nothing can be fixed at assembly time, because a link from the first wiki to the
last forum cannot be resolved until the last forum exists. So this runs
afterwards, over the finished model, when everything is known.

It only ever UPGRADES a link -- `hcl_deployment` to `in_export` -- and never the
reverse. A link classified as in-export by its own container was resolved
against better information than this pass has; a link that is genuinely off the
deployment is not reconsidered at all.

The unit is the export, not the container: capture a community and its wiki,
blogs, forums and Highlights cross-link to each other, because they were
captured together.
"""

from __future__ import annotations

from connections_export.crawler.assets import document_id_from_link
from connections_export.derive.links import _path_key, build_page_lookup
from connections_export.derive.model import Interchange


def _entities(interchange: Interchange):
    """Every linkable thing in the export, as `(entity, id, url, label)`."""
    for wiki in interchange.wikis:
        for page in wiki.pages.values():
            yield page, page.id, page.alternate_url, page.label
    for blog in interchange.blogs:
        for post in blog.posts.values():
            yield post, post.id, post.alternate_url, None
    for forum in interchange.forums:
        for topic in forum.topics.values():
            yield topic, topic.id, topic.alternate_url, None
    # Highlights, in BOTH directions. Its pages were in neither: their own
    # links to the community's wiki pages and posts were never upgraded, and
    # nothing else could link to a Highlights page either. A front page whose
    # links all leave the archive is the one page in a community that is
    # supposed to lead somewhere.
    for section in interchange.rich_content:
        for page in section.pages.values():
            yield page, page.id, page.alternate_url, None


def crosslink(interchange: Interchange) -> int:
    """Upgrade deployment links that point at something else in this export.

    Returns how many were upgraded, which is the only interesting thing to log:
    zero means every link left the export, which is plausible and worth being
    able to see.
    """
    entries = [
        (entity_id, url, label) for _entity, entity_id, url, label in _entities(interchange) if url
    ]
    # No early return on an empty page lookup: an export can hold documents
    # worth linking to even when nothing carries an `alternate_url`, and
    # returning here skipped the file pass entirely.
    lookup = build_page_lookup(entries) if entries else {}

    # Documents this export captured, by the id a link names them with. A
    # linked file is the same problem as a linked page and gets the same
    # answer: nothing can resolve it at assembly time, because the library
    # holding it may belong to a community assembled later -- which is the
    # common case, since people link to files in OTHER communities.
    files = {
        file_id: file_id
        for library in interchange.file_libraries
        for file_id in (library.files or {})
    }

    upgraded = 0
    for entity, _entity_id, _url, _label in _entities(interchange):
        for link in getattr(entity, "links", []) or []:
            if link.scope != "hcl_deployment":
                continue
            document_id = document_id_from_link(link.original_href) or document_id_from_link(
                link.resolved_url or ""
            )
            if document_id and document_id in files:
                # The bytes are in this archive, so the link can be answered
                # after the deployment is gone -- which is the whole reason
                # the document was captured.
                link.scope = "in_export"
                link.target_file_id = files[document_id]
                upgraded += 1
                continue
            target = (
                lookup.get(link.resolved_url or "")
                or lookup.get(link.original_href)
                or lookup.get(_path_key(link.resolved_url or "") or "")
            )
            if target:
                link.scope = "in_export"
                link.target_page_id = target
                upgraded += 1
    return upgraded
