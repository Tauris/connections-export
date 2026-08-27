"""Normalized entities the wikis adapter parses Atom/JSON bytes into
(Normalized model (pydantic v2)). Pure data, no parsing
logic -- pydantic validates the shape of stale, untrusted API data
(project.md, "Key libraries").
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WikiRef(_Model):
    """One entry from the wikis feed."""

    uuid: str
    label: str
    title: str
    created: str | None = None
    modified: str | None = None
    self_url: str | None = None
    community_uuid: str | None = None
    community_title: str | None = None


class Page(_Model):
    """A wiki page entry.

    `replies_count` is not in the literal field list, but
    something has to carry the `thr:count` value off the `rel="replies"`
    link so callers can satisfy the "Comment count agrees with
    the replies link" scenario (compare it against the parsed comments
    feed's length) -- the own atom-core helper list includes
    `thr_count(entry)` for exactly this purpose, it just wasn't wired
    to a model field. Added here as the minimal fix.
    """

    uuid: str
    label: str
    title: str
    parent_uuid: str | None = None
    version_label: int | None = None
    visibility: str | None = None
    created: str | None = None
    modified: str | None = None
    author: str | None = None
    author_userid: str | None = None
    contributors: list[str] = []
    content_src: str | None = None
    alternate_url: str | None = None  # link[rel="alternate"] — browser/HTML URL for live PDF
    replies_url: str | None = None
    replies_count: int | None = None
    ranks: dict[str, int] = {}
    category_term: str | None = None


class NavNode(_Model):
    """One real page node in a parsed navigation tree.

    `ordinal` is this node's position among its siblings, taken from
    the order of `_reference` entries inside its parent's `children`
    array (including the synthetic root's `children`, for top-level
    pages) -- the only sibling-order signal the nav feed carries
    (reference doc, "Sibling ordering -- NOT DOCUMENTED"; Q1). Falls
    back to feed-array position for a node no `children` list
    references (e.g. a bare/stub feed with no tree structure at all).

    `is_root` marks a node that is a direct child of the synthetic
    tree wrapper -- i.e. a genuine top-level page -- not the wrapper
    itself, which is never emitted as a NavNode (see NavTree.root_id).
    """

    id: str
    label: str | None = None
    title: str | None = None
    parent: str | None = None
    ordinal: int = 0
    child_ids: list[str] = []
    is_root: bool = False


class NavTree(_Model):
    """A parsed navigation feed.

    `nodes` holds every real page node, in feed order. `root_id` is
    the id of the synthetic `{"id": "tree", "root": "true"}` wrapper if
    the feed carried one (i.e. was fetched with `tree=true`), else
    None -- it is recorded, not emitted as a page node (Synthetic root is recognized).
    """

    nodes: list[NavNode] = []
    root_id: str | None = None


class Comment(_Model):
    """# INFERRED: no comment entry schema is published anywhere
    in the reference (3.0.1 through 8.0). Parsed from the fake's
    plausible shape; every field but `uuid` is optional so an
    unexpected real shape degrades rather than failing outright.
    `in_reply_to` is speculative -- no Wikis-specific documentation
    confirms wiki comments can be threaded at all.

    `id`, not `uuid`: this was the one wikis-era model whose identifier name
    differed from `BlogComment`'s, and the two are threaded by the same
    function. That single disagreement cost a `getattr` in the shared
    threader. The remaining wikis-era `uuid` fields (`WikiRef`, `Page`,
    `Version`, `Attachment`, `BlogRef`) are a separate migration -- see the
    note in `docs/reference/architecture.md`.
    """

    id: str
    title: str | None = None
    content_html: str | None = None
    author: str | None = None
    author_userid: str | None = None
    contributors: list[str] = []
    published: str | None = None
    updated: str | None = None
    in_reply_to: str | None = None


class Version(_Model):
    """# INFERRED: no version entry schema is published; only
    `<td:versionLabel>` on the *page* entry is."""

    uuid: str
    version_label: int | None = None
    content_src: str | None = None
    author: str | None = None
    created: str | None = None


class Attachment(_Model):
    """# INFERRED: no attachment entry schema is published; we
    only know attachments are listed via `?category=attachment` and
    counted by `snx:rank scheme=".../attachments"` on the page entry.
    """

    uuid: str
    filename: str | None = None
    content_type: str | None = None
    enclosure_href: str | None = None
    author: str | None = None
    created: str | None = None


class Tag(_Model):
    """# INFERRED: no tag entry schema is published either."""

    term: str


# --------------------------------------------------------------------
# Blogs
# --------------------------------------------------------------------


class BlogRef(_Model):
    """One entry from the "list all blogs" feed
    (`/blogs/{homepage}/feed/blogs/atom`).

    # INFERRED: the published API reference documents this
    endpoint's *path* but gives no field list for the
    "blog" (container) resource itself -- only for a blog *entry*
    (post). Modeled here on the cross-app "Genuinely shared" fields
    (id, title, self link) the reference confirms hold for all three
    apps, deliberately not on the post/entry field list, which
    describes a different resource.
    """

    uuid: str
    title: str | None = None
    self_url: str | None = None
    community_uuid: str | None = None
    community_title: str | None = None
    blog_type: str | None = None


class BlogPost(_Model):
    """A blog entry (post). `provenance` is the untouched `<id>` LSID text; `id` is the
    `entry_uuid`-stripped bare uuid.
    """

    id: str
    title: str | None = None
    author: str | None = None
    author_userid: str | None = None
    contributors: list[str] = []
    content_html: str | None = None
    published: str | None = None
    updated: str | None = None
    tags: list[str] = []
    comments_enabled: bool | None = None
    ranks: dict[str, int] = {}
    provenance: str | None = None
    alternate_url: str | None = None  # link[rel="alternate"] — browser URL for live PDF


class BlogComment(_Model):
    """A comment on a blog entry. Threading is arbitrary-depth via
    `thr:in-reply-to/@ref`: blog comments use the same RFC 4685 mechanism
    as forum replies. `in_reply_to` is the parent comment's id (stripped to
    bare uuid), `None` for a top-level comment.

    The field list mirrors the generic Atom entry shape, since no comment
    entry sample is documented."""

    id: str
    author: str | None = None
    author_userid: str | None = None
    contributors: list[str] = []
    content_html: str | None = None
    published: str | None = None
    in_reply_to: str | None = None


# --------------------------------------------------------------------
# Rich Content
# --------------------------------------------------------------------


class RichContentPage(_Model):
    """One Rich Content page on a community's Highlights area.

    that the page entry carries category `page`, the body as INLINE
    HTML, a version label, and media/enclosure, edit and alternate links. The
    literal XML shape is not documented, as with Files: the captures recorded which
    fields exist rather than the bytes, so every field degrades to None.

    `resource_id` is the `widgetResourceId` the page is addressed by -- it comes
    from the widget layout, not from the entry, so it is set by the caller.
    """

    resource_id: str
    title: str | None = None
    #: The page body, when the entry carries it inline.
    content_html: str | None = None
    #: Some deployments put the body at the Atom content `src` URL instead.
    content_src: str | None = None
    author: str | None = None
    author_userid: str | None = None
    published: str | None = None
    updated: str | None = None
    version_label: str | None = None
    media_url: str | None = None
    alternate_url: str | None = None
    provenance: str | None = None


class RichContentWidget(_Model):
    """One Rich Content widget instance, as the community widget layout lists it.

    A layout lists more instances than there are readable pages: a capture saw
    **eight instances, seven initialized**. An uninitialized widget is one an
    owner has placed but never written into, and it has no resource id, so
    there is nothing to fetch. Keeping the uninitialized ones here rather than
    dropping them at parse time is what lets the crawler say "8 placed, 7 with
    content" instead of silently reporting 7 as the whole truth.
    """

    #: None when the widget was never initialized -- nothing to retrieve.
    resource_id: str | None = None
    title: str | None = None
    #: The `widgetResourceContainerId`, e.g. `conn-rte#{communityUuid}`.
    container_id: str | None = None

    @property
    def initialized(self) -> bool:
        return bool(self.resource_id)


# --------------------------------------------------------------------
# Forums
# --------------------------------------------------------------------


class CommunityFile(_Model):
    """One file in a community library.

    Field names are from two live captures -- `libraryId`,
    `libraryType`, `objectTypeName`, `isFiledInFolder`, `versionLabel`,
    `totalMediaSize`, and links `self`/`alternate`/`edit`/`edit-media`/
    `enclosure`/`thumbnail`/`replies`. The literal XML shape is not documented:
    the captures recorded which fields exist, not the bytes carrying them.

    `download_url` is the `enclosure` link -- the bytes. Observed shape:
    `/files/basic/anonymous/api/library/{libraryId}/document/{id}/media/...`.
    """

    id: str
    name: str | None = None
    title: str | None = None
    library_id: str | None = None
    author: str | None = None
    author_userid: str | None = None
    published: str | None = None
    updated: str | None = None
    size: int | None = None
    content_type: str | None = None
    version_label: str | None = None
    #: True when the file is associated with at least one folder. Folders are
    #: an association over a flat store, so this is a hint, not a location.
    filed_in_folder: bool = False
    tags: list[str] = []
    download_url: str | None = None
    alternate_url: str | None = None
    replies_url: str | None = None
    provenance: str | None = None


class CommunityFolder(_Model):
    """A folder (collection) in a community library, and the file ids in it.

    `file_ids` may name files the community library feed does not contain --
    the collection feed carries personal-file entries too. Filter against the
    library, never merge: a community export must not pull in someone's
    private documents.
    """

    id: str
    name: str | None = None
    file_ids: list[str] = []


class ForumTopic(_Model):
    """A forum topic. `forum_uuid` is the parent
    forum's uuid, read off the topic's own `thr:in-reply-to/@ref` --
    present on every topic entry (the TRAP: this is data, not a type
    signal; type comes only from the `sn/type` category term, never
    from `thr:in-reply-to`'s presence). `flags` holds `sn/flags`
    category terms (`pinned`/`locked`/`question`/`answered`).
    `provenance` is the untouched `<id>` LSID text.
    """

    id: str
    forum_uuid: str | None = None
    title: str | None = None
    author: str | None = None
    author_userid: str | None = None
    contributors: list[str] = []
    content_html: str | None = None
    published: str | None = None
    flags: set[str] = set()
    tags: list[str] = []
    attachments: list[Attachment] = []
    provenance: str | None = None
    alternate_url: str | None = None  # link[rel="alternate"] — browser URL for live PDF


class ForumReply(_Model):
    """A forum reply, from the FLAT replies feed (docs/reference/
    hcl-blogs-forums-api.md, Forums "Threading", the module's most
    load-bearing finding). `ref` is the immediate parent (another
    reply, or the topic for a top-level reply); `source_topic` is the
    top-level topic, letting an arbitrary-depth tree be rebuilt from
    one flat feed pass (see `build_reply_tree`). `flags` is not in
    the literal ForumReply field list, but the reference
    documents the `answer` flag applying specifically to replies
    ("Flags... on replies" -- the `sn/flags` scheme) with nothing
    else to carry it; added here for the same reason wikis.py's `Page`
    added `replies_count` beyond its own literal list.
    """

    id: str
    author: str | None = None
    author_userid: str | None = None
    contributors: list[str] = []
    content_html: str | None = None
    published: str | None = None
    ref: str | None = None
    source_topic: str | None = None
    flags: set[str] = set()
    attachments: list[Attachment] = []
    alternate_url: str | None = None


class ReplyNode(_Model):
    """One node of the reply tree `build_reply_tree` reconstructs from
    Forums' flat replies feed (`build_reply_tree(replies)`
    -> nested structure (children keyed by ref)). `children` holds
    the nodes whose `ref` names this node's reply id, recursively --
    the tree structure the flat feed never carries directly."""

    reply: ForumReply
    children: list[ReplyNode] = []


ReplyNode.model_rebuild()
