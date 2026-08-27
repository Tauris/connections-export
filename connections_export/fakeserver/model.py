"""The in-memory dataset model synthesized data is built from and served
from. Plain dataclasses only — no parsing, no I/O, no wall-clock or
random-number reads here. `synth.py` is the only place that decides
*values*; this module only decides *shape*.

Field choices follow the "Dataset model" section, transcribed
from the published API reference where the reference documents a
shape, and extended with plainly-synthetic bookkeeping (e.g. `tags`,
`recommendations`) where the fake needs somewhere to hold values that
`atom.py` serializes into the (partly undocumented) response shapes.
"""

import uuid
from dataclasses import dataclass, field

#: Namespace for synthetic person ids. Fixed, so the same name yields the same
#: id on every run and across every component -- the demo's archives stay
#: byte-comparable between runs.
_PERSON_NS = uuid.UUID("6f5c9e2a-1d84-4c53-9a6e-2f0b7d5c81aa")


def person_uid(name: str) -> str:
    """The directory id the synthetic deployment credits `name` with.

    A deployment's `snx:userid` identifies the PERSON: one id, one human, the
    same wherever they appear. The fake had neither property -- every post in a
    blog carried `blogger0` whoever wrote it, and a wiki comment carried the
    author's own display name as its id -- so the author list offered two
    people wearing one id, and ids that were just names again.

    Shaped like what a real deployment stores: the published API reference
    records `snx:userid` as the directory **GUID** (e.g.
    `57017ac0-0101-102e-8b1e-f78755f7e0ed`), and says in terms that the display
    name is the thing NOT to match on. A name-derived slug would have kept
    teaching that an id is a name in disguise -- so this is opaque, and
    deterministic only so the demo is reproducible.
    """
    return str(uuid.uuid5(_PERSON_NS, name.strip().casefold()))


@dataclass
class FakeComment:
    """A page comment.

    # INFERRED: no comment entry schema is published anywhere in
    # the reference (3.0.1 through 8.0). This is a *plausible* Atom
    # entry shape (id/title/content/author/published/updated), not a
    # transcribed one. See atom.py for where it becomes XML.
    """

    uuid: str
    author: str
    content_html: str
    published: str
    updated: str
    title: str = ""


@dataclass
class FakeVersion:
    """A historical page version.

    `version_label` is as a page-entry concept
    (`<td:versionLabel>`, incremental from 1). The *version entry*
    shape returned by `?category=version` is # INFERRED.
    """

    uuid: str
    version_label: int
    author: str
    created: str
    content_html: str


@dataclass
class FakeAttachment:
    """A page attachment.

    # INFERRED: attachment *entry* contents are undocumented; we
    # only know attachments are listed via `?category=attachment` and
    # counted by `snx:rank scheme=".../attachments"` on the page entry.
    """

    uuid: str
    filename: str
    content_type: str
    size: int
    author: str
    created: str
    #: The attachment's actual bytes, served by the fakeserver's attachment
    #: route so the crawler captures a real blob (an empty default keeps older
    #: fixtures valid). Without stored bytes the capture/download mechanism
    #: can't be exercised end to end.
    content: bytes = b""


@dataclass
class FakePage:
    """A wiki page.

    `ordinal` is explicit (not derived from list position) so the
    synthesizer can assert sibling order survives a round-trip, and so
    the duplicate-ordinals nasty case can be represented directly.
    """

    uuid: str
    label: str
    title: str
    parent_uuid: str | None
    ordinal: int
    body_html: str
    created: str
    modified: str
    version_label: int
    author: str = "Fake Author"
    visibility: str = "public"
    #: Which wave of content this item belongs to (see `FakeBlogPost.wave`).
    wave: int = 0
    comments: list[FakeComment] = field(default_factory=list)
    versions: list[FakeVersion] = field(default_factory=list)
    attachments: list[FakeAttachment] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # snx:rank counters exist on the page entry; the *values* here are
    # synthesized. `comment`/`attachments`/
    # `versions` counters are derived from the lists above, not stored
    # separately, so they can never drift out of sync.
    recommendations: int = 0
    hits: int = 0
    anonymous_hits: int = 0


@dataclass
class FakeWiki:
    """A wiki (a top-level container of pages)."""

    uuid: str
    label: str
    title: str
    created: str
    modified: str
    pages: list[FakePage] = field(default_factory=list)
    #: Community membership, emitted as the `snx:communityUuid` /
    #: `snx:communityTitle` markers the derive layer groups containers by.
    #: `None` for a standalone container (the default -- most fixtures).
    community_uuid: str | None = None
    community_title: str | None = None

    def page_by_label(self, label: str) -> FakePage | None:
        for page in self.pages:
            if page.label == label:
                return page
        return None

    def page_by_uuid(self, uuid: str) -> FakePage | None:
        for page in self.pages:
            if page.uuid == uuid:
                return page
        return None

    def children_of(self, parent_uuid: str | None) -> list[FakePage]:
        """Direct children of `parent_uuid`, sorted by explicit ordinal
        (stable sort, so duplicate ordinals keep a deterministic
        relative order)."""
        kids = [p for p in self.pages if p.parent_uuid == parent_uuid]
        return sorted(kids, key=lambda p: p.ordinal)

    def top_level_pages(self) -> list[FakePage]:
        return self.children_of(None)


@dataclass
class WikiSet:
    """The full synthesized dataset: every wiki, plus a seed-derived
    timestamp used for anything requiring a single dataset-wide clock
    reading (e.g. the nav feed's `timestamp` field) — never the wall
    clock."""

    wikis: list[FakeWiki] = field(default_factory=list)
    base_timestamp_ms: int = 0

    def wiki_by_label(self, label: str) -> FakeWiki | None:
        for wiki in self.wikis:
            if wiki.label == label:
                return wiki
        return None


# --------------------------------------------------------------------
# Blogs (Stage 2 -- everything below is not documented; see
# Blogs section, and the
# matching adapter fixtures under tests/adapters/fixtures/. No literal
# XML sample exists for any Blogs shape, so these dataclasses hold only
# the field *lists* the reference documents, never a
# byte-verbatim shape.)
# --------------------------------------------------------------------


@dataclass
class FakeBlogComment:
    """A comment on a blog entry.

    # INFERRED: no comment-entry field list/sample is published (Blogs
    # "Comment threading" documents only the *mechanism*). `in_reply_to`
    # is the bare uuid of the parent comment (one level only -- Blogs
    # threading is one level); None for a top-level comment.
    """

    uuid: str
    author: str
    author_userid: str
    content_html: str
    published: str
    in_reply_to: str | None = None


@dataclass
class FakeBlogPost:
    """A blog entry (post) (Blogs "Entry shape" -- field
    list, no literal XML sample). `slug` is the entry-slug that appears
    in the per-entry comments path (`/feed/entrycomments/{slug}/atom`).
    `comments_enabled` maps to `<app:control><snx:comments enabled=>`.
    """

    uuid: str
    slug: str
    title: str
    author: str
    author_userid: str
    body_html: str
    published: str
    updated: str
    comments_enabled: bool = True
    tags: list[str] = field(default_factory=list)
    comments: list[FakeBlogComment] = field(default_factory=list)
    #: Which wave of content this item belongs to. 0 is what exists at the
    #: start; 1 arrives later, so a demo can capture, let time pass, and see an
    #: update find something. A pure function of the seed and the item's index
    #: -- never the wall clock, because determinism is load-bearing here.
    wave: int = 0

    # snx:rank counters.
    recommendations: int = 0
    hits: int = 0


@dataclass
class FakeBlog:
    """One blog (a container of posts). `handle` is the blog's handle
    used in its feed paths (`/blogs/{handle}/feed/...`)."""

    uuid: str
    handle: str
    title: str
    created: str
    modified: str
    posts: list[FakeBlogPost] = field(default_factory=list)
    #: Community membership, emitted as the `snx:communityUuid` /
    #: `snx:communityTitle` markers the derive layer groups containers by.
    #: `None` for a standalone container (the default -- most fixtures).
    community_uuid: str | None = None
    community_title: str | None = None

    def post_by_slug(self, slug: str) -> FakeBlogPost | None:
        for post in self.posts:
            if post.slug == slug:
                return post
        return None


@dataclass
class BlogSet:
    """The full synthesized Blogs dataset. `homepage` is the handle of
    the blog configured as the Blogs home page -- the only handle that
    serves the "list all blogs" feed (`/blogs/{homepage}/feed/blogs/atom`)."""

    homepage: str
    blogs: list[FakeBlog] = field(default_factory=list)
    base_timestamp_ms: int = 0

    def blog_by_handle(self, handle: str) -> FakeBlog | None:
        for blog in self.blogs:
            if blog.handle == handle:
                return blog
        return None


# --------------------------------------------------------------------
# Forums (Stage 2 -- everything below is not documented; see
# Forums section. No literal
# XML sample exists for a topic or a reply entry.)
# --------------------------------------------------------------------


@dataclass
class FakeForumReply:
    """A forum reply, held FLAT (never nested in the model -- the served
    replies feed is flat, per the reference's "the replies feed is
    FLAT" finding). `ref` is the immediate parent's bare uuid (a topic
    for a top-level reply, or another reply); `source_topic` is the
    top-level topic's bare uuid. `flags` are `sn/flags` terms (`answer`
    on replies)."""

    uuid: str
    author: str
    content_html: str
    published: str
    ref: str
    source_topic: str
    flags: list[str] = field(default_factory=list)
    #: Which wave of content this item belongs to (see `FakeBlogPost.wave`).
    wave: int = 0


@dataclass
class FakeForumTopic:
    """A forum topic. `forum_uuid` is the parent forum's bare uuid,
    surfaced on the served entry via `<thr:in-reply-to ref=>` (the TRAP:
    that is *data*, not a type signal -- type is the `sn/type` category
    term). `flags` are `sn/flags` terms (`pinned`/`locked`/`question`/
    `answered`). `replies` is the flat reply list served by the replies
    feed for this topic."""

    uuid: str
    forum_uuid: str
    title: str
    author: str
    content_html: str
    published: str
    flags: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    replies: list[FakeForumReply] = field(default_factory=list)
    #: Which wave of content this item belongs to. 0 is what exists at the
    #: start; 1 arrives later, so a demo can capture, let time pass, and see an
    #: update find something. A pure function of the seed and the item's index
    #: -- never the wall clock, because determinism is load-bearing here.
    wave: int = 0


@dataclass
class FakeForum:
    """One forum (a container of topics)."""

    uuid: str
    title: str
    created: str
    modified: str
    topics: list[FakeForumTopic] = field(default_factory=list)
    #: Community membership, emitted as the `snx:communityUuid` /
    #: `snx:communityTitle` markers the derive layer groups containers by.
    #: `None` for a standalone container (the default -- most fixtures).
    community_uuid: str | None = None
    community_title: str | None = None


@dataclass
class FakeCommunityFile:
    """One file in a synthetic community library.

    Shaped after two live captures: the fields recorded there are the fields
    served here, so the demo exercises the same parser path a real deployment
    would. The literal XML is not documented -- see adapters/files.py.
    """

    uuid: str
    name: str
    title: str
    author: str
    published: str
    updated: str
    size: int
    content_type: str
    version_label: str
    filed_in_folder: bool = False
    tags: list[str] = field(default_factory=list)
    #: The bytes. Deliberately small and real: the demo has to prove that a
    #: file is downloaded and written under its own name, not that it is big.
    body: bytes = b""
    #: Which wave of content this item belongs to (see `FakeBlogPost.wave`).
    wave: int = 0


@dataclass
class FakeCommunityFolder:
    """A folder in a synthetic library, holding file uuids."""

    uuid: str
    name: str
    file_uuids: list[str] = field(default_factory=list)


@dataclass
class FakeFileLibrary:
    """One community's library: its files, its folders, and the community it
    belongs to."""

    community_uuid: str
    library_id: str
    title: str
    files: list[FakeCommunityFile] = field(default_factory=list)
    folders: list[FakeCommunityFolder] = field(default_factory=list)


@dataclass
class FileSet:
    """The full synthesized Files dataset."""

    libraries: list[FakeFileLibrary] = field(default_factory=list)

    def library_for(self, community_uuid: str) -> FakeFileLibrary | None:
        for library in self.libraries:
            if library.community_uuid == community_uuid:
                return library
        return None


@dataclass
class FakeRichContentPage:
    """One Rich Content page on a community's Highlights area."""

    resource_id: str
    title: str
    body_html: str
    author: str
    published: str
    updated: str
    version_label: str
    #: Which wave of content this item belongs to (see `FakeBlogPost.wave`).
    wave: int = 0


@dataclass
class FakeRichContentSet:
    """A community's Rich Content: the pages, plus how many widgets were placed.

    `uninitialized` is the count of widgets an owner placed and never wrote
    into. They are real -- a live capture saw eight instances and seven
    initialized -- and they carry no resource id, so there is nothing to
    fetch. The fake serves them so the crawler's "placed vs captured"
    reporting is exercised against something rather than assumed.
    """

    community_uuid: str
    pages: list[FakeRichContentPage] = field(default_factory=list)
    uninitialized: int = 0


@dataclass
class RichContentSet:
    """The full synthesized Rich Content dataset."""

    communities: list[FakeRichContentSet] = field(default_factory=list)

    def for_community(self, community_uuid: str) -> FakeRichContentSet | None:
        for entry in self.communities:
            if entry.community_uuid == community_uuid:
                return entry
        return None


@dataclass
class ForumSet:
    """The full synthesized Forums dataset."""

    forums: list[FakeForum] = field(default_factory=list)
    base_timestamp_ms: int = 0

    def forum_by_uuid(self, uuid: str) -> FakeForum | None:
        for forum in self.forums:
            if forum.uuid == uuid:
                return forum
        return None

    def topic_by_uuid(self, uuid: str) -> FakeForumTopic | None:
        for forum in self.forums:
            for topic in forum.topics:
                if topic.uuid == uuid:
                    return topic
        return None


#: The demo dataset's single community. A community in this tool is only ever
#: a grouping -- which wiki(s), blog(s), and forum(s) belong together -- so the
#: fake needs no community documents of its own, just the membership markers
#: its containers already know how to carry.
DEMO_COMMUNITY_UUID = "b7f1c2a4-5d3e-4a91-8c26-0f4e7a91d3b8"
DEMO_COMMUNITY_TITLE = "Platform Engineering"

#:...and its one child. A set of communities is the case the exporter now has
#: to get right, and a fixture with a single community cannot exercise it: the
#: tests would describe a shape this deployment cannot produce. One level only,
#: which is what the real deployment does.
DEMO_CHILD_COMMUNITY_UUID = "9d4c1f2e-77ab-4a03-9e51-2f0b6c8d4415"
DEMO_CHILD_COMMUNITY_TITLE = "Platform Engineering — Tooling"


def assign_community(
    *,
    wikiset: WikiSet | None = None,
    blogset: BlogSet | None = None,
    forumset: ForumSet | None = None,
    uuid: str = DEMO_COMMUNITY_UUID,
    title: str = DEMO_COMMUNITY_TITLE,
    child_uuid: str | None = None,
    child_title: str | None = None,
) -> None:
    """Stamp community membership onto every container in the given sets.

    Mutates in place, and is deliberately applied by the caller that builds a
    dataset rather than by the builders themselves -- the prototype/synth
    fixtures stay standalone, which is what the great majority of tests
    expect, and only the demo opts its containers into a community.

    Every forum joins, but only the FIRST wiki and the FIRST blog do. A
    community holds at most one wiki, one blog and one ideation blog
    (`DerivedCommunity.wiki_id`/`.blog_id`/`.ideation_blog_id` are single
    fields, mirroring the real system), so stamping every wiki or blog would
    silently drop all but one. Leaving the rest standalone also gives the demo
    the more useful shape: content inside the community and content outside
    it, side by side.
    """
    wikis = list(wikiset.wikis) if wikiset else []
    blogs = list(blogset.blogs) if blogset else []
    for container in (
        *wikis[:1],
        *blogs[:1],
        *(forumset.forums if forumset else ()),
    ):
        container.community_uuid = uuid
        container.community_title = title

    # A child community, holding content of its own. Without this the demo
    # has a parent with everything and a child with nothing, which cannot
    # exercise -- or show -- what capturing a SET of communities means. The
    # last forum moves rather than being copied: a container belongs to one
    # community, and two communities claiming it would be a shape the real
    # system cannot produce.
    forums = list(forumset.forums) if forumset else []
    if child_uuid and len(forums) > 1:
        forums[-1].community_uuid = child_uuid
        forums[-1].community_title = child_title or child_uuid
