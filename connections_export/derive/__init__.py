"""`derive`: reads an archive (produced by the crawler) and assembles
the tool-agnostic interchange model.

Public surface: `derive(archive) -> Interchange`, plus the model types
it returns. Everything else (`ArchiveIndex`, the assembly internals,
asset/link resolution) is implementation detail of how `derive` gets
there.
"""

from connections_export.derive.assemble import DeriveError, derive
from connections_export.derive.model import (
    DerivedAttachment,
    DerivedBlog,
    DerivedBlogPost,
    DerivedComment,
    DerivedCommunity,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    DerivedPage,
    DerivedVersion,
    DerivedWiki,
    Interchange,
    LinkRef,
    Provenance,
    ResolvedAsset,
)

__all__ = [
    "derive",
    "DeriveError",
    "Interchange",
    "DerivedWiki",
    "DerivedPage",
    "DerivedComment",
    "DerivedCommunity",
    "DerivedVersion",
    "DerivedAttachment",
    "DerivedBlog",
    "DerivedBlogPost",
    "DerivedComment",
    "DerivedForum",
    "DerivedForumTopic",
    "DerivedForumReply",
    "ResolvedAsset",
    "LinkRef",
    "Provenance",
]
