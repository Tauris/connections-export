"""The capability manifest (PackageManifest & capabilities):
counts and a capability map, both **derived from the model**, never
asserted. An ingester reads this to degrade knowingly instead of
guessing at what the package actually captured.

`build_manifest(interchange)` is the only entry point; everything a
consumer needs is a pure function of what `Interchange` actually
carries -- e.g. `comment_threading` is `"present"` only if some comment
in the model truly has a `parent_comment_id`, never because the format
*could* carry threading.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict

from connections_export.derive.model import DerivedPage, Interchange, ResolvedAsset
from connections_export.derive.traverse import iter_all_assets
from connections_export.sbom import OWN_PACKAGE_URL, own_version

GENERATOR = "connections-export/interchange-1"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackageCounts(_Model):
    """Entity counts across the whole package, exactly as derived --
    `blobs` is the deduped count of distinct present blob hashes
    actually referenced, i.e. what `write_package` will copy. Blogs and
    forums (Stage 2) are counted separately from wiki pages/comments so
    an ingester sees exactly what each app contributed."""

    wikis: int
    pages: int
    comments: int
    versions: int
    attachments: int
    blogs: int = 0
    blog_posts: int = 0
    blog_comments: int = 0
    forums: int = 0
    forum_topics: int = 0
    forum_replies: int = 0
    communities: int = 0
    #: A community's Files section: the library, and the documents in it.
    file_libraries: int = 0
    files: int = 0
    #: A community's Highlights area: the container, and its pages.
    rich_content: int = 0
    rich_content_pages: int = 0
    blobs: int = 0


class CapabilityMap(_Model):
    """What the package IS and ISN'T carrying, so an ingester can
    degrade knowingly instead of assuming completeness the data
    doesn't have. `comment_
    threading` is the wiki-page one; blogs/forums carry their own, all
    derived from the data (never because the format *could* carry it)."""

    versions: Literal["full", "list", "none"]
    comment_threading: Literal["present", "flat"]
    acls: Literal["present", "absent"]
    assets: Literal["resolved"]
    assets_not_present: int
    blogs: Literal["present", "absent"] = "absent"
    forums: Literal["present", "absent"] = "absent"
    communities: Literal["present", "absent"] = "absent"
    blog_comment_threading: Literal["present", "flat", "none"] = "none"
    forum_reply_threading: Literal["present", "flat", "none"] = "none"


#: The oldest reader that can read a package of this schema correctly.
#: Stays put while changes are additive; rises only when something is removed
#: or repurposed.
#:
#: This exists because "treat a higher schema_version as a superset of what I
#: know how to read" -- which version 1 of the format instructed ingesters to
#: do -- is only safe while nothing is ever removed. When it is wrong, an
#: ingester obeying it does not fail: it finds the fields it knows absent,
#: derives empty bodies, and reports success. A silent gap is the one failure
#: this project refuses everywhere else, so the format carries the means to
#: make it loud instead.
MINIMUM_READER_VERSION = 2


class PackageManifest(_Model):
    schema_version: int
    #: See `MINIMUM_READER_VERSION`. A reader MUST refuse a package whose value
    #: here exceeds the version it was built against, rather than reading what
    #: it recognises and ignoring the rest.
    minimum_reader_version: int = MINIMUM_READER_VERSION
    generator: str
    #: The release that wrote this package, and where that release can be
    #: obtained. A package is read long after it is written, by someone who
    #: may have nothing but the directory: `generator` alone names a tool
    #: without saying where it lives.
    generator_version: str
    generator_url: str
    counts: PackageCounts
    capabilities: CapabilityMap


# --- model traversal --------------------------------------------------


def _iter_pages(interchange: Interchange) -> Iterator[DerivedPage]:
    for wiki in interchange.wikis:
        yield from wiki.pages.values()


def _iter_blog_posts(interchange: Interchange):
    for blog in interchange.blogs:
        yield from blog.posts.values()


def _iter_forum_topics(interchange: Interchange):
    for forum in interchange.forums:
        yield from forum.topics.values()


#: Every resolved asset in the whole model, from the one walk the package
#: writer also uses (`derive.traverse`). They had a walk each and covered
#: different apps: rich-content images and a community's file bytes were
#: counted here and never copied there, while forum attachments were missed by
#: both -- symmetrically, so no cross-check between the two could see them.
_all_assets = iter_all_assets


def _referenced_blob_hashes(assets: Iterable[ResolvedAsset]) -> set[str]:
    return {asset.blob_hash for asset in assets if asset.present and asset.blob_hash}


# --- derivation -------------------------------------------------------


def build_manifest(interchange: Interchange) -> PackageManifest:
    """Derive a `PackageManifest` from `interchange`. Pure function of
    the model -- no side effects, no filesystem access (that's
    `package.write_package`'s job)."""
    library_files = [f for lib in interchange.file_libraries for f in lib.files.values()]
    rich_pages = [p for rc in interchange.rich_content for p in rc.pages.values()]
    pages = list(_iter_pages(interchange))
    comments = [comment for page in pages for comment in page.comments]
    versions = [version for page in pages for version in page.versions]
    attachments = [attachment for page in pages for attachment in page.attachments]
    all_assets = list(_all_assets(interchange))

    blog_posts = list(_iter_blog_posts(interchange))
    blog_comments = [comment for post in blog_posts for comment in post.comments]
    forum_topics = list(_iter_forum_topics(interchange))
    forum_replies = [reply for topic in forum_topics for reply in topic.replies.values()]

    counts = PackageCounts(
        wikis=len(interchange.wikis),
        pages=len(pages),
        comments=len(comments),
        versions=len(versions),
        attachments=len(attachments),
        blogs=len(interchange.blogs),
        blog_posts=len(blog_posts),
        blog_comments=len(blog_comments),
        forums=len(interchange.forums),
        forum_topics=len(forum_topics),
        forum_replies=len(forum_replies),
        communities=len(interchange.communities),
        file_libraries=len(interchange.file_libraries),
        files=len(library_files),
        rich_content=len(interchange.rich_content),
        rich_content_pages=len(rich_pages),
        blobs=len(_referenced_blob_hashes(all_assets)),
    )

    if not versions:
        versions_capability: Literal["full", "list", "none"] = "none"
    elif any(version.content_present for version in versions):
        versions_capability = "full"
    else:
        versions_capability = "list"

    comment_threading: Literal["present", "flat"] = (
        "present" if any(comment.parent_comment_id for comment in comments) else "flat"
    )
    acls: Literal["present", "absent"] = "present" if any(page.acls for page in pages) else "absent"
    assets_not_present = sum(1 for asset in all_assets if not asset.present)

    def _threading(items, has_parent) -> Literal["present", "flat", "none"]:
        if not items:
            return "none"
        return "present" if any(has_parent(item) for item in items) else "flat"

    capabilities = CapabilityMap(
        versions=versions_capability,
        comment_threading=comment_threading,
        acls=acls,
        assets="resolved",
        assets_not_present=assets_not_present,
        blogs="present" if interchange.blogs else "absent",
        forums="present" if interchange.forums else "absent",
        communities="present" if interchange.communities else "absent",
        blog_comment_threading=_threading(blog_comments, lambda c: c.parent_comment_id),
        forum_reply_threading=_threading(forum_replies, lambda r: bool(r.child_ids)),
    )

    return PackageManifest(
        schema_version=interchange.schema_version,
        minimum_reader_version=MINIMUM_READER_VERSION,
        generator=GENERATOR,
        generator_version=own_version(),
        generator_url=OWN_PACKAGE_URL,
        counts=counts,
        capabilities=capabilities,
    )
