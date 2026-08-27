"""Assembling Blogs: list feed -> per blog: entries -> per post: comments."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

from connections_export.adapters.blogs import (
    parse_blogs_feed,
    parse_entries_with_comment_urls,
    parse_entry_comments_feed,
)
from connections_export.derive.apps._shared import (
    _BLOG_ENTRIES_HANDLE_RE,
    _BLOG_ROLLER_UUID_RE,
    _community_title,
    _community_uuid,
    _feed_note,
    thread_comments,
)
from connections_export.derive.assets import resolve_body_assets, strip_avatars
from connections_export.derive.index import ArchiveIndex
from connections_export.derive.links import resolve_body_links
from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    Provenance,
)


class DeriveError(Exception):
    """Derivation could not even locate its starting point in the
    archive -- neither a `.../wikis/feed?ps=..&page=1` request nor any
    `.../wiki/{label}/nav/feed` was ever archived. Distinct from a
    per-page/per-artifact gap, which is always represented via
    `Provenance.note`, never raised."""


def _discover_direct_blog_entries(index: ArchiveIndex) -> list[tuple[str, str]]:
    """Fallback for archives that were crawled via the roller-ui entries
    feed directly (e.g. a community blog whose UUID is not listed in the
    homepage list feed, so `/blogs/homepage/feed/blogs/atom` returned a
    non-200 and was never recorded in `ok_urls`). Returns a list of
    `(base_entries_url, uuid)` pairs -- one per distinct blog UUID that
    has at least one successfully-archived roller-ui entries feed page.
    `base_entries_url` is the query-stripped form ready for
    `ArchiveIndex.paginate` with `start_page=0`.

    Also recognises the legacy handle-in-path form
    (`/blogs/{handle}/feed/entries/atom`) for archives that were crawled
    before the roller-ui URL was confirmed."""
    seen: dict[str, str] = {}  # uuid/handle -> base_entries_url
    for url in index.ok_urls():
        split = urlsplit(url)
        m = _BLOG_ROLLER_UUID_RE.search(split.path)
        if m:
            uuid = m.group("uuid")
            if uuid not in seen:
                seen[uuid] = urlunsplit((split.scheme, split.netloc, split.path, "", ""))
            continue
        m2 = _BLOG_ENTRIES_HANDLE_RE.search(split.path)
        if m2:
            handle = m2.group("handle")
            if handle not in seen:
                seen[handle] = urlunsplit((split.scheme, split.netloc, split.path, "", ""))
    return [(base_url, uid) for uid, base_url in seen.items()]


def _assemble_blog_post(
    index: ArchiveIndex,
    post,
    comments_href: str | None,
    *,
    ordinal: int,
    entries_url: str,
    page_size: int,
    base_url: str,
    hcl_hosts: Iterable[str],
) -> DerivedBlogPost:
    notes: list[str] = []
    # HCL-ism confined to provenance: the raw `<id>` LSID text (`post.
    # provenance`), never the bare uuid used as the interchange id.
    provenance = Provenance(hcl_id=post.provenance, source_url=entries_url)

    body_html = strip_avatars(post.content_html or "")
    # A blog post body is inline in the entries feed (no separate content
    # fetch, unlike a wiki page), so a relative href resolves against the
    # deployment root and same-deployment assets are judged against it too
    # -- the same `resolve_body_assets`/`resolve_body_links` calls
    # `_assemble_page` makes, just with `base_url` as the join base.
    resolved_assets = resolve_body_assets(
        body_html, page_url=base_url, base_url=base_url, hcl_hosts=hcl_hosts, index=index
    )
    resolved_links = resolve_body_links(
        body_html,
        page_url=base_url,
        self_page_id=post.id,
        base_url=base_url,
        hcl_hosts=hcl_hosts,
        page_lookup={},
    )

    comments_items: list = []
    if comments_href:
        comments_items = index.paginate(
            comments_href, parse_entry_comments_feed, page_size=page_size
        )
        note = _feed_note(index, "comments", comments_href, page_size=page_size)
        if note:
            notes.append(note)

    if notes:
        provenance.note = "; ".join(notes)

    return DerivedBlogPost(
        id=post.id,
        title=post.title,
        author=post.author,
        author_userid=post.author_userid,
        contributors=list(post.contributors),
        content_html=body_html,
        created=post.published,
        modified=post.updated,
        ordinal=ordinal,
        tags=list(post.tags),
        ranks=dict(post.ranks),
        comments=thread_comments(comments_items),
        assets=resolved_assets,
        links=resolved_links,
        provenance=provenance,
        alternate_url=post.alternate_url,
    )


def _assemble_blog(
    index: ArchiveIndex,
    blogref,
    *,
    page_size: int,
    base_url: str,
    hcl_hosts: Iterable[str],
    max_items: int | None = None,
) -> DerivedBlog | None:
    # A blog that only appeared in the list feed but whose entries feed was
    # never fetched (scoped out of the crawl -- a "one blog"/"single post"
    # import) must NOT become an empty container in the model. Its entries feed
    # (blog feeds are 0-indexed) being absent from the archive is how
    # we tell "scoped out" from "crawled, genuinely no posts" (which is kept).
    if blogref.self_url and not index.has_feed_page(
        blogref.self_url, page_size=page_size, start_page=0
    ):
        return None
    handle_match = _BLOG_ENTRIES_HANDLE_RE.search(blogref.self_url or "")
    if handle_match:
        handle = handle_match.group("handle")
    else:
        # roller-ui URL: use uuid from the URL if present
        roller_match = _BLOG_ROLLER_UUID_RE.search(blogref.self_url or "")
        handle = roller_match.group("uuid") if roller_match else None

    # Recover the blog's display title and its browser URL from the entries
    # feed's own <title> and <link rel="alternate"> when the list feed didn't
    # provide them (community blogs, direct-UUID crawls).
    title = blogref.title
    community_uuid: str | None = blogref.community_uuid
    community_title: str | None = blogref.community_title
    kind = "ideation_blog" if blogref.blog_type == "ideationblog" else "blog"
    alternate_url: str | None = None
    if blogref.self_url:
        first_page_content = index.get(blogref.self_url + "?ps=" + str(page_size) + "&page=0")
        if first_page_content is None:
            # Try without explicit page=0 query (belt-and-suspenders)
            from connections_export.crawler.engine import add_query  # noqa: PLC0415

            first_page_content = index.get(add_query(blogref.self_url, ps=page_size, page=0))
        if first_page_content is not None:
            community_uuid = _community_uuid(first_page_content.content) or community_uuid
            community_title = _community_title(first_page_content.content) or community_title
            if title is None:
                from connections_export.adapters.atom import feed_title  # noqa: PLC0415

                title = feed_title(first_page_content.content)
            # Feed-level <link rel="alternate"> is the blog's own browser URL.
            try:
                from connections_export.adapters.atom import (  # noqa: PLC0415
                    links_by_rel,
                    parse_xml,
                )

                feed_root = parse_xml(first_page_content.content, expected_root="feed")
                alt_link = links_by_rel(feed_root).get("alternate")
                if alt_link is not None:
                    alternate_url = alt_link.get("href")
            except Exception:  # noqa: BLE001
                pass
    # Fallback: construct the blog's browser URL from base_url + uuid.
    if alternate_url is None and handle:
        alternate_url = base_url.rstrip("/") + "/blogs/" + handle

    entries_url = blogref.self_url
    if not entries_url:
        # No entries feed to follow (defensive; the list feed always
        # carries a rel="self") -- an empty-but-present blog, mirroring
        # `_assemble_wiki`'s empty-wiki return when nav is missing.
        return DerivedBlog(
            id=blogref.uuid,
            handle=handle,
            title=title,
            community_uuid=community_uuid,
            community_title=community_title,
            kind=kind,
            alternate_url=alternate_url,
        )

    pairs = index.paginate(
        entries_url, parse_entries_with_comment_urls, page_size=page_size, start_page=0
    )

    posts: dict[str, DerivedBlogPost] = {}
    post_ids: list[str] = []
    for ordinal, (post, comments_href) in enumerate(pairs):
        if max_items is not None and ordinal >= max_items:
            break
        posts[post.id] = _assemble_blog_post(
            index,
            post,
            comments_href,
            ordinal=ordinal,
            entries_url=entries_url,
            page_size=page_size,
            base_url=base_url,
            hcl_hosts=hcl_hosts,
        )
        post_ids.append(post.id)

    return DerivedBlog(
        id=blogref.uuid,
        handle=handle,
        title=title,
        community_uuid=community_uuid,
        community_title=community_title,
        kind=kind,
        alternate_url=alternate_url,
        post_ids=post_ids,
        posts=posts,
    )


def _assemble_blogs(
    index: ArchiveIndex,
    *,
    blogs_feed_url: str,
    page_size: int,
    base_url: str,
    hcl_hosts: Iterable[str],
    max_items: int | None = None,
) -> list[DerivedBlog]:
    """Every blog in the archived blogs-list feed, each with its
    page-walked posts (bodies, resolved assets/links, threaded comments)."""
    blogrefs = index.paginate(blogs_feed_url, parse_blogs_feed, page_size=page_size)
    assembled = [
        _assemble_blog(
            index,
            blogref,
            page_size=page_size,
            base_url=base_url,
            hcl_hosts=hcl_hosts,
            max_items=max_items,
        )
        for blogref in blogrefs
    ]
    return [blog for blog in assembled if blog is not None]


# --------------------------------------------------------------------
# Forums assembly. The flat replies feed is
# rebuilt into a tree by `build_reply_tree`, then flattened into the
# model's id-keyed form (a `replies` dict + ordered top-level `reply_ids`
# + per-reply `child_ids`) -- the same shape `DerivedWiki` stores its
# page tree in, so an arbitrary-depth thread round-trips without nesting.
# --------------------------------------------------------------------
