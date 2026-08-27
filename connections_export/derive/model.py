"""The interchange model (Interchange model (pydantic
v2)): tool-agnostic entities assembled from an archive. Deliberately
free of HCL-isms in the shape itself -- nothing named `td:`/`snx:`/
`lsid` here, and no field carries an HCL identifier as its primary
key. Every entity that needs one keeps it in a `Provenance` sidecar
instead (`hcl_id`), alongside where the bytes came from
(`source_url`/`blob_hash`/`discovered_from`) and, when a piece of the
archive was missing or unparseable, a human-readable `note` -- the
"nothing silently dropped" discipline (project.md principle 2) carried
into the derived model.

`DerivedPage.id` is the page uuid, but callers are told to treat it as
an opaque interchange id; the HCL-ness lives in `provenance.hcl_id`.
Sibling order is the plain integer `ordinal`; a page's children are
`child_ids` ordered by it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 2


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provenance(_Model):
    """Where a derived entity's bytes came from, and (when relevant)
    why a piece of it is missing -- the only place an HCL identifier
    (`hcl_id`) belongs."""

    source_url: str | None = None
    blob_hash: str | None = None
    hcl_id: str | None = None
    discovered_from: str | None = None
    note: str | None = None


class ResolvedAsset(_Model):
    """A body- or attachment-referenced asset, resolved against the
    archive. `present=False` for a same-deployment asset with no
    archived blob is a surfaced completeness defect, not an omission."""

    original_href: str
    resolved_url: str
    filename: str | None = None
    blob_hash: str | None = None
    present: bool
    scope: Literal["same", "external"]


class LinkRef(_Model):
    """A body `<a href>`, normalized to an absolute `resolved_url`
    then classified three ways:

    - `in_export`: resolves to a page this archive derived --
      `target_page_id` is set.
    - `hcl_deployment`: the resolved host is the deployment (base host
      or an `hcl_hosts` entry) but it doesn't resolve to an exported
      page -- another wiki, a Files document, another app. An
      *absolute* URL to the deployment is never misclassified as
      external merely for being absolute.
    - `external`: a genuinely different host (the public web).

    `original_href` always keeps the href exactly as it appeared in
    the body, whatever the scope."""

    original_href: str
    resolved_url: str | None = None
    scope: Literal["in_export", "hcl_deployment", "external"]
    target_page_id: str | None = None
    #: The captured document this link names, when it names one this export
    #: holds. A link to a Files document is `in_export` on the same terms as a
    #: link to a page: the archive can answer it after the deployment is gone.
    #: `None` for every other link, and for a document nobody captured -- which
    #: stays an honest `hcl_deployment` reference rather than pretending.
    target_file_id: str | None = None


class DerivedComment(_Model):
    """A comment, threaded via `parent_comment_id` when the archived comment
    carried reply information; flat (`None`) otherwise.

    Serves wiki pages, blog posts and community files alike. Blogs had a class
    of its own until the timestamp spellings were unified, at which point
    nothing distinguished the two -- they carried identical fields and differed
    only in whether the date was called `created` or `published`. Blogs
    supports exactly one level of threading (`thr:in-reply-to`,);
    wiki comment threading is unconfirmed and is carried through
    best-effort, exactly as parsed.
    """

    id: str
    author: str | None = None
    author_userid: str | None = None
    contributors: list[str] = Field(default_factory=list)
    content_html: str | None = None
    created: str | None = None
    modified: str | None = None
    parent_comment_id: str | None = None


class DerivedContainer(_Model):
    """What every app's top-level container has in common.

    A wiki, a blog, a forum, a community's file library and its Highlights
    area are five different things that are addressed, titled and attributed
    identically. The child collection is NOT here: `wiki.pages`, `blog.posts`,
    `forum.topics` and `library.files` keep their own names because that
    vocabulary is what makes the model readable, and generic traversal reaches
    them through `apps.AppSpec.interchange_field` instead.
    """

    id: str
    title: str | None = None
    #: Which community this container belongs to, when it belongs to one. A
    #: wiki captured on its own belongs to none, and says so with `None`.
    community_uuid: str | None = None
    community_title: str | None = None
    #: The browser URL on the source deployment -- a linkback while the system
    #: is still up, and a record of where this came from after it is not.
    alternate_url: str | None = None


class DerivedItem(_Model):
    """What every app's individual piece of content has in common.

    A page, a post, a topic, a reply, a file and a rich-content page all carry
    the same authorship, body, dates, resolved assets and links. Four consumers
    used to enumerate those fields per app, which is how a fact about one app
    kept being forgotten in the other four.

    A comment is deliberately NOT one of these: it carries no assets and no
    links, and giving it empty ones would state something untrue about what was
    captured.
    """

    id: str
    title: str | None = None
    author: str | None = None
    author_userid: str | None = None
    contributors: list[str] = Field(default_factory=list)
    content_html: str = ""
    created: str | None = None
    modified: str | None = None
    assets: list[ResolvedAsset] = Field(default_factory=list)
    links: list[LinkRef] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)
    alternate_url: str | None = None


class DerivedVersion(_Model):
    """A historical page version. `content_present` records whether
    this version's own content was actually archived (it usually is
    not -- see `Config.versions`), never fabricated content."""

    id: str
    version_label: int | None = None
    author: str | None = None
    created: str | None = None
    content_present: bool = False


class DerivedAttachment(_Model):
    """A page attachment; its own bytes are just another resolved
    asset (`asset`), same treatment as a body-referenced image."""

    id: str
    filename: str | None = None
    content_type: str | None = None
    asset: ResolvedAsset


class DerivedPage(DerivedItem):
    """One wiki page. Hierarchy (`parent_id`/`ordinal`/`child_ids`)
    comes from the navigation feed -- the hierarchy authority
    (Hierarchy and order from the navigation feed) --
    never from a page entry's own (unconfirmed, Q2) parent pointer.
    `parent_discrepancy` is set, not raised, when the two disagree.
    """

    label: str | None = None

    parent_id: str | None = None
    ordinal: int = 0
    child_ids: list[str] = Field(default_factory=list)

    comments: list[DerivedComment] = Field(default_factory=list)
    versions: list[DerivedVersion] = Field(default_factory=list)
    attachments: list[DerivedAttachment] = Field(default_factory=list)

    # True when this page was kept only to preserve the tree path to an
    # authored descendant (the author filter) -- context, not something the
    # target user authored. False in an unfiltered model.
    is_context: bool = False
    tags: list[str] = Field(default_factory=list)
    # ACL principal resolution needs a Profiles lookup this project
    # doesn't have yet -- always empty
    # for now, kept as a field so the shape doesn't need to change
    # when that lands.
    acls: list[str] = Field(default_factory=list)

    parent_discrepancy: str | None = None


class DerivedWiki(DerivedContainer):
    """One wiki: its pages keyed by id, plus the ordered top-level
    page ids (sibling order within a level lives on each page's own
    `ordinal`/`child_ids`)."""

    label: str
    #: A wiki always has a title; the base leaves it optional because four of
    #: the five containers can genuinely lack one.
    title: str
    root_page_ids: list[str] = Field(default_factory=list)
    pages: dict[str, DerivedPage] = Field(default_factory=dict)


# --------------------------------------------------------------------
# Blogs (Stage 2,) -- mirrors the wiki shapes:
# HCL-isms in `Provenance`, assets/links resolved, id-keyed containers.
# --------------------------------------------------------------------


class DerivedBlogPost(DerivedItem):
    """One blog entry (post): its body, resolved assets/links, threaded
    comments, and rank counters. `ordinal` is feed order (blogs have no
    hierarchy). HCL identifiers live in `provenance`."""

    ordinal: int = 0
    tags: list[str] = Field(default_factory=list)
    ranks: dict[str, int] = Field(default_factory=dict)
    comments: list[DerivedComment] = Field(default_factory=list)


class DerivedBlog(DerivedContainer):
    """One blog: its posts keyed by id, plus the ordered post ids (feed
    order). `handle` is the blog's URL handle; `title` its display
    name. `alternate_url` is the human-readable browser URL for the blog
    on the source HCL system (link[rel="alternate"] from the entries feed,
    or constructed from base_url + uuid when absent)."""

    handle: str | None = None
    kind: Literal["blog", "ideation_blog"] = "blog"
    post_ids: list[str] = Field(default_factory=list)
    posts: dict[str, DerivedBlogPost] = Field(default_factory=dict)


# --------------------------------------------------------------------
# Forums (Stage 2,). The reply tree is stored the
# same id-keyed way `DerivedWiki` stores its page tree: a flat `replies`
# dict + ordered `child_ids` per node, so an arbitrary-depth thread
# (`adapters.forums.build_reply_tree`) round-trips without nesting the
# JSON.
# --------------------------------------------------------------------


class DerivedForumReply(DerivedItem):
    """One forum reply. `child_ids` are the ids of the replies whose
    parent is this one (ordered) -- the arbitrary-depth tree the flat
    replies feed never carries directly. `flags` holds `sn/flags` terms
    (e.g. `answer`)."""

    flags: list[str] = Field(default_factory=list)
    child_ids: list[str] = Field(default_factory=list)
    attachments: list[DerivedAttachment] = Field(default_factory=list)
    #: A reply body can be genuinely absent, where a topic's cannot; the
    #: base's `""` would claim an empty body was captured.
    content_html: str | None = None


class DerivedForumTopic(DerivedItem):
    """One forum topic: its body, resolved assets/links, `flags`
    (`pinned`/`locked`/`question`/`answered`), and its reply tree --
    ordered top-level `reply_ids` plus every reply flat in `replies`
    keyed by id (nested via each reply's `child_ids`)."""

    flags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    reply_ids: list[str] = Field(default_factory=list)
    replies: dict[str, DerivedForumReply] = Field(default_factory=dict)
    attachments: list[DerivedAttachment] = Field(default_factory=list)


class DerivedForum(DerivedContainer):
    """One forum: its topics keyed by id, plus the ordered topic ids
    (feed order). `alternate_url` is the browser URL for the forum or
    thread on the source HCL system (linkback while the system is live)."""

    topic_ids: list[str] = Field(default_factory=list)
    topics: dict[str, DerivedForumTopic] = Field(default_factory=dict)


# --------------------------------------------------------------------
# Files (a community's file library). Modelled as a CONTAINER of items,
# the way a blog holds posts -- not as attachments. A community file has
# an author, timestamps, a size, version history, its own comments and
# folder memberships; `DerivedAttachment` holds four fields and none of
# that. Treating a library as attachments would discard most of what
# makes Files worth exporting.
# --------------------------------------------------------------------


class DerivedFileVersion(_Model):
    """One version of a file. `label` is HCL's own version label (observed
    running 1..10 on a live library), not an index we invented."""

    label: str
    created: str | None = None
    author: str | None = None
    size: int | None = None
    asset: ResolvedAsset | None = None


class DerivedFile(DerivedItem):
    """One file in a library.

    `name` is what Connections holds, verbatim -- a filesystem-safe name is
    computed when a package is written (`interchange.filenames`), never stored
    here, so the model keeps the truth and the package keeps the compromise.

    `folder_ids` is a LIST because a file can be associated with several
    folders. Folders are an association on top of a flat store, not a location,
    so a file has memberships rather than a path.
    """

    name: str | None = None
    size: int | None = None
    content_type: str | None = None
    #: HCL's label for the current version; `versions` carries the history.
    version_label: str | None = None
    versions: list[DerivedFileVersion] = Field(default_factory=list)
    folder_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    #: The file's own comments -- files have a `replies` feed of their own.
    comments: list[DerivedComment] = Field(default_factory=list)
    #: The bytes, resolved like any other asset.
    asset: ResolvedAsset | None = None
    #: Absent bytes are not all the same absence. This one means the capture
    #: was filtered to somebody else's documents, so nothing ever tried to
    #: fetch this file -- as opposed to a download the deployment refused or
    #: that failed. Saying "not captured" for both told a reader that a
    #: document was missing when it had only been filtered out.
    excluded_by_author_filter: bool = False


class DerivedFileFolder(_Model):
    """A folder in a library. Holds ids, not files: the same file may appear
    in more than one folder, and its bytes live once."""

    id: str
    name: str | None = None
    file_ids: list[str] = Field(default_factory=list)


class DerivedFileLibrary(DerivedContainer):
    """One community's file library: files keyed by id, plus feed order.

    Mirrors `DerivedBlog`/`DerivedForum` deliberately -- same shape, same
    ordering convention, so everything downstream that walks a container
    already knows how to walk this one.
    """

    file_ids: list[str] = Field(default_factory=list)
    files: dict[str, DerivedFile] = Field(default_factory=dict)
    folders: list[DerivedFileFolder] = Field(default_factory=list)


class DerivedRichContentPage(DerivedItem):
    """One Rich Content page from a community's Highlights area.

    Very close to `DerivedPage` minus the hierarchy: a titled HTML body with a
    version. `resource_id` is what the page is addressed by in the deployment
    (`widgetResourceId`), and doubles as its id here -- there is no second
    identifier that survives a round trip.
    """

    resource_id: str
    version_label: str | None = None
    #: A Highlights widget can be placed and never written into, which is
    #: a different fact from an empty body.
    content_html: str | None = None


class DerivedRichContent(DerivedContainer):
    """A community's Highlights pages, as a container.

    Mirrors `DerivedFileLibrary` -- same shape, same ordering convention -- so
    scoping, the reader and the PDF walk it the way they walk everything else.

    A container rather than a list hung off `DerivedCommunity` on purpose: a
    community here is a GROUPING assembled from the `snx:communityUuid` markers
    its containers carry, and owns no content of its own. Giving it content
    would break that, so the community keeps only a pointer
    (`rich_content_id`), exactly as it does for its file library.

    `placed` and `initialized` are what the widget layout said before anything
    was fetched. They are kept because they can exceed `page_ids`: a widget an
    owner placed and never wrote into has no page behind it, and a model that
    recorded only what it captured could not tell anyone that.
    """

    page_ids: list[str] = Field(default_factory=list)
    pages: dict[str, DerivedRichContentPage] = Field(default_factory=dict)
    #: Rich Content widgets on the community, per the layout.
    placed: int = 0
    #: How many of those had content to retrieve.
    initialized: int = 0


class DerivedCommunity(_Model):
    """One community and the HCL containers associated with it.

    A community is only ever a grouping here -- it owns no content of its
    own, so it is assembled entirely from the `snx:communityUuid` markers its
    containers already carry, never from a community document.

    Cardinality is not uniform, and the fields reflect that:

    - blog, ideation blog: AT MOST ONE each.
    - wiki: AT MOST ONE.
    - forums: MANY -- one community can hold several.

    Do not "tidy" the single fields into lists for symmetry: they are single
    because the source system is.
    """

    id: str
    title: str | None = None
    wiki_id: str | None = None
    forum_ids: list[str] = Field(default_factory=list)
    blog_id: str | None = None
    ideation_blog_id: str | None = None
    #: The community's file library, when one was captured. Singular:
    #: a community has one Files section, addressed by the community uuid.
    file_library_id: str | None = None
    #: The community's Rich Content pages, when captured. Singular for the
    #: same reason: the Highlights area belongs to the community itself.
    rich_content_id: str | None = None
    #: The community's own picture, captured like any other asset so it lives
    #: in the archive rather than on a system that may not answer later. This
    #: is what `{logo}` resolves to in a PDF header or footer.
    logo: ResolvedAsset | None = None


class Interchange(_Model):
    """The top-level derive result: every wiki (and, Stage 2, every blog
    and forum) assembled from one archive, plus run-level provenance.

    `hcl_hosts` is the deployment host set the
    same-deployment/external boundary was actually judged against --
    recovered from the archive's own run metadata when present, kept
    here so the derived model itself records where its classification
    boundary came from, not just the content it holds.

    `blogs`/`forums` default empty, so a
    wiki-only archive derives to exactly the same model as before."""

    schema_version: int = SCHEMA_VERSION
    base_url: str | None = None
    #: The author filter this archive was captured under, from the run's own
    #: record. Not a view setting: it says what the archive HOLDS, which is
    #: why an item can be absent without anything having gone wrong.
    author_filter: str | None = None
    source_version: str | None = None
    run_id: str | None = None
    hcl_hosts: list[str] = Field(default_factory=list)
    wikis: list[DerivedWiki] = Field(default_factory=list)
    blogs: list[DerivedBlog] = Field(default_factory=list)
    forums: list[DerivedForum] = Field(default_factory=list)
    file_libraries: list[DerivedFileLibrary] = Field(default_factory=list)
    rich_content: list[DerivedRichContent] = Field(default_factory=list)
    communities: list[DerivedCommunity] = Field(default_factory=list)
