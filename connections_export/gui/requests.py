"""The request bodies the console posts.

Extracted from `app.py` so the route modules can import them without importing
`app.py` itself, which would be a cycle. Nothing here has behaviour; they are
the shapes FastAPI validates against.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class _IdentifyRequest(BaseModel):
    url: str


class CommunitySelection(BaseModel):
    """One community in a run, and which of its components to capture.

    `components` holds the same `"kind:id"` strings the picker has always
    posted (`console.js` `selectedCommunityComponents`), so a single
    community's selection is unchanged -- there is simply a list of them now.
    """

    uuid: str
    title: str | None = None
    components: list[str] = Field(default_factory=list)


class _StartRequest(BaseModel):
    base_url: str | None = None
    auth_mode: str | None = None
    app: str = "wiki"  # wiki | blog | forum | community
    wiki_label: str | None = None
    blog_handle: str | None = None
    forum_uuid: str | None = None
    demo: bool = False
    #: Cap total entries/posts/topics for a preview run (None = unlimited).
    max_entries: int | None = None
    #: URL-identified scope: "all" (whole wiki/blog/forum) or "single" (one
    #: entry). With the app-specific single-entry id below, scopes the ingest
    #: to exactly what the URL named, so the archive holds only that.
    scope: str | None = None
    entry_slug: str | None = None  # blog: the single post's slug
    topic_id: str | None = None  # forum: the single thread's topic id
    #: "Capture everything I authored": when set, the served model is narrowed
    #: to this user (name or userid) -- the crawl still captures the whole
    #: wiki/blog/forum, but only content they authored/participated in (each
    #: with its full chain) is exposed. Empty/None = no filter.
    author: str | None = None
    #: Demo only: how much of the synthesized dataset the fake server reveals.
    #: `0` holds the second wave back -- the state a demonstration of extend
    #: and update starts from -- and `1` is "a week has passed". `None`, the
    #: default, reveals everything, so an ordinary demo is unchanged.
    demo_wave: int | None = None
    #: An existing archive this run adds to. Makes the run an update, and its
    #: cutoff is read from that archive's own provenance.
    into: str | None = None
    #: With `into`: which components this update is for, as `{kind, id}` --
    #: the console's per-component selection, including anything being added
    #: that the archive does not hold yet. Absent means "whatever the archive
    #: holds"; it never means "everything on the deployment".
    archive_components: list[dict] = Field(default_factory=list)
    #: During an update, ask for each item's comments even when the item
    #: itself did not change. See `Config.recheck_comments`.
    recheck_comments: bool = False
    #: Search-driven ingest: `source="search"` seeds the crawl from HCL Search
    #: hits for `search_userid` (optionally pinned to `community_uuid`) instead
    #: of walking the whole container -- each forum hit expands to its WHOLE
    #: thread, each blog to its whole entry+comments.
    source: str = "url"
    search_userid: str | None = None
    community_uuid: str | None = None
    community_components: list[str] = Field(default_factory=list)
    #: The communities this run captures, in the order the user added them.
    #: One community is the common case, not a different shape: see
    #: `routes.run.selected_communities`, which folds the two fields above
    #: into this one. Every selected community lands in the SAME archive,
    #: which is what lets `derive/crosslink.py` resolve links between them.
    communities: list[CommunitySelection] = Field(default_factory=list)
    #: Human name of what this run captures -- the community, forum, blog
    #: or wiki -- used only to name the archive directory. The console
    #: already displays it; without it a directory name can say WHERE and
    #: WHEN but never WHAT, so telling two runs apart meant opening them.
    target_label: str | None = None
    #: Politeness delay between HTTP requests, in seconds (0 = as fast as the
    #: server allows). Configurable from the setup screen for slow/rate-limited
    #: deployments.
    min_interval: float = 1.0


class _ArchiveLabelRequest(BaseModel):
    """A human's name for an archive. Empty means "stop overriding"."""

    label: str = ""


class _OpenArchiveRequest(BaseModel):
    name: str


class _PaceRequest(BaseModel):
    """A new delay for the run already in flight, in seconds."""

    min_interval: float


class _OpenExternalRequest(BaseModel):
    """An archive that lives outside the archives folder.

    `path` is whatever a drop carried: an absolute path, or the `file:///...`
    URI a file manager hands the browser instead of one.
    """

    path: str


class _ArchiveDeleteRequest(BaseModel):
    name: str
    confirmation: str


class _SelectedDeleteRequest(BaseModel):
    """Delete an explicitly chosen set. `confirmation` is the number of names,
    typed by the user: a count read off the screen proves the scope was seen,
    and unlike a fixed phrase it changes every time, so it cannot become
    muscle memory. It also catches a stale selection -- if the list moved on,
    the number no longer matches what is being sent."""

    names: list[str]
    confirmation: str


class _BulkDeleteRequest(BaseModel):
    """A bulk delete names no single archive. It reused
    `_ArchiveDeleteRequest`, whose required `name` the handler never read, so
    any caller that sensibly omitted it got an opaque validation error."""

    confirmation: str


class _BulkRepairRequest(BaseModel):
    """The archives selected for a one-shot repair from the archive list."""

    names: list[str]
    confirmation: str


class _IngestRequest(BaseModel):
    """Which developer format to reconstruct the open archive into, and how
    its page content is written (`ingest._bodies.HTML_MODES`; the format's
    own default when absent).

    `archives` names archives on the Archives screen to export instead of the
    open one -- several are combined into one export (`derive.combine`).
    `dry_run` reports what the export would hold and where it would go, and
    writes nothing. `starter_site` (Hugo only) adds the starter site beside
    `content/`, so the export can be viewed with `hugo server`."""

    format: str
    html_mode: str | None = None
    archives: list[str] | None = None
    dry_run: bool = False
    starter_site: bool = False


class _HugoPreviewRequest(BaseModel):
    """The Hugo export folder to preview: a path the console's own export
    reported."""

    path: str


class _ArchiveZipRequest(BaseModel):
    """Which archive to write out as a `.zip` beside itself."""

    name: str


class _SettingsRequest(BaseModel):
    base_url: str | None = None
    auth_mode: str = "sspi"
    min_interval: float = 1.0
    default_author_filter: str | None = None
    #: Hide the screens that need a live deployment -- Select & Tailor, the
    #: ingest console, "Extend or update", Live PDF -- leaving the archive
    #: reader, the exports and the manual.
    #:
    #: For anyone who works only with archives they already have: someone
    #: handed one to read, or keeping one for reference. A screen that can
    #: never succeed is not neutral -- it is clutter that makes the tool look
    #: broken. Purely a display choice: nothing on disk changes, and turning it
    #: off brings everything back.
    #:
    #: `None` means "not mentioned": the setting is chosen in one card while
    #: the Save buttons live in others, so a body that does not carry it must
    #: leave the stored value alone rather than reset it.
    archive_only: bool | None = None
    #: PDF design-token overrides, by token name (see `pdf.html.style_token_names`).
    pdf_style: dict[str, str] = Field(default_factory=dict)
    #: Running header/footer settings (see `connections_export.pdf.marks`).
    pdf_marks: dict[str, str] = Field(default_factory=dict)
    pdf_timeout: float = 120.0
    #: Size at or below which an external image counts as an icon in text.
    pdf_small_image_px: int = 48


class _ArchivesDirRequest(BaseModel):
    """Where archives are kept. A directory that already exists."""

    path: str = ""


class _StylePreviewRequest(BaseModel):
    """What to preview. Deliberately not the whole settings object: previewing
    is not saving, and a preview that quietly changed a stored default would be
    a nasty surprise."""

    pdf_style: dict[str, str] = Field(default_factory=dict)
    pdf_marks: dict[str, str] = Field(default_factory=dict)
