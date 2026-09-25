"""Helpers the console's routes share.

Extracted from `app.py` for the same reason as `requests.py`: a route module
that needed one of these would otherwise have to import `app.py`, which
imports the route modules.
"""

from __future__ import annotations

import datetime
import os
import re
import secrets
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from connections_export.gui.archives import (
    slugify,
)
from connections_export.gui.demo import (
    demo_synth_seed,
)
from connections_export.interchange.package import SPEC_DOC_PATH


def _resolve_archives_base(env: Mapping[str, str], cwd: Path) -> Path:
    """Resolve `ARCHIVES_BASE` from `env`/`cwd` -- factored out of
    module-import time so the resolution logic is unit-testable without
    reimporting/reloading `connections_export.gui.app`. Order:

    1. `env[ARCHIVES_DIR_ENV]` if set and non-empty -- `"."` resolves to
       `cwd` itself, anything else is used as a path verbatim.
    2. Otherwise `cwd / "connections-export-archives"` -- a user never has
       to choose a location, and running from the repo
       root during development lands archives in a gitignored dir there
       rather than scattered under the system temp dir.
    """
    raw = env.get(ARCHIVES_DIR_ENV)
    if raw:
        return cwd if raw == "." else Path(raw)
    return cwd / "connections-export-archives"


STATIC_DIR = Path(__file__).parent / "static"

CONSOLE_HTML = STATIC_DIR / "console.html"

CONSOLE_CSS = STATIC_DIR / "console.css"

CONSOLE_JS = STATIC_DIR / "console.js"

VENDOR_DIR = STATIC_DIR / "vendor"

#: Vendored, version-pinned third-party assets served verbatim (pdf.js, its
#: worker). Immutable, so unlike the console assets they are cached hard.
#: The vendored assets the console may fetch, by exact name. pdf.js is an ES
#: module from v4 onwards, so these are `.mjs` -- served as JavaScript, which
#: is what a module script and a module worker both require.
_VENDOR_FILES = {
    "pdfjs-boot.mjs": "text/javascript",
    "pdf.min.mjs": "text/javascript",
    "pdf.worker.min.mjs": "text/javascript",
}

#: Env var that overrides where run archives land. A value of `"."`
#: means "the current directory itself" -- documented shorthand rather than
#: a magic empty-string convention. Any other value is used as a path
#: verbatim (relative paths resolve against the process's cwd, same as
#: any other relative path passed to `Path`).
ARCHIVES_DIR_ENV = "CONNECTIONS_EXPORT_ARCHIVES_DIR"

#: How often `/events` re-checks whether a run has been started yet,
#: while idle (the design: "gains an idle 'no run yet' state so the
#: console waits for a start"). Short enough that a client connecting
#: just after `/api/start` sees its first event promptly; long enough
#: not to spin.
_IDLE_POLL_SECONDS = 0.02

#: Typed verbatim to authorise a bulk delete. Compared after collapsing
#: whitespace: a stray leading/trailing space is invisible, so rejecting it
#: replies "type <phrase>" to someone who did exactly that.
BULK_DELETE_CONFIRMATION = "DELETE DEMO ARCHIVES"


#: Where run archives land: resolved once at import time from
#: `_resolve_archives_base`. Each run gets its own self-describing
#: subdirectory (see `_new_run_archive_dir`). A module-level `Path` --
#: tests monkeypatch this directly (`monkeypatch.setattr(app_module,
#: "ARCHIVES_BASE",...)`) rather than re-resolving it.
ARCHIVES_BASE = _resolve_archives_base(os.environ, Path.cwd())


def _new_run_archive_dir(*, demo: bool, label: str | None = None) -> Path:
    """Create and return a fresh run-archive directory, named for what it
    holds. Names:

    - demo: ``DEMO-FAKE-DATA-<timestamp>-<slug>-<unique>``
    - real: ``export-<timestamp>-<slug>-<unique>``

    `<slug>` is what was captured -- the community, forum, blog or wiki --
    which the name never carried before: a directory said which deployment
    and when, and nothing about which of a hundred forums it held.

    The deployment host is NOT in the name. Everyone saves from one system,
    so it repeated the same string in every directory and spent length that
    the captured thing's name needs. It is still recorded, in the run's
    manifest and its `archive-summary.json`, where it can be read without
    being in every filename.

    The only distinction the NAME has to carry is demo from real, and the
    `DEMO-FAKE-DATA-` prefix already does that unmistakably.

    The four-character `<unique>` keeps two runs of the same thing in the
    same second apart. The timestamp is a filesystem label only -- it never
    feeds the deterministic synthesizer.
    """
    ARCHIVES_BASE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    head = f"DEMO-FAKE-DATA-{stamp}" if demo else f"export-{stamp}"
    slug = slugify(label)
    if slug:
        head = f"{head}-{slug}"
    # Created directly rather than via mkdtemp, which appends its own suffix
    # and would put the unique part in the wrong place.
    for _ in range(50):
        candidate = ARCHIVES_BASE / f"{head}-{secrets.token_hex(2)}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    # Fifty collisions on a four-character suffix within one second is not a
    # thing that happens; fall back rather than fail a run over it.
    return Path(tempfile.mkdtemp(prefix=f"{head}-", dir=ARCHIVES_BASE))


def _resolve_interchange_spec_path() -> Path | None:
    """Where to read the interchange-format spec from for the Manual's
    "Full interchange format specification" section: the copy shipped with
    this tool (`SPEC_DOC_PATH` -- next to the module in a wheel, under
    `docs/reference/` in a checkout). `None` if it is missing.

    Never the open archive's own `INTERCHANGE.md` (`SPEC_COPY_FILENAME`),
    although every package carries one: an archive may have come from anyone,
    and markdown passes raw HTML through, so its copy would put the archive
    author's markup into the console's own document. The Manual documents
    this tool, so this tool's copy is the right one anyway."""
    if SPEC_DOC_PATH.is_file():
        return SPEC_DOC_PATH
    return None


def _pdf_style_token_defaults() -> list[dict[str, str]]:
    """Every PDF style token with the value the stylesheet gives it.

    The default is what makes a control usable: an empty box tells you nothing
    about what you are changing from.
    """

    from connections_export.pdf.html import _STYLE, style_token_names  # noqa: PLC0415

    out = []
    for name in style_token_names():
        match = re.search(rf"--pdf-{re.escape(name)}:\s*([^;]+);", _STYLE)
        out.append({"name": name, "default": (match.group(1).strip() if match else "")})
    return out


def _community_uuid_of(ledger: dict) -> str | None:
    """The community an archive belongs to, if it names one.

    Extend only means something for a community archive: it is the thing that
    HAS components, some of which may be missing. A standalone wiki archive has
    nothing to extend with, and the ledger says so by offering nothing.
    """
    for community in ledger.get("communities") or []:
        if community.get("id"):
            return community["id"]
    return None


def _demo_subcommunities(community_uuid: str) -> list[dict[str, str]] | None:
    """`/api/subcommunities`' demo answer.

    Exists for the same reason `_demo_community_components` does: the demo
    fakeserver runs in-process (`SyncASGIBridge`) and is never reachable over
    HTTP at the placeholder host a demo chip's URL carries.

    `None` means "not a demo community" -- never a wrong guess. An empty list
    means "this one has no children", which is a different and equally real
    answer.
    """
    from connections_export.fakeserver.model import (  # noqa: PLC0415
        DEMO_CHILD_COMMUNITY_TITLE,
        DEMO_CHILD_COMMUNITY_UUID,
        DEMO_COMMUNITY_UUID,
    )

    if community_uuid == DEMO_COMMUNITY_UUID:
        return [
            {
                "kind": "community",
                "id": DEMO_CHILD_COMMUNITY_UUID,
                "title": DEMO_CHILD_COMMUNITY_TITLE,
            }
        ]
    if community_uuid == DEMO_CHILD_COMMUNITY_UUID:
        return []
    return None


def _demo_community_components(community_uuid: str) -> dict[str, Any] | None:
    """`/api/community-components`' demo-mode answer, read straight from the
    same synth data `run_demo` serves -- never an HTTP fetch, for exactly the
    reason `_demo_feed_info` exists: the demo fakeserver runs in-process
    (`SyncASGIBridge`) and is never reachable at the placeholder host
    `DEMO_SAMPLE_BASE_URL` a demo chip's URL carries.

    Rebuilds the sets with the same seed `demo_sample_urls`/`run_demo` use and
    applies `assign_community`, so the components listed are exactly the ones a
    demo ingest of that community would capture. Returns `None` for any other
    community uuid -- never a wrong guess.
    """
    try:
        from connections_export.fakeserver import (  # noqa: PLC0415
            synthesize_blogs,
            synthesize_forums,
        )
        from connections_export.fakeserver.model import (  # noqa: PLC0415
            DEMO_CHILD_COMMUNITY_TITLE,
            DEMO_CHILD_COMMUNITY_UUID,
            DEMO_COMMUNITY_TITLE,
            DEMO_COMMUNITY_UUID,
            assign_community,
        )
        from connections_export.fakeserver.prototype import (  # noqa: PLC0415
            build_prototype_wikiset,
        )
        from connections_export.fakeserver.synth import (  # noqa: PLC0415
            synthesize_files,
            synthesize_rich_content,
        )

        # The demo has a parent and one child; both are real communities
        # here, and each reports only what it actually holds.
        if community_uuid not in (DEMO_COMMUNITY_UUID, DEMO_CHILD_COMMUNITY_UUID):
            return None
        wikiset = build_prototype_wikiset()
        seed = demo_synth_seed()
        blogset = synthesize_blogs(seed)
        forumset = synthesize_forums(seed)
        fileset = synthesize_files(seed, community_uuid=community_uuid)
        rteset = synthesize_rich_content(seed, community_uuid=community_uuid)
        assign_community(
            wikiset=wikiset,
            blogset=blogset,
            forumset=forumset,
            child_uuid=DEMO_CHILD_COMMUNITY_UUID,
            child_title=DEMO_CHILD_COMMUNITY_TITLE,
        )
    except Exception:  # noqa: BLE001 - the demo answer is best-effort
        return None

    components: list[dict[str, Any]] = []
    components.extend(
        {"kind": "wiki", "id": wiki.label, "title": wiki.title, "count": len(wiki.pages)}
        for wiki in wikiset.wikis
        if wiki.community_uuid == community_uuid
    )
    components.extend(
        {
            "kind": "ideation_blog" if getattr(blog, "ideation", False) else "blog",
            "id": blog.uuid,
            "title": blog.title,
            "count": len(blog.posts),
        }
        for blog in blogset.blogs
        if blog.community_uuid == community_uuid
    )
    components.extend(
        {"kind": "forum", "id": forum.uuid, "title": forum.title, "count": len(forum.topics)}
        for forum in forumset.forums
        if forum.community_uuid == community_uuid
    )
    # Files and Highlights are synthesized FOR a community uuid rather than
    # matched against one, so they exist for any uuid asked about -- including
    # the child, whose libraries this fake server does not actually serve.
    # Offering them there would put a component in the picker that 404s on
    # capture, which is the exact failure `test_demo_community.py` guards
    # against. Only the parent has them.
    if community_uuid != DEMO_COMMUNITY_UUID:
        return {"community": DEMO_CHILD_COMMUNITY_TITLE, "components": components}

    # Files. The library is addressed BY the community uuid, so unlike the
    # other three there is nothing to match against -- if the demo community
    # has a library, it is this community's library.
    components.extend(
        {
            "kind": "files",
            "id": community_uuid,
            "title": library.title or "Files",
            "count": len(library.files),
        }
        for library in fileset.libraries
    )
    # Rich Content, addressed by the community uuid for the same reason Files
    # is: the Highlights area belongs to the community itself.
    components.extend(
        {
            "kind": "rich_content",
            "id": community_uuid,
            "title": "Highlights",
            # Same two numbers the live discovery reports: what can be
            # captured, and how many areas exist at all.
            "count": len(entry.pages),
            "placed": len(entry.pages) + entry.uninitialized,
        }
        for entry in rteset.communities
        if entry.pages
    )
    return {
        "components": components,
        "forums": [c for c in components if c["kind"] == "forum"],
        "community": DEMO_COMMUNITY_TITLE,
    }


def _demo_feed_info(target: Any) -> dict[str, Any] | None:
    """`/api/feed-info`'s demo-mode answer: the name + item count for a
    demo chip's blog/forum/wiki, read straight from the same synth data
    `run_demo`/`demo_sample_urls` build -- never an HTTP fetch. The demo
    fakeserver runs in-process (`SyncASGIBridge`), not reachable at the
    placeholder host `DEMO_SAMPLE_BASE_URL` a demo chip's URL carries,
    so the real fetch path in `feed_info` always fails for one; this is
    what makes the setup screen show the real name/count anyway.

    Rebuilds the wiki/blog/forum sets with the exact same seed
    (`demo_synth_seed`, `build_prototype_wikiset`)
    `demo_sample_urls` uses, so the ids `parse_url` extracted from a
    demo chip's URL always resolve to the entity the chip names. Falls
    back to the collection's first entry when `target` carries no
    specific id (an all-blogs/-forum URL with none to disambiguate);
    returns `None` -- never a wrong guess -- when a specific id *was*
    given but doesn't match anything in the synth data, or on any
    error, so the caller can fall back to its existing behaviour."""
    try:
        from connections_export.fakeserver import (  # noqa: PLC0415
            synthesize_blogs,
            synthesize_forums,
        )
        from connections_export.fakeserver.prototype import (  # noqa: PLC0415
            build_prototype_wikiset,
        )

        if target.app == "wiki":
            wikiset = build_prototype_wikiset()
            wiki = (
                wikiset.wiki_by_label(target.wiki_label)
                if target.wiki_label
                else (wikiset.wikis[0] if wikiset.wikis else None)
            )
            if wiki is None:
                return None
            return {"total": len(wiki.pages), "title": wiki.title, "feed_url": None}

        bf_seed = demo_synth_seed()

        if target.app == "blog":
            blogset = synthesize_blogs(bf_seed)
            blog = (
                blogset.blog_by_handle(target.blog_handle)
                if target.blog_handle
                else (blogset.blogs[0] if blogset.blogs else None)
            )
            if blog is None:
                return None
            return {"total": len(blog.posts), "title": blog.title, "feed_url": None}

        if target.app == "forum":
            forumset = synthesize_forums(bf_seed)
            forum = (
                forumset.forum_by_uuid(target.forum_uuid)
                if target.forum_uuid
                else (forumset.forums[0] if forumset.forums else None)
            )
            if forum is None:
                return None
            return {"total": len(forum.topics), "title": forum.title, "feed_url": None}
    except Exception:
        return None
    return None


#: A blob hash as the archive writes it: 64 lowercase hex characters. Anything
#: else is rejected before it reaches the filesystem -- the hash arrives in a
#: URL path.
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: Sentinel pushed onto the event queue to close an open `/events` stream. A
#: unique object, so it can never collide with a real event.
_DONE: Any = object()


def _demo_current_user() -> dict[str, Any] | None:
    """`/api/current-user`'s demo answer: who the demo is signed in as.

    Goes through the real two steps -- `profileService.do`, then
    `parse_current_user` -- against the same in-process fake the demo crawls,
    rather than returning a name from a constant. The point of a demo that
    runs the real pipeline is that the pipeline is what runs, and this is the
    identity path a real deployment always exercises: it answers who you are,
    so the demo answers it too.

    `None` on any failure, so the caller keeps its existing behaviour.
    """
    try:
        import httpx  # noqa: PLC0415

        from connections_export.adapters.directory import (  # noqa: PLC0415
            parse_current_user,
            profile_service_url,
        )
        from connections_export.fakeserver import (  # noqa: PLC0415
            synthesize_blogs,
            synthesize_forums,
        )
        from connections_export.fakeserver.app import make_app  # noqa: PLC0415
        from connections_export.fakeserver.prototype import (  # noqa: PLC0415
            build_prototype_wikiset,
        )
        from connections_export.fakeserver.synth import impersonated_person  # noqa: PLC0415
        from connections_export.gui.bridge import SyncASGIBridge  # noqa: PLC0415
        from connections_export.gui.demo import (  # noqa: PLC0415
            DEMO_SAMPLE_BASE_URL,
            demo_synth_seed,
        )

        seed = demo_synth_seed(0)
        app = make_app(
            build_prototype_wikiset(),
            blogset=synthesize_blogs(seed),
            forumset=synthesize_forums(seed),
            current_user=impersonated_person(seed),
        )
        transport = httpx.MockTransport(SyncASGIBridge(app).handle_request)
        url = profile_service_url(base_url=DEMO_SAMPLE_BASE_URL)
        with httpx.Client(transport=transport) as client:
            response = client.get(url)
        if response.status_code >= 400:
            return None
        user = parse_current_user(response.content)
        if user is None:
            return None
        return {"userid": user.userid, "name": user.name, "url": url, "demo": True}
    except Exception:  # noqa: BLE001 - a lookup degrades, never raises
        return None
