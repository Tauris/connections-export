"""`make_app(wikiset, faults=Faults) -> ASGI application` -- routes
the documented Wikis endpoints under a
configurable auth root, and wraps the result with fault injection.

The auth root (`basic` | `basic/anonymous` | `form` | `oauth`,
context roots) is baked into the route prefix at
construction time -- one `make_app` instance serves one root, so a
multi-segment root like `basic/anonymous` is just a literal path
prefix, never a dynamic route parameter.

The reference documents two distinct feed shapes that the endpoint table compresses onto a single
row (`/wiki/{wikiLabel}/feed`
described as both "Atom feed of pages" *and* category-switched
artifacts). Those are genuinely different URLs in the reference:

- `/wikis/{auth}/api/wiki/{wikiLabel}/feed` -- the wiki's pages
.
- `/wikis/{auth}/api/wiki/{wikiLabel}/page/{pageLabel}/feed?category=`
  -- one page's artifacts. `category` takes `tag`, `version`,
  `attachment` or `comment`; comments are the default when it is omitted.

This app implements both, on their respective (reference-documented)
URLs -- the own wording ("the single *page-feed* URL") matches
the second, page-scoped shape.
"""

import re
from collections.abc import Callable
from dataclasses import fields, is_dataclass, replace
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from connections_export.adapters import profiles
from connections_export.fakeserver import atom, navjson
from connections_export.fakeserver.faults import Faults
from connections_export.fakeserver.model import (
    DEMO_CHILD_COMMUNITY_TITLE,
    DEMO_CHILD_COMMUNITY_UUID,
    DEMO_COMMUNITY_UUID,
    BlogSet,
    FakeWiki,
    FileSet,
    ForumSet,
    RichContentSet,
    WikiSet,
    person_uid,
)
from connections_export.fakeserver.prototype import cover_svg, genthumb_svg

# A minimal, valid 1x1 PNG -- kept as a fallback fixture.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de000000097048597300000ec300000ec301c76fa8640000000c49444154"
    "789c6360000002000100ffff03000006000557bfabd4000000004945454e44"
    "ae426082"
)


def _thumb_index(filename: str) -> int:
    """The genThumb index encoded in an image filename (e.g. `diagram-7.png`
    -> 7); 0 when the name carries no number (the parametric bodies'
    `diagram.png`/`logo.png`)."""
    match = re.search(r"(\d+)", filename)
    return int(match.group(1)) if match else 0


def _wiki_or_404(wikiset: WikiSet, wiki_label: str) -> FakeWiki:
    wiki = wikiset.wiki_by_label(wiki_label)
    if wiki is None:
        raise HTTPException(status_code=404, detail=f"no such wiki: {wiki_label}")
    return wiki


def _page_or_404(wiki: FakeWiki, page_label: str):
    page = wiki.page_by_label(page_label)
    if page is None:
        raise HTTPException(status_code=404, detail=f"no such page: {page_label}")
    return page


def _attachment_by_uuid(wikiset: WikiSet, uuid: str):
    """The attachment with `uuid` anywhere in the wikiset, or None -- the
    attachment-content route serves its bytes by uuid, independent of which
    page it hangs off."""
    for wiki in wikiset.wikis:
        for page in wiki.pages:
            for att in page.attachments:
                if att.uuid == uuid:
                    return att
    return None


def _ps(ps: int | None, page_size: int | None) -> int | None:
    """`ps` with legacy `pageSize` fallback (reference: "Plan: send
    `ps`, fall back to `pageSize`.")."""
    return ps if ps is not None else page_size


def _effective_ps(
    ps: int | None, page_size: int | None, default_page_size: int | None
) -> int | None:
    """The `ps` actually applied to a feed request: whatever the client
    sent (`ps`, falling back to legacy `pageSize`) if it sent anything,
    else `default_page_size` (fakeserver -- a default page
    size). `default_page_size=None` preserves today's behavior
    exactly -- an omitted `ps` still means "return everything"."""
    explicit = _ps(ps, page_size)
    return explicit if explicit is not None else default_page_size


def _clamp_ps(ps: int | None, profile: profiles.AppProfile) -> int | None:
    """Clamp an effective `ps` to the app's documented ceiling
    (`AppProfile.ps_max`: Blogs 50, Forums 100). Mirrors the real
    server's "silently clamps" behavior the profile map exists to make
    explicit -- an over-max `ps` returns at most `ps_max` items, never
    more."""
    if ps is None:
        return None
    return min(ps, profile.ps_max)


class _Visibility:
    """How much of the dataset the server currently reveals.

    The dataset always holds every wave -- it is a pure function of the seed,
    and that is what makes a demo repeatable. What a REQUEST can see is
    separate, mutable state, so "a week passes on the live system" happens at a
    moment the demo chooses rather than while someone is watching.

    Hidden items are hidden everywhere: feeds, and direct fetches by id. Absent
    from a feed alone would not be enough -- anything that already knew an id
    could still reach content the demo says has not arrived.

    Revealing everything is the DEFAULT. Holding a wave back is what a demo
    of extend/update opts into, by starting the server at wave 0 -- so every
    other test and every ordinary demo sees the whole dataset, exactly as
    before this existed.
    """

    def __init__(self, wave: int | None = None) -> None:
        #: `None` means "no wave is held back".
        self.wave = wave

    def advance(self) -> int:
        self.wave = 0 if self.wave is None else self.wave + 1
        return self.wave

    def visible(self, items: list) -> list:
        if self.wave is None:
            return list(items)
        return [item for item in items if getattr(item, "wave", 0) <= self.wave]

    def hides(self, item) -> bool:
        return self.wave is not None and getattr(item, "wave", 0) > self.wave


def _build_fastapi(
    wikiset: WikiSet,
    *,
    auth_root: str,
    default_page_size: int | None,
    blogset: BlogSet | None = None,
    forumset: ForumSet | None = None,
    fileset: FileSet | None = None,
    rteset: RichContentSet | None = None,
    visible_wave: int | None = None,
    current_user: str | None = None,
) -> FastAPI:
    api = FastAPI()
    prefix = f"/wikis/{auth_root}/api"
    visibility = _Visibility(visible_wave)

    @api.post("/_demo/advance")
    def demo_advance_route():
        """Reveal the next wave of content.

        A demo-only control, and deliberately explicit: nothing advances on its
        own, so two identical requests always answer identically and a demo
        stays repeatable.
        """
        return {"visible_wave": visibility.advance()}

    @api.get("/_demo/state")
    def demo_state_route():
        return {"visible_wave": visibility.wave}

    known_people = _people_in(wikiset, blogset, forumset, fileset, rteset)

    @api.get("/profiles/atom/profileService.do")
    def profile_service_route(request: Request, userid: str = "", userId: str = ""):
        """ "Who am I", in the shape and status codes the published API
        reference gives. With no params it answers for the
        authenticated user, which for this server is whoever `make_app` was
        told it is signed in as. `401` when nobody is and no id was given,
        `400` when an id names no one -- both documented, and both worth being
        able to reproduce.

        The doc spells the parameter `userId` in one place and `userid` in
        another; the reference says to accept either, so this does.
        """
        wanted = (userid or userId or "").strip()
        if wanted:
            for name in known_people:
                if person_uid(name) == wanted:
                    return Response(
                        content=atom.profile_service_document(
                            name, base_url=str(request.base_url).rstrip("/")
                        ),
                        media_type="application/atomsvc+xml",
                    )
            raise HTTPException(status_code=400, detail="no matching user record")
        if not current_user:
            raise HTTPException(status_code=401, detail="no authenticated user")
        return Response(
            content=atom.profile_service_document(
                current_user, base_url=str(request.base_url).rstrip("/")
            ),
            media_type="application/atomsvc+xml",
        )

    @api.get(prefix + "/wikis/feed")
    def wikis_feed_route(
        request: Request,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
        sortBy: str | None = None,
    ):
        xml = atom.wikis_feed(
            wikiset,
            auth_root=auth_root,
            base_url=str(request.base_url).rstrip("/"),
            ps=_effective_ps(ps, pageSize, default_page_size),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get(prefix + "/wiki/{wiki_label}/feed")
    def wiki_pages_feed_route(
        wiki_label: str,
        request: Request,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
    ):
        wiki = _wiki_or_404(wikiset, wiki_label)
        xml = atom.wiki_pages_feed(
            wiki,
            auth_root=auth_root,
            base_url=str(request.base_url).rstrip("/"),
            ps=_effective_ps(ps, pageSize, default_page_size),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get(prefix + "/wiki/{wiki_label}/nav/feed")
    def nav_feed_route(
        wiki_label: str,
        tree: str | None = None,
        parent: str | None = None,
        acls: str | None = None,
        includeSiblings: str | None = None,
    ):
        wiki = _wiki_or_404(wikiset, wiki_label)
        data = navjson.nav_feed(
            wiki,
            tree=(tree == "true"),
            parent=parent,
            timestamp=wikiset.base_timestamp_ms,
        )
        return JSONResponse(content=data)

    @api.get(prefix + "/wiki/{wiki_label}/navigation/{page_label}/entry")
    def nav_entry_route(wiki_label: str, page_label: str, request: Request):
        wiki = _wiki_or_404(wikiset, wiki_label)
        page = _page_or_404(wiki, page_label)
        xml = atom.navigation_entry(
            wiki, page, auth_root=auth_root, base_url=str(request.base_url).rstrip("/")
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get(prefix + "/wiki/{wiki_label}/page/{page_label}/entry")
    def page_entry_route(wiki_label: str, page_label: str, request: Request):
        wiki = _wiki_or_404(wikiset, wiki_label)
        page = _page_or_404(wiki, page_label)
        xml = atom.page_entry(
            wiki, page, auth_root=auth_root, base_url=str(request.base_url).rstrip("/")
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get(prefix + "/wiki/{wiki_label}/page/{page_label}/feed")
    def artifacts_feed_route(
        wiki_label: str,
        page_label: str,
        request: Request,
        category: str | None = None,
        ps: int | None = None,
        pageSize: int | None = None,
        sO: str | None = None,
        page: int | None = None,
    ):
        wiki = _wiki_or_404(wikiset, wiki_label)
        fake_page = _page_or_404(wiki, page_label)
        xml = atom.artifacts_feed(
            wiki,
            fake_page,
            category=category,
            auth_root=auth_root,
            base_url=str(request.base_url).rstrip("/"),
            ps=_effective_ps(ps, pageSize, default_page_size),
            page_num=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get(prefix + "/wiki/{wiki_label}/page/{page_label}/media")
    def media_route(wiki_label: str, page_label: str):
        wiki = _wiki_or_404(wikiset, wiki_label)
        page = _page_or_404(wiki, page_label)
        return PlainTextResponse(content=page.body_html, media_type="text/html")

    @api.get(prefix + "/wiki/{wiki_label}/page/{page_label}/media/img/{filename}")
    def same_host_image_route(wiki_label: str, page_label: str, filename: str):
        _wiki_or_404(wikiset, wiki_label)
        # The prototype's exact genThumb gradient (indexed by the filename)
        # -- a convincing image, not a 1x1 placeholder.
        return Response(content=genthumb_svg(_thumb_index(filename)), media_type="image/svg+xml")

    # Cross-app asset fixture (the design: "the whole-HCL-deployment
    # asset case"). Not a Wikis endpoint -- a different HCL app
    # (Files) -- served here purely so the embedded cross-app `<img>`
    # in body HTML resolves to real bytes.
    @api.get("/files/basic/api/library/{library_id}/document/{document_id}/media/{filename}")
    def cross_app_image_route(library_id: str, document_id: str, filename: str):
        return Response(content=genthumb_svg(_thumb_index(filename)), media_type="image/svg+xml")

    # Attachment content: the bytes an attachment entry's rel="enclosure" points
    # at, so the crawler captures a real blob (reader download + PDF embed).
    @api.get("/wikis/attachment/{uuid}/{filename}")
    def attachment_content_route(uuid: str, filename: str):
        att = _attachment_by_uuid(wikiset, uuid)
        if att is None:
            raise HTTPException(status_code=404, detail=f"no such attachment: {uuid}")
        return Response(content=att.content, media_type=att.content_type)

    @api.get("/search/atom/mysearch/results")
    @api.get("/search/atom/search/results")
    def search_results_route(request: Request, userid: str = "", social: str = "", query: str = ""):
        """The cross-component Search person query (subset: wiki pages the
        person authored). The person comes from the `social` clause
        (`{"type":"personUserId","id":<uuid>}`) -- the real query-optional
        person filter -- or the legacy `userid` param. `query`/`page`/`scope`
        are accepted and ignored beyond the person filter (demo scope)."""
        import json as _json  # noqa: PLC0415

        from connections_export.fakeserver.searchfeed import search_results_feed  # noqa: PLC0415

        person = userid
        if social:
            try:
                clause = _json.loads(social)
                if clause.get("type") in ("personUserId", "personEmail"):
                    person = clause.get("id", "")
            except (ValueError, TypeError):
                person = userid
        xml = search_results_feed(
            wikiset, userid=person, base_url=str(request.base_url).rstrip("/"), auth_root=auth_root
        )
        return Response(content=xml, media_type="application/atom+xml")

    if blogset is not None:
        _register_blog_routes(
            api, blogset, default_page_size=default_page_size, visibility=visibility
        )
    if forumset is not None:
        _register_forum_routes(
            api, forumset, default_page_size=default_page_size, visibility=visibility
        )
    if fileset is not None:
        _register_file_routes(
            api, fileset, default_page_size=default_page_size, visibility=visibility
        )
    if rteset is not None:
        _register_rte_routes(api, rteset, visibility=visibility)
    # Unconditional: every community has children or has none, whether or not
    # it happens to hold rich content, files or a blog.
    _register_community_routes(api)

    return api


def _register_community_routes(api: FastAPI) -> None:
    """Routes about a community itself, rather than about one of its apps."""

    @api.get("/communities/service/atom/community/subcommunities")
    def subcommunities_route(request: Request, communityUuid: str = ""):
        # No 404 for an unknown community, for the same reason the widgets
        # route below returns a feed rather than one: a community with no
        # children still HAS this feed, and "no children" must not be
        # indistinguishable from "no such community" -- only one of those is
        # a reason for the picker to stop offering anything.
        children = (
            [(DEMO_CHILD_COMMUNITY_UUID, DEMO_CHILD_COMMUNITY_TITLE)]
            if communityUuid == DEMO_COMMUNITY_UUID
            else []
        )
        xml = atom.subcommunities_feed(children, base_url=str(request.base_url).rstrip("/"))
        return Response(content=xml, media_type="application/atom+xml")

    return None


# --------------------------------------------------------------------
# Blogs & Forums routes (Stage 2). Served at the real, per-app URL
# shapes the adapters' URL builders produce: Blogs handle-in-path,
# Forums query-param on a flat `/forums/atom` root. `ps` is clamped to
# each app's documented ceiling (AppProfile.ps_max).
# --------------------------------------------------------------------


def _register_blog_routes(
    api: FastAPI,
    blogset: BlogSet,
    *,
    default_page_size: int | None,
    visibility: _Visibility,
) -> None:
    def _eff_ps(ps: int | None, page_size: int | None) -> int | None:
        return _clamp_ps(_effective_ps(ps, page_size, default_page_size), profiles.BLOGS)

    def _blog_or_404(handle: str):
        blog = blogset.blog_by_handle(handle)
        if blog is None:
            raise HTTPException(status_code=404, detail=f"no such blog: {handle}")
        return blog

    @api.get("/blogs/{handle}/feed/blogs/atom")
    def blogs_list_route(
        handle: str,
        request: Request,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
    ):
        # Only the homepage handle serves the "list all blogs" feed
        # (docs: {homepage} = the configured Blogs home page handle).
        #
        # `homepage` is accepted as well as this dataset's own handle. On a
        # real deployment that literal IS the configured handle, and it is what
        # `crawler.community.discover_components` asks for -- so a fake that
        # answered only its synthesized handle left community BLOG discovery
        # with no route at all, and the demo could never exercise the code path
        # that runs against a deployment.
        if handle not in (blogset.homepage, "homepage"):
            raise HTTPException(status_code=404, detail=f"not the blogs homepage: {handle}")
        xml = atom.blogs_list_feed(
            blogset,
            base_url=str(request.base_url).rstrip("/"),
            ps=_eff_ps(ps, pageSize),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get("/blogs/{handle}/feed/entries/atom")
    def blog_entries_route(
        handle: str,
        request: Request,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
        since: str | None = None,
        sortBy: str | None = None,
    ):
        blog = _blog_or_404(handle)
        blog = replace(
            blog,
            posts=_since_filtered(visibility.visible(blog.posts), since, epoch_millis=False),
        )
        xml = atom.blog_entries_feed(
            blog,
            base_url=str(request.base_url).rstrip("/"),
            ps=_eff_ps(ps, pageSize),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    def _blog_by_uuid_or_404(uuid: str):
        blog = next((b for b in blogset.blogs if b.uuid == uuid), None)
        if blog is None:
            raise HTTPException(status_code=404, detail=f"no such blog uuid: {uuid}")
        return blog

    @api.get("/blogs/roller-ui/rendering/feed/{uuid}/entries/atom")
    def blog_roller_entries_route(
        uuid: str,
        request: Request,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
        lang: str | None = None,
        since: str | None = None,
        sortBy: str | None = None,
    ):
        """Real HCL Connections blog entries URL shape.

        `since` is RFC 3339 here --."""
        blog = _blog_by_uuid_or_404(uuid)
        blog = replace(
            blog,
            posts=_since_filtered(visibility.visible(blog.posts), since, epoch_millis=False),
        )
        xml = atom.blog_entries_feed(
            blog,
            base_url=str(request.base_url).rstrip("/"),
            ps=_eff_ps(ps, pageSize),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get("/blogs/roller-ui/rendering/feed/{uuid}/comments/atom")
    def blog_roller_comments_route(
        uuid: str,
        request: Request,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
        since: str | None = None,
        sortBy: str | None = None,
    ):
        """Every comment in one blog, filterable by date.

        Verified with `ps=150&page=0&since=...&sortBy=modified`:
        it returned exactly the comment made during the experiment, and a
        future cutoff returned an empty feed rather than an error. That is what
        makes an incremental blog update possible -- one request per blog to
        learn which discussions grew, instead of one per post to find out that
        most did not.
        """
        blog = _blog_by_uuid_or_404(uuid)
        visible = replace(blog, posts=visibility.visible(blog.posts))
        if since:
            visible = replace(
                visible,
                posts=[
                    replace(
                        post,
                        comments=_since_filtered(post.comments, since, epoch_millis=False),
                    )
                    for post in visible.posts
                ],
            )
        xml = atom.blog_all_comments_feed(
            visible,
            base_url=str(request.base_url).rstrip("/"),
            ps=_eff_ps(ps, pageSize),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get("/blogs/roller-ui/rendering/feed/{uuid}/media/atom")
    def blog_roller_media_route(
        uuid: str,
        request: Request,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
    ):
        """Blog media (attachments/images) feed."""
        blog = _blog_by_uuid_or_404(uuid)
        # Reuse entries feed for now — media feed shape is not yet modelled.
        xml = atom.blog_entries_feed(
            blog,
            base_url=str(request.base_url).rstrip("/"),
            ps=_eff_ps(ps, pageSize),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get("/blogs/{handle}/feed/entrycomments/{slug}/atom")
    def blog_comments_route(
        handle: str,
        slug: str,
        request: Request,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
    ):
        blog = _blog_or_404(handle)
        post = blog.post_by_slug(slug)
        # Hidden everywhere, not just absent from feeds: anything that already
        # knew this slug could otherwise reach content the demo says has not
        # arrived yet.
        if post is None or visibility.hides(post):
            raise HTTPException(status_code=404, detail=f"no such entry: {slug}")
        xml = atom.blog_comments_feed(
            blog,
            post,
            base_url=str(request.base_url).rstrip("/"),
            ps=_eff_ps(ps, pageSize),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get("/blogs/{handle}/resource/{slug}/{filename}")
    def blog_post_image_route(handle: str, slug: str, filename: str):
        # Serves the same-host `<img>` `_blog_post_body` embeds in every
        # synthesized post (the design, "the whole-HCL-deployment asset
        # case" -- Blogs analogue of `same_host_image_route`), so the
        # crawler's body-image capture resolves to real bytes
        # instead of a 404.
        #
        # Keyed to the POST, not the filename. Every post embeds `cover.png`,
        # which carries no digit, so `_thumb_index` returned 0 for all of them
        # and every cover in every blog was the same bytes -- one shared blob,
        # the same picture on every entry.
        blog = _blog_or_404(handle)
        post = blog.post_by_slug(slug)
        if post is None:
            return Response(
                content=genthumb_svg(_thumb_index(filename)), media_type="image/svg+xml"
            )
        index = blog.posts.index(post) + len(blog.handle)
        return Response(
            content=cover_svg(title=post.title, kicker=blog.title, index=index),
            media_type="image/svg+xml",
        )


def _since_filtered(items: list, since: str | None, *, epoch_millis: bool) -> list:
    """The items changed after `since`, in the format that feed accepts.

    The two apps disagree, and the fake reproduces the disagreement rather
    than smoothing it over: blogs take RFC 3339, forums take epoch
    MILLISECONDS. Accepting either everywhere would let the adapters' split go
    untested anywhere but against the real deployment -- the one place that
    cannot be tested.

    An unparseable value returns everything, which is what a server that does
    not understand the parameter does. That is deliberately the same outcome
    as sending a seconds value to a milliseconds feed: it silently means 1970.
    """
    if not since:
        return items
    from datetime import UTC, datetime  # noqa: PLC0415

    try:
        if epoch_millis:
            cutoff = datetime.fromtimestamp(int(since) / 1000, tz=UTC)
        else:
            cutoff = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if cutoff.tzinfo is None:
                cutoff = cutoff.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return items

    def moved_after(item) -> bool:
        stamp = getattr(item, "updated", None) or getattr(item, "published", None)
        if not stamp:
            return True
        try:
            when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return when > cutoff

    return [item for item in items if moved_after(item)]


def _register_rte_routes(api: FastAPI, rteset: RichContentSet, *, visibility: _Visibility) -> None:
    """A community's Rich Content: the widget layout, and one page each.

    Discovery and retrieval are separate endpoints in separate applications --
    `/communities/...` for the layout, `/connections/rte/...` for a page -- and
    the fake keeps them that way, because collapsing them would hide the one
    thing that makes this feature awkward.
    """

    @api.get("/communities/service/atom/community/widgets")
    def community_widgets_route(request: Request, communityUuid: str = ""):
        # No 404 for an unknown community: a community with no rich content
        # still HAS a widget layout, holding everything else. Returning a feed
        # with no RTE widgets is the honest answer, and it is the case the
        # crawler must handle without treating it as a failure.
        rte = rteset.for_community(communityUuid)
        if rte is not None:
            rte = replace(rte, pages=visibility.visible(rte.pages))
        xml = atom.community_widgets_feed(
            rte, base_url=str(request.base_url).rstrip("/"), community_uuid=communityUuid
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get("/connections/rte/community/{community_uuid}/page/{resource_id}/entry")
    def rich_content_page_route(request: Request, community_uuid: str, resource_id: str):
        rte = rteset.for_community(community_uuid)
        page = next((p for p in (rte.pages if rte else []) if p.resource_id == resource_id), None)
        if page is None:
            return Response(content="not found", media_type="text/plain", status_code=404)
        xml = atom.rich_content_page_entry(
            page, base_url=str(request.base_url).rstrip("/"), community_uuid=community_uuid
        )
        return Response(content=xml, media_type="application/atom+xml")


def _register_file_routes(
    api: FastAPI,
    fileset: FileSet,
    *,
    default_page_size: int | None,
    visibility: _Visibility,
) -> None:
    """A community's files, folders, and the bytes behind them.

    Community-scoped paths, taking the COMMUNITY uuid -- the shape a live
    capture found, not the one HCL documents.
    """

    @api.get("/files/basic/api/communitylibrary/{community_uuid}/feed")
    def community_library_route(
        request: Request,
        community_uuid: str,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
    ):
        library = fileset.library_for(community_uuid)
        if library is None:
            return Response(content="not found", media_type="text/plain", status_code=404)
        # Later waves are held back here too. Without this, a test could not
        # tell an update that re-read the library feed from one that served the
        # feed out of the archive and reported success.
        library = replace(library, files=visibility.visible(library.files))
        xml = atom.community_library_feed(
            library,
            base_url=str(request.base_url).rstrip("/"),
            ps=_clamp_ps(_effective_ps(ps, pageSize, default_page_size), profiles.FILES),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get("/files/basic/api/communitycollection/{community_uuid}/feed")
    def community_collection_route(request: Request, community_uuid: str):
        library = fileset.library_for(community_uuid)
        if library is None:
            return Response(content="not found", media_type="text/plain", status_code=404)
        xml = atom.community_collection_feed(library, base_url=str(request.base_url).rstrip("/"))
        return Response(content=xml, media_type="application/atom+xml")

    @api.get(
        "/files/basic/anonymous/api/library/{library_id}/document/{document_id}/media/{name:path}"
    )
    def file_media_route(library_id: str, document_id: str, name: str):
        """The bytes. Real ones -- the package writer has to prove it wrote a
        file under its own name, which needs something to write."""
        for library in fileset.libraries:
            if library.library_id != library_id:
                continue
            for item in library.files:
                if item.uuid == document_id:
                    return Response(content=item.body, media_type=item.content_type)
        return Response(content="not found", media_type="text/plain", status_code=404)


def _register_forum_routes(
    api: FastAPI,
    forumset: ForumSet,
    *,
    default_page_size: int | None,
    visibility: _Visibility,
) -> None:
    def _eff_ps(ps: int | None, page_size: int | None) -> int | None:
        return _clamp_ps(_effective_ps(ps, page_size, default_page_size), profiles.FORUMS)

    @api.get("/forums/html/topic")
    def forum_topic_html_route(request: Request, id: str = ""):
        """The topic's browser page -- what its `rel="alternate"` points at.

        Not decoration: the crawler fetches this to recover attachment
        filenames the Atom feed replaces with `name=blob`. Without the route
        that path was never exercised, because no topic had an address to
        fetch. The shape here is a reconstruction; a real deployment serves a
        full topic page.
        """
        for forum in forumset.forums:
            for topic in forum.topics:
                if topic.uuid == id:
                    rows = "".join(
                        f'<li><a href="{a.enclosure_href}">{a.filename or "attachment"}</a></li>'
                        for a in getattr(topic, "attachments", []) or []
                    )
                    body = (
                        f"<html><head><title>{topic.title}</title></head><body>"
                        f"<h1>{topic.title}</h1>"
                        f"{topic.content_html or ''}"
                        + (f"<ul class='attachments'>{rows}</ul>" if rows else "")
                        + "</body></html>"
                    )
                    return Response(content=body, media_type="text/html")
        return Response(content="not found", media_type="text/plain", status_code=404)

    @api.get("/forums/atom/forums")
    def forums_list_route(
        request: Request,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
    ):
        xml = atom.forums_list_feed(
            forumset,
            base_url=str(request.base_url).rstrip("/"),
            ps=_eff_ps(ps, pageSize),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get("/forums/atom/topics")
    def forum_topics_route(
        request: Request,
        forumUuid: str,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
        since: str | None = None,
        sortBy: str | None = None,
    ):
        """`since` is epoch MILLISECONDS here --, and
        deliberately a different format from Blogs."""
        forum = forumset.forum_by_uuid(forumUuid)
        if forum is None:
            raise HTTPException(status_code=404, detail=f"no such forum: {forumUuid}")
        forum = replace(
            forum,
            topics=_since_filtered(visibility.visible(forum.topics), since, epoch_millis=True),
        )
        xml = atom.forum_topics_feed(
            forum,
            base_url=str(request.base_url).rstrip("/"),
            ps=_eff_ps(ps, pageSize),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")

    @api.get("/forums/atom/topic")
    def forum_topic_route(request: Request, topicUuid: str):
        topic = forumset.topic_by_uuid(topicUuid)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"no such topic: {topicUuid}")
        xml = atom.forum_topic_entry(topic, base_url=str(request.base_url).rstrip("/"))
        return Response(content=xml, media_type="application/atom+xml")

    @api.get("/forums/atom/replies")
    def forum_replies_route(
        request: Request,
        topicUuid: str,
        ps: int | None = None,
        pageSize: int | None = None,
        page: int | None = None,
    ):
        topic = forumset.topic_by_uuid(topicUuid)
        if topic is None or visibility.hides(topic):
            raise HTTPException(status_code=404, detail=f"no such topic: {topicUuid}")
        topic = replace(topic, replies=visibility.visible(topic.replies))
        xml = atom.forum_replies_feed(
            topic,
            base_url=str(request.base_url).rstrip("/"),
            ps=_eff_ps(ps, pageSize),
            page=page,
        )
        return Response(content=xml, media_type="application/atom+xml")


class _FaultInjectingApp:
    """A pure-ASGI wrapper around the FastAPI app that applies `Faults`
    before/after the real routing, matched by exact request path.

    Deliberately not a Starlette/FastAPI middleware: a timeout fault
    must raise an exception that propagates all the way out to the
    caller (so `httpx` sees `TimeoutException`) rather than being
    caught and turned into a 500 by Starlette's `ServerErrorMiddleware`
    -- which is exactly what happens when the raise happens *outside*
    the FastAPI app object entirely, as it does here.
    """

    def __init__(self, app: FastAPI, faults: Faults):
        self._app = app
        self._faults = faults

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path: str = scope["path"]
        faults = self._faults

        if path in faults.timeout_paths:
            raise httpx.ReadTimeout(f"fakeserver: simulated timeout for {path}")

        forced_status = faults.forced_status(path)
        if forced_status is not None:
            body = f"fakeserver: forced status {forced_status} for {path}".encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": forced_status,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        if path in faults.malformed_paths:
            body = b"<feed><entry><unterminated-element"
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/atom+xml")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        needs_postprocess = path in faults.truncate_paths or path in faults.wrong_content_type
        if not needs_postprocess:
            await self._app(scope, receive, send)
            return

        messages: list[dict[str, Any]] = []

        async def _capture(message: dict[str, Any]) -> None:
            messages.append(message)

        await self._app(scope, receive, _capture)

        start = next(m for m in messages if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        status = start["status"]
        headers = dict(start["headers"])

        if path in faults.truncate_paths:
            body = body[: len(body) // 2]

        if path in faults.wrong_content_type:
            headers[b"content-type"] = faults.wrong_content_type[path].encode()

        await send(
            {"type": "http.response.start", "status": status, "headers": list(headers.items())}
        )
        await send({"type": "http.response.body", "body": body})


def _people_in(*datasets) -> list[str]:
    """Every person any of these datasets credits, in first-seen order.

    Walked generically rather than by naming each collection: authors live on
    pages, comments, versions, attachments, posts, topics, replies, documents
    and Highlights pages, and a list that goes stale the next time one of
    those grows is worse than no list -- it would answer `400 no matching
    user record` for somebody the deployment plainly knows.
    """
    seen: dict[str, None] = {}

    def walk(value, depth: int = 0) -> None:
        if depth > 6 or value is None:
            return
        if is_dataclass(value) and not isinstance(value, type):
            for field in fields(value):
                if field.name == "author":
                    name = getattr(value, field.name, None)
                    if isinstance(name, str) and name.strip():
                        seen.setdefault(name.strip(), None)
                else:
                    walk(getattr(value, field.name, None), depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item, depth + 1)

    for dataset in datasets:
        walk(dataset)
    return list(seen)


def make_app(
    wikiset: WikiSet,
    faults: Faults | None = None,
    *,
    auth_root: str = "basic",
    default_page_size: int | None = None,
    blogset: BlogSet | None = None,
    forumset: ForumSet | None = None,
    fileset: FileSet | None = None,
    rteset: RichContentSet | None = None,
    #: Hold back later waves of content: `0` reveals only the first, which is
    #: what a demo of extend/update starts from. `None` (the default) reveals
    #: everything, so nothing else changes.
    visible_wave: int | None = None,
    #: Who this server is signed in as, for `profileService.do` ("who am I").
    #: `None` means nobody is authenticated, which is a real state a
    #: deployment has and answers `401` for.
    current_user: str | None = None,
) -> Callable:
    """Build the fake Connections server for `wikiset`. Returns an ASGI
    application suitable for `httpx.ASGITransport(app=make_app(...))`.

    `default_page_size` (fakeserver -- a default page
    size): the effective `ps` applied to a feed request that omits
    `ps` (and the legacy `pageSize`) entirely. Defaults to `None`,
    which preserves today's behavior exactly -- an omitted `ps` means
    "return everything, no next link" -- so every existing test is
    unaffected.

    `blogset` / `forumset` (Stage 2): when supplied, the app also serves
    the Blogs and Forums feeds at their real, per-app URL shapes (Blogs
    handle-in-path under `/blogs/...`; Forums query-param under
    `/forums/atom/...`). Omitted by default, so the Wikis-only app is
    unchanged.
    """
    faults = faults if faults is not None else Faults()
    api = _build_fastapi(
        wikiset,
        auth_root=auth_root,
        default_page_size=default_page_size,
        blogset=blogset,
        forumset=forumset,
        fileset=fileset,
        rteset=rteset,
        visible_wave=visible_wave,
        current_user=current_user,
    )
    return _FaultInjectingApp(api, faults)
