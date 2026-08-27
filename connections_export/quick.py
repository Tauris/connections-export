"""Quick model — build an `Interchange` by fetching feeds directly, with
**no archive**.

The archive-first import (crawl → archive → derive) is the *lossless,
recoverable* path. But it is heavier than some jobs need: to render a
browser PDF of a blog you only need the list of entries and their bodies,
not a full byte-exact dump with blobs and provenance. This module gives
that lighter path so the import is not a **gatekeeper** to the rest of the
functionality (reader, PDF, package all consume an `Interchange` — they
don't care how it was built).

**Trade-off, on purpose:** a quick model is a *preview/export*, not a
capture. It resolves no asset blobs and writes no archive/provenance, so
it has no round-trip/losslessness guarantee. It reuses the same adapters
and the same normalized model, so it renders identically — just without
the archival guarantees. Use `crawl` + `derive` when you need a recoverable
archive; use this when you just want to see/print the content.

Blogs first (the browser-PDF case). Comments are off by default — the
list of entries is all the browser path needs — but `with_comments=True`
follows each entry's `rel="replies"` link and threads them (blog comments
are richer than wiki's: threaded via `thr:in-reply-to`).
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from lxml import etree

from connections_export.adapters import blogs
from connections_export.adapters.errors import AdapterError
from connections_export.config import Config
from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedComment,
    Interchange,
    Provenance,
)
from connections_export.http.client import HttpClient
from connections_export.http.results import Fetched

_ATOM = "http://www.w3.org/2005/Atom"

#: A quick fetch is interactive; cap the page-walk well above any real
#: single blog but far below a runaway (mirrors the crawler's backstop).
_MAX_PAGES = 1000


def _get_bytes(client: HttpClient, url: str) -> bytes | None:
    """The response body for `url`, or `None` on any non-2xx/failure —
    quick fetch skips what it can't get rather than aborting."""
    result = client.get(url)
    if isinstance(result, Fetched) and 200 <= result.status < 300:
        return result.content
    return None


def _with_page(url: str, *, ps: int, page: int) -> str:
    split = urlsplit(url)
    sep = "&" if split.query else ""
    query = f"{split.query}{sep}ps={ps}&pageSize={ps}&page={page}"
    return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))


def _paginate(client: HttpClient, feed_url: str, parser, page_size: int) -> list:
    """Page-walk `feed_url` (the crawler's `ps`/`page` convention) and
    concatenate what `parser` yields — stopping on a short/empty/failed
    page. No archive; nothing is stored."""
    items: list = []
    page = 1
    while page <= _MAX_PAGES:
        data = _get_bytes(client, _with_page(feed_url, ps=page_size, page=page))
        if not data:
            break
        try:
            batch = parser(data)
        except AdapterError:
            break
        items.extend(batch)
        if len(batch) < page_size:
            break
        page += 1
    return items


def _reply_hrefs(client: HttpClient, feed_url: str, page_size: int) -> list[str | None]:
    """The `rel="replies"` href per entry across all pages, in entry order
    (parse_entries_feed doesn't surface it, so read it off the raw bytes) —
    zipped with the parsed posts to reach each post's comments feed."""
    hrefs: list[str | None] = []
    page = 1
    while page <= _MAX_PAGES:
        data = _get_bytes(client, _with_page(feed_url, ps=page_size, page=page))
        if not data:
            break
        try:
            root = etree.fromstring(data)
        except etree.XMLSyntaxError:
            break
        entries = root.findall(f"{{{_ATOM}}}entry")
        if not entries:
            break
        for entry in entries:
            href = None
            for link in entry.findall(f"{{{_ATOM}}}link"):
                if link.get("rel") == "replies" and link.get("href"):
                    href = link.get("href")
                    break
            hrefs.append(href)
        if len(entries) < page_size:
            break
        page += 1
    return hrefs


def _abs(base_url: str, href: str) -> str:
    return href if href.startswith("http") else base_url.rstrip("/") + href


def _handle_from_self(self_url: str | None) -> str | None:
    if not self_url:
        return None
    marker = "/blogs/"
    idx = self_url.find(marker)
    if idx == -1:
        return None
    rest = self_url[idx + len(marker) :]
    return rest.split("/", 1)[0] or None


def quick_blog_model(
    config: Config,
    client: HttpClient,
    *,
    homepage: str = "homepage",
    with_comments: bool = False,
) -> Interchange:
    """Fetch the deployment's blogs and their entries directly into an
    `Interchange` — no archive, no derive-from-disk. Bodies come through;
    assets are left unresolved (`present=False` if referenced) since
    nothing is archived. `with_comments=True` also follows each entry's
    `rel="replies"` link and threads the comments. The result feeds the
    reader/PDF/package unchanged."""
    base = config.base_url or ""
    list_url = blogs.blogs_list_url(base_url=base, homepage=homepage)
    blog_refs = _paginate(client, list_url, blogs.parse_blogs_feed, config.page_size)

    derived_blogs: list[DerivedBlog] = []
    for ref in blog_refs:
        handle = _handle_from_self(ref.self_url) or homepage
        entries_url = ref.self_url or blogs.entries_feed_url(base_url=base, blog_uuid=ref.uuid)
        posts = _paginate(client, entries_url, blogs.parse_entries_feed, config.page_size)
        reply_hrefs = (
            _reply_hrefs(client, entries_url, config.page_size)
            if with_comments
            else [None] * len(posts)
        )

        derived_posts: dict[str, DerivedBlogPost] = {}
        post_ids: list[str] = []
        for ordinal, post in enumerate(posts):
            comments: list[DerivedComment] = []
            href = reply_hrefs[ordinal] if ordinal < len(reply_hrefs) else None
            if with_comments and href:
                raw = _paginate(
                    client, _abs(base, href), blogs.parse_entry_comments_feed, config.page_size
                )
                comments = [
                    DerivedComment(
                        id=c.id,
                        author=c.author,
                        content_html=c.content_html,
                        created=c.published,
                        parent_comment_id=c.in_reply_to,
                    )
                    for c in raw
                ]
            derived_posts[post.id] = DerivedBlogPost(
                id=post.id,
                title=post.title,
                author=post.author,
                content_html=post.content_html or "",
                created=post.published,
                modified=post.updated,
                ordinal=ordinal,
                tags=list(post.tags),
                ranks=dict(post.ranks),
                comments=comments,
                provenance=Provenance(hcl_id=post.provenance, source_url=entries_url),
            )
            post_ids.append(post.id)

        derived_blogs.append(
            DerivedBlog(
                id=ref.uuid,
                handle=handle,
                title=ref.title,
                post_ids=post_ids,
                posts=derived_posts,
            )
        )

    return Interchange(base_url=base or None, blogs=derived_blogs)
