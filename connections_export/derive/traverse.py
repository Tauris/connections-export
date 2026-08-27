"""One walk over an assembled `Interchange`.

Two callers need "every asset in the model": the manifest, to count blobs and
account for the not-present ones, and the package writer, to copy them. They
had a walk each, and the walks covered different apps -- rich-content images
and a community's file bytes were declared in `manifest.json` and never
written to `blobs/`, while forum attachments were missed by both, which is
worse: symmetric omissions survive any cross-check between the two.

The promise `manifest.json` makes about `blobs/` can only be structural if a
single function decides what "every asset" means. Adding an app means adding
one clause HERE, and both callers get it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from connections_export.derive.model import DerivedContainer, Interchange, ResolvedAsset

if TYPE_CHECKING:
    from connections_export.apps import AppSpec


def iter_containers(interchange: Interchange) -> Iterator[tuple[AppSpec, DerivedContainer]]:
    """`(AppSpec, container)` for every container in the model.

    The field names come from the registry (`apps.AppSpec.interchange_field`),
    so a sixth app is reachable here the moment it is declared -- rather than
    when someone remembers to add a sixth `for` loop, which is how the app list
    came to be restated in eleven places.

    `iter_all_assets` below keeps its explicit per-app clauses on purpose: the
    child collections have different names and files carry version assets that
    nothing else does, so a generic walk there would be less clear, not more.
    """
    from connections_export import apps  # noqa: PLC0415 - keeps model imports light

    seen: set[str] = set()
    for spec in apps.APPS:
        if spec.interchange_field in seen:
            continue  # blog and ideation_blog are two apps over one `blogs` list
        seen.add(spec.interchange_field)
        for container in getattr(interchange, spec.interchange_field, ()):
            yield spec, container


def iter_all_assets(interchange: Interchange) -> Iterator[ResolvedAsset]:
    """Every `ResolvedAsset` anywhere in `interchange`, present or not.

    Not deduplicated: two items may legitimately reference the same blob, and
    both callers dedupe by hash themselves. Yielding every reference keeps the
    not-present accounting honest, which a set would flatten.
    """
    for wiki in interchange.wikis:
        for page in wiki.pages.values():
            yield from page.assets
            for attachment in page.attachments:
                yield attachment.asset

    for blog in interchange.blogs:
        for post in blog.posts.values():
            yield from post.assets

    for forum in interchange.forums:
        for topic in forum.topics.values():
            yield from topic.assets
            # Forum attachments are easy to walk past: a topic or reply
            # carries real `enclosure` downloads (assemble.py's
            # `_derive_attachments` resolves them like any other asset), and
            # skipping them drops every forum attachment from every package.
            for attachment in topic.attachments:
                yield attachment.asset
            for reply in topic.replies.values():
                yield from reply.assets
                for attachment in reply.attachments:
                    yield attachment.asset

    for library in interchange.file_libraries:
        for file in library.files.values():
            # A community file's bytes ARE an asset -- the package copies them,
            # so a count that skipped them understated it by the whole library.
            if file.asset is not None:
                yield file.asset
            for version in file.versions:
                if version.asset is not None:
                    yield version.asset

    for highlights in interchange.rich_content:
        for page in highlights.pages.values():
            yield from page.assets
