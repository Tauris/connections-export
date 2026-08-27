"""`render_html(interchange, *, blob_bytes) -> str` (render_html (the core)): the tested core of
the pdf
capability. Pure -- no browser, no filesystem, no wall clock -- an
`Interchange` in, one self-contained print-ready HTML document out.

`blob_bytes: Callable[[str], bytes | None]` is the caller's hash ->
bytes lookup (an archive's `read_blob` or a package's `blobs/`), kept
as an injected callable so this module never touches the archive/
package filesystem details itself (mirrors `interchange/package.py`'s
`open_blob` boundary).

Order, sanitization, embedding, and link classification all follow the
model as assembled by `derive` -- this module never re-derives or
re-classifies anything (it trusts `DerivedPage.assets`/`.links` were
built by scanning the same `body_html`, exactly as `derive/assets.py`
and `derive/links.py` do), it only *renders* what's already there.
"""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import Callable
from html import escape as _escape

import lxml.etree
import lxml.html

from connections_export.derive.model import (
    DerivedAttachment,
    DerivedBlog,
    DerivedBlogPost,
    DerivedComment,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    ResolvedAsset,
)

#: A fixed, non-wall-clock placeholder for the "generated" date shown
#: on the title page when the caller doesn't inject one (the design,
#: "Determinism: no wall clock; any 'generated' date is injected").
DEFAULT_GENERATED_AT = "(generation date not provided)"

#: The visible marker for an image whose bytes were never captured
#: (A not-captured image is a visible gap) -- shown, never
#: silently dropped.
NOT_CAPTURED_MARKER = "[image not captured]"
NOT_CAPTURED_ATTACHMENT_MARKER = "not captured"

BlobBytes = Callable[[str], bytes | None]

# --- content-type sniffing ---------------------------------------------
#
# `ResolvedAsset` carries no content-type (only `derive`'s pipeline saw
# the response header, and it isn't threaded through the interchange
# model) -- so the data-URI's MIME type is recovered here, from the
# blob's own magic bytes first (robust regardless of what the href
# looked like), falling back to a guess from the href's extension.

_MAGIC_SNIFFERS: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"%PDF-", "application/pdf"),
]


def _guess_content_type(href: str, data: bytes) -> str:
    for magic, content_type in _MAGIC_SNIFFERS:
        if data.startswith(magic):
            return content_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # SVG is text, so it has no binary magic and its href often lies (the demo
    # diagrams are SVG served at a `.png` URL). Sniff it the same way the
    # reader's blob endpoint does (model_source._sniff_content_type) -- without
    # this, an SVG data-URI is mislabelled image/png from the href and the
    # image renders blank in the exported PDF ("diagrams not visible"), even
    # though the reader shows it fine.
    head = data[:512].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head.lower()):
        return "image/svg+xml"
    guessed, _ = mimetypes.guess_type(href)
    return guessed or "application/octet-stream"


def _blob_digest(blob_hash: str) -> str:
    """`ResolvedAsset.blob_hash` carries the archive's `"sha256:<hex>"`
    form; `blob_bytes` is keyed by the bare hex digest (mirrors
    `interchange/package.py`'s `_blob_filename`)."""
    return blob_hash.split(":", 1)[-1]


def _data_uri(href: str, data: bytes) -> str:
    content_type = _guess_content_type(href, data)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


# --- body/comment sanitization ------------------------------------------


def _strip_scripts_and_handlers(tree: lxml.html.HtmlElement) -> None:
    """Remove every `<script>` and inline `on*=` handler (Author CSS preserved, scripts
    neutralized) -- `<style>`, `style=`,
    and classes are left untouched."""
    for script in tree.xpath("//script"):
        parent = script.getparent()
        if parent is not None:
            parent.remove(script)
    for element in tree.iter():
        for attr in list(element.attrib):
            if attr.lower().startswith("on"):
                del element.attrib[attr]


def _asset_lookup(assets: list[ResolvedAsset]) -> dict[str, ResolvedAsset]:
    lookup: dict[str, ResolvedAsset] = {}
    for asset in assets:
        lookup.setdefault(asset.original_href, asset)
        if asset.resolved_url:
            lookup.setdefault(asset.resolved_url, asset)
    return lookup


def _rewrite_images(
    tree: lxml.html.HtmlElement, assets: list[ResolvedAsset], blob_bytes: BlobBytes
) -> None:
    lookup = _asset_lookup(assets)
    for img in tree.xpath("//img"):
        src = img.get("src")
        asset = lookup.get(src) if src is not None else None
        data = None
        if asset is not None and asset.present and asset.blob_hash:
            data = blob_bytes(_blob_digest(asset.blob_hash))
        if data is not None:
            img.set("src", _data_uri(asset.original_href, data))
            continue
        # Not present, unresolved, or the blob lookup came back empty:
        # a visible marker, never a silently dropped image.
        marker = tree.makeelement("span", {"class": "hcl-missing-image"})
        marker.text = NOT_CAPTURED_MARKER
        parent = img.getparent()
        if parent is not None:
            parent.replace(img, marker)


def _link_lookup(links: list[LinkRef]) -> dict[str, LinkRef]:
    lookup: dict[str, LinkRef] = {}
    for link in links:
        lookup.setdefault(link.original_href, link)
    return lookup


def _rewrite_links(tree: lxml.html.HtmlElement, links: list[LinkRef]) -> None:
    lookup = _link_lookup(links)
    for anchor in tree.xpath("//a[@href]"):
        link = lookup.get(anchor.get("href"))
        if link is None:
            continue
        if link.scope == "in_export" and link.target_page_id:
            anchor.set("href", f"#p-{link.target_page_id}")
            continue
        # hcl_deployment/external: keep the resolved URL, and show it
        # visibly too so it survives on paper (the design, "External/
        # deployment link kept visible").
        url = link.resolved_url or link.original_href
        anchor.set("href", url)
        visible = anchor.makeelement("span", {"class": "hcl-link-url"})
        visible.text = f" ({url})"
        anchor.addnext(visible)


def _parse_fragment(html_fragment: str) -> lxml.html.HtmlElement | None:
    if not html_fragment or not html_fragment.strip():
        return None
    try:
        return lxml.html.fragment_fromstring(html_fragment, create_parent="div")
    except lxml.etree.ParserError:
        return None


def _sanitize_html(
    body_html: str,
    assets: list[ResolvedAsset],
    links: list[LinkRef],
    blob_bytes: BlobBytes,
) -> str:
    """The sanitize+embed+rewrite core shared by pages, blog posts, and
    forum topics/replies (all of which carry their own `assets`/`links`):
    scripts/handlers stripped, present images embedded as data URIs, links
    reclassified to internal anchors or kept-visible URLs."""
    tree = _parse_fragment(body_html)
    if tree is None:
        return ""
    _strip_scripts_and_handlers(tree)
    _rewrite_images(tree, assets, blob_bytes)
    _rewrite_links(tree, links)
    return lxml.html.tostring(tree, encoding="unicode")


def _sanitize_body(page: DerivedPage, blob_bytes: BlobBytes) -> str:
    return _sanitize_html(page.content_html, page.assets, page.links, blob_bytes)


def _sanitize_fragment(html_fragment: str | None) -> str:
    """Comments carry no asset/link lists of their own (`DerivedComment`
    has neither), so only script/handler neutralization applies here --
    the same "never execute authored content" discipline, scoped to
    what's actually available.

    Plain-text comment bodies (no block-level HTML, just bare `\\n`) have
    their newlines converted to `<br>` first so line breaks survive HTML
    rendering. HCL comment content often arrives this way."""
    if not html_fragment:
        return ""
    # Convert bare newlines to <br> before parsing. If the fragment already
    # has block-level elements the extra <br> is harmless whitespace.
    fragment = html_fragment.replace("\n", "<br>\n")
    tree = _parse_fragment(fragment)
    if tree is None:
        return ""
    _strip_scripts_and_handlers(tree)
    return lxml.html.tostring(tree, encoding="unicode")


# --- page walk (hierarchy + ordinal order) ----------------------------------


def _walk_pages(wiki: DerivedWiki):
    """Depth-first walk of `root_page_ids` then each page's `child_ids`
    -- both already ordered by `derive` (sibling order lives in
    `ordinal`, baked into `child_ids`/`root_page_ids` at assembly time)
    -- so this never re-sorts, it only follows the given order."""

    def _walk(page_id: str, depth: int):
        page = wiki.pages[page_id]
        yield page, depth
        for child_id in page.child_ids:
            yield from _walk(child_id, depth + 1)

    for root_id in wiki.root_page_ids:
        yield from _walk(root_id, 0)


# --- comments: continuous thread, replies indented --------------------------


def _thread_comments(comments: list[DerivedComment]) -> list[tuple[DerivedComment, int]]:
    """Group replies immediately under their parent (via
    `parent_comment_id`), recursively -- "the whole comment flow, not a
    paging widget". A comment whose `parent_comment_id`
    doesn't match any comment on the page is treated as top-level (flat
    threading is the documented fallback, the design model.py
    docstring). A defensive `seen` guard means a malformed cycle can
    never infinite-loop this walk."""
    by_id = {comment.id for comment in comments}
    children: dict[str | None, list[DerivedComment]] = {}
    for comment in comments:
        parent = comment.parent_comment_id if comment.parent_comment_id in by_id else None
        children.setdefault(parent, []).append(comment)

    ordered: list[tuple[DerivedComment, int]] = []
    seen: set[str] = set()

    def _walk(parent_id: str | None, depth: int) -> None:
        for comment in children.get(parent_id, []):
            if comment.id in seen:
                continue
            seen.add(comment.id)
            ordered.append((comment, depth))
            _walk(comment.id, depth + 1)

    _walk(None, 0)
    return ordered


def _render_comment(comment: DerivedComment, depth: int) -> str:
    author = _escape(comment.author) if comment.author else "(unknown author)"
    # Wiki comments date via `created`; blog comments via `published`.
    when_raw = getattr(comment, "created", None) or getattr(comment, "published", None)
    when = _escape(when_raw) if when_raw else ""
    body = _sanitize_fragment(comment.content_html)
    meta = f"{author}" + (f" &middot; {when}" if when else "")
    return (
        f'<div class="comment" id="comment-{_escape(comment.id)}" data-depth="{depth}" '
        f'style="margin-left: {depth * 1.5}em">'
        f'<div class="comment-meta">{meta}</div>'
        f'<div class="comment-body">{body}</div>'
        "</div>"
    )


def _render_comments(comments: list[DerivedComment]) -> str:
    if not comments:
        return ""
    threaded = _thread_comments(comments)
    rendered = "".join(_render_comment(comment, depth) for comment, depth in threaded)
    return (
        f'<div class="comments"><h3>Comments</h3><div class="comment-thread">{rendered}</div></div>'
    )


# --- attachments -------------------------------------------------------------


def _render_attachment(attachment: DerivedAttachment, blob_bytes: BlobBytes) -> str:
    filename = _escape(attachment.filename or attachment.id)
    data = None
    if attachment.asset.present and attachment.asset.blob_hash:
        data = blob_bytes(_blob_digest(attachment.asset.blob_hash))
    if data is not None:
        uri = _data_uri(attachment.asset.original_href, data)
        return f'<li><a href="{uri}" download="{filename}">{filename}</a></li>'
    marker = f'<span class="hcl-missing-attachment">[{NOT_CAPTURED_ATTACHMENT_MARKER}]</span>'
    return f"<li>{filename} {marker}</li>"


def _render_attachments(attachments: list[DerivedAttachment], blob_bytes: BlobBytes) -> str:
    if not attachments:
        return ""
    items = "".join(_render_attachment(attachment, blob_bytes) for attachment in attachments)
    return f'<div class="attachments"><h3>Attachments</h3><ul>{items}</ul></div>'


# --- table of contents ---------------------------------------------------


def _render_toc_list(wiki: DerivedWiki, page_ids: list[str]) -> str:
    items = []
    for page_id in page_ids:
        page = wiki.pages[page_id]
        title = _escape(page.title or page.label or page.id)
        children = _render_toc_list(wiki, page.child_ids) if page.child_ids else ""
        items.append(f'<li><a href="#p-{_escape(page.id)}">{title}</a>{children}</li>')
    return f"<ul>{''.join(items)}</ul>"


#: Contents rows per page. paged.js loses a fragment when it has to split one
#: long list across pages -- an entire page of entries disappeared between two
#: that rendered -- so the list is split HERE, into blocks that each fit a
#: page, and paged.js is never asked to fragment one. Conservative: a wrapped
#: two-line title costs a row, and a short last page is harmless where a
#: dropped one is not.
_TOC_ROWS_PER_PAGE = 26


def _toc_page_rows(wiki: DerivedWiki, page_ids: list[str], depth: int = 0) -> list[str]:
    """Wiki pages flattened depth-first, one row each.

    Nested `<ul>`s would make a single row render as many lines, and the
    split below counts rows -- so the hierarchy is carried by an indent
    class instead of by nesting.
    """
    rows: list[str] = []
    for page_id in page_ids:
        page = wiki.pages[page_id]
        title = _escape(page.title or page.label or page.id)
        rows.append(
            f'<li class="toc-d{min(depth, 4)}"><a href="#p-{_escape(page.id)}">{title}</a></li>'
        )
        if page.child_ids:
            rows.extend(_toc_page_rows(wiki, page.child_ids, depth + 1))
    return rows


def _render_toc(interchange: Interchange) -> str:
    """The table of contents, pre-split into page-sized blocks.

    Container names are list ITEMS, not headings: a heading between lists is a
    block of its own, and paged.js drops such a block when it lands exactly on
    a page boundary, so whichever container fell at the break lost its name
    while its entries stayed.
    """
    rows: list[str] = []

    def group(name: str) -> None:
        rows.append('<li class="toc-group">' + _escape(name) + "</li>")

    for wiki in interchange.wikis:
        group(wiki.title or wiki.label)
        rows.extend(_toc_page_rows(wiki, wiki.root_page_ids))
    for blog in interchange.blogs:
        group(blog.title or blog.handle or blog.id)
        for post_id in blog.post_ids:
            post = blog.posts.get(post_id)
            if post is None:
                continue
            rows.append(
                '<li><a href="#b-'
                + _escape(post.id)
                + '">'
                + _escape(post.title or post.id)
                + "</a></li>"
            )
    for forum in interchange.forums:
        group(forum.title or forum.id)
        for topic_id in forum.topic_ids:
            topic = forum.topics.get(topic_id)
            if topic is None:
                continue
            rows.append(
                '<li><a href="#t-'
                + _escape(topic.id)
                + '">'
                + _escape(topic.title or topic.id)
                + "</a></li>"
            )

    rows.insert(0, '<li class="toc-title">Table of contents</li>')
    blocks = [
        '<ul class="toc-list'
        + (" toc-continued" if start else "")
        + '">'
        + "".join(rows[start : start + _TOC_ROWS_PER_PAGE])
        + "</ul>"
        for start in range(0, max(len(rows), 1), _TOC_ROWS_PER_PAGE)
    ]
    # The "Table of contents" title is the first ROW of the first block, not a
    # standalone heading: a heading of its own is a block paged.js can drop at
    # a page boundary -- which it did, taking the title with it and leaving a
    # blank page where it had been.
    return '<nav id="toc">' + "".join(blocks) + "</nav>"


# --- page sections -------------------------------------------------------


_MAX_HEADING_LEVEL = 6


def _heading_level(depth: int) -> int:
    return min(depth + 1, _MAX_HEADING_LEVEL)


def _render_page_section(
    page: DerivedPage, depth: int, blob_bytes: BlobBytes, *, include_comments: bool = True
) -> str:
    level = _heading_level(depth)
    title = _escape(page.title or page.label or page.id)
    tags = _render_tags(page.tags)
    body = _sanitize_body(page, blob_bytes)
    comments = _render_comments(page.comments) if include_comments else ""
    attachments = _render_attachments(page.attachments, blob_bytes)
    # Every wiki page starts on a fresh PDF page (not just depth-0 roots): a
    # subpage is its own document to the reader, so it earns its own page.
    page_break_class = " page-break"
    section_id = _escape(page.id)
    return (
        f'<section id="p-{section_id}" class="hcl-page{page_break_class}" data-depth="{depth}">'
        f'<h{level} class="pdf-sectitle">{title}</h{level}>'
        f"{tags}"
        f'<div class="page-body">{body}</div>'
        f"{comments}{attachments}"
        "</section>"
    )


def _render_wiki(wiki: DerivedWiki, blob_bytes: BlobBytes, *, include_comments: bool = True) -> str:
    heading = _escape(wiki.title or wiki.label)
    sections = "".join(
        _render_page_section(page, depth, blob_bytes, include_comments=include_comments)
        for page, depth in _walk_pages(wiki)
    )
    return f'<section class="hcl-wiki"><h1>{heading}</h1>{sections}</section>'


# --- blogs & forums ----------------------------------------------------------
#
# Blog posts and forum topics/replies each carry their own `content_html`
# plus `assets`/`links` (same shape as a page body), so they share the page
# sanitize+embed+rewrite core (`_sanitize_html`). `ScopeBody` is the seam
# Path B uses to scope each entry's author CSS to its own section; Path A
# passes `_identity_scope` (no scoping), so both paths render the same
# structure from one set of functions.

#: (sanitized_html, scope_id) -> html. Path A: identity. Path B: CSS scoping.
ScopeBody = Callable[[str, str], str]


def _identity_scope(html: str, _scope: str) -> str:
    return html


def _render_flags(flags: list[str]) -> str:
    if not flags:
        return ""
    labels = ", ".join(_escape(flag) for flag in flags)
    return f'<div class="hcl-flags">{labels}</div>'


def _render_tags(tags: list[str]) -> str:
    """A page's / post's tags as a row of chips. Empty -> nothing."""
    if not tags:
        return ""
    chips = "".join(f'<span class="hcl-tag">{_escape(tag)}</span>' for tag in tags)
    return f'<div class="hcl-tags">{chips}</div>'


def _render_entry_meta(author: str | None, when: str | None) -> str:
    who = _escape(author) if author else "(unknown author)"
    date = _escape(when) if when else ""
    text = who + (f" &middot; {date}" if date else "")
    return f'<div class="entry-meta">{text}</div>'


def _render_blog_post(
    post: DerivedBlogPost,
    blob_bytes: BlobBytes,
    scope_body: ScopeBody = _identity_scope,
    *,
    include_comments: bool = True,
) -> str:
    section_id = _escape(post.id)
    title = _escape(post.title or post.id)
    meta = _render_entry_meta(post.author, post.created)
    tags = _render_tags(post.tags)
    body = scope_body(
        _sanitize_html(post.content_html, post.assets, post.links, blob_bytes),
        f"#b-{section_id}",
    )
    comments = _render_comments(post.comments) if include_comments else ""
    return (
        f'<section id="b-{section_id}" class="hcl-post page-break">'
        f'<h2 class="pdf-sectitle">{title}</h2>{meta}{tags}'
        f'<div class="post-body">{body}</div>'
        f"{comments}"
        "</section>"
    )


def _render_blog(
    blog: DerivedBlog,
    blob_bytes: BlobBytes,
    scope_body: ScopeBody = _identity_scope,
    *,
    include_comments: bool = True,
) -> str:
    heading = _escape(blog.title or blog.handle or blog.id)
    posts = "".join(
        _render_blog_post(
            blog.posts[post_id], blob_bytes, scope_body, include_comments=include_comments
        )
        for post_id in blog.post_ids
        if post_id in blog.posts
    )
    return f'<section class="hcl-blog"><h1>{heading}</h1>{posts}</section>'


def _render_forum_reply(
    reply: DerivedForumReply, depth: int, blob_bytes: BlobBytes, scope_body: ScopeBody
) -> str:
    reply_id = _escape(reply.id)
    meta = _render_entry_meta(reply.author, reply.created)
    flags = _render_flags(reply.flags)
    body = scope_body(
        _sanitize_html(reply.content_html or "", reply.assets, reply.links, blob_bytes),
        f"#r-{reply_id}",
    )
    return (
        f'<div class="forum-reply" id="r-{reply_id}" data-depth="{depth}" '
        f'style="margin-left: {depth * 1.5}em">'
        f"{meta}{flags}"
        f'<div class="reply-body">{body}</div>'
        "</div>"
    )


def _render_reply_thread(
    topic: DerivedForumTopic, blob_bytes: BlobBytes, scope_body: ScopeBody
) -> str:
    """Render the id-keyed reply tree depth-first: each top-level
    `reply_id`, then recursively each reply's `child_ids`, indenting by
    depth. A `seen` guard means a malformed cycle can never infinite-loop,
    mirroring `_thread_comments`."""
    if not topic.reply_ids:
        return ""
    seen: set[str] = set()

    def _walk(reply_id: str, depth: int) -> str:
        if reply_id in seen or reply_id not in topic.replies:
            return ""
        seen.add(reply_id)
        reply = topic.replies[reply_id]
        rendered = _render_forum_reply(reply, depth, blob_bytes, scope_body)
        children = "".join(_walk(child_id, depth + 1) for child_id in reply.child_ids)
        return rendered + children

    body = "".join(_walk(reply_id, 0) for reply_id in topic.reply_ids)
    return f'<div class="forum-replies"><h3>Replies</h3>{body}</div>'


def _render_forum_topic(
    topic: DerivedForumTopic, blob_bytes: BlobBytes, scope_body: ScopeBody = _identity_scope
) -> str:
    section_id = _escape(topic.id)
    title = _escape(topic.title or topic.id)
    meta = _render_entry_meta(topic.author, topic.created)
    flags = _render_flags(topic.flags)
    body = scope_body(
        _sanitize_html(topic.content_html, topic.assets, topic.links, blob_bytes),
        f"#t-{section_id}",
    )
    replies = _render_reply_thread(topic, blob_bytes, scope_body)
    return (
        f'<section id="t-{section_id}" class="hcl-topic page-break">'
        f'<h2 class="pdf-sectitle">{title}</h2>{meta}{flags}'
        f'<div class="topic-body">{body}</div>'
        f"{replies}"
        "</section>"
    )


def _human_size(size: int | None) -> str:
    """`148213` -> `145 KB`. A byte count is data; a size is information."""
    if not size:
        return ""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _bytes_note(item, captured: bool) -> str:
    if captured:
        return "in package"
    if getattr(item, "excluded_by_author_filter", False):
        return "outside the filter"
    return NOT_CAPTURED_ATTACHMENT_MARKER


def _render_file_library(library, blob_bytes: BlobBytes) -> str:
    """A community's files, as a listing.

    Deliberately a table and not an attempt to render content. A PDF cannot show
    a spreadsheet or a zip, and pretending otherwise would be worse than saying
    plainly what the archive holds -- so this is the inventory: what is in the
    library, how big, which version, in which folder, and by whom. The documents
    themselves are in the package under `files/`.
    """
    heading = _escape(library.title or "Files")
    folder_names = {f.id: (f.name or f.id) for f in library.folders}
    rows = []
    for file_id in library.file_ids:
        item = library.files.get(file_id)
        if item is None:
            continue
        folders = ", ".join(sorted(folder_names.get(fid, fid) for fid in item.folder_ids))
        captured = item.asset is not None and item.asset.present
        rows.append(
            "<tr>"
            f"<td>{_escape(item.name or item.id)}</td>"
            f'<td class="hcl-file-meta">{_escape(folders)}</td>'
            f'<td class="hcl-file-meta">{_escape(_human_size(item.size))}</td>'
            f'<td class="hcl-file-meta">{_escape(item.version_label or "")}</td>'
            f'<td class="hcl-file-meta">{_escape(item.author or "")}</td>'
            # Said per row, because "this document is listed but its bytes were
            # never captured" is exactly the thing a reader must not have to
            # guess at -- and a document left out by an author filter is a
            # different statement from one that would not come down.
            f'<td class="hcl-file-meta">{_bytes_note(item, captured)}</td>'
            "</tr>"
        )
    if not rows:
        return ""
    return (
        '<section class="hcl-files">'
        f"<h1>{heading}</h1>"
        '<table class="hcl-file-table">'
        "<thead><tr><th>Name</th><th>Folder</th><th>Size</th>"
        "<th>Version</th><th>Author</th><th>Bytes</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        '<p class="hcl-file-note">The documents themselves are in the package '
        "under <code>files/</code>, named as listed here.</p>"
        "</section>"
    )


def _render_forum(
    forum: DerivedForum, blob_bytes: BlobBytes, scope_body: ScopeBody = _identity_scope
) -> str:
    heading = _escape(forum.title or forum.id)
    topics = "".join(
        _render_forum_topic(forum.topics[topic_id], blob_bytes, scope_body)
        for topic_id in forum.topic_ids
        if topic_id in forum.topics
    )
    return f'<section class="hcl-forum"><h1>{heading}</h1>{topics}</section>'


def _render_rich_content_page(
    page, blob_bytes: BlobBytes, scope_body: ScopeBody = _identity_scope
) -> str:
    """One Highlights page. Unlike a file, this HAS a body worth rendering --
    it is prose, tables and images, the same as a wiki page."""
    section_id = _escape(page.id)
    title = _escape(page.title or page.resource_id)
    meta = _render_entry_meta(page.author, page.created)
    version = (
        f'<span class="hcl-rc-version">version {_escape(page.version_label)}</span>'
        if page.version_label
        else ""
    )
    body = scope_body(
        _sanitize_html(page.content_html or "", page.assets, page.links, blob_bytes),
        f"#rc-{section_id}",
    )
    return (
        f'<section id="rc-{section_id}" class="hcl-rc-page page-break">'
        f'<h2 class="pdf-sectitle">{title}</h2>{meta}{version}'
        f'<div class="rc-body">{body}</div>'
        "</section>"
    )


def _render_rich_content(
    highlights, blob_bytes: BlobBytes, scope_body: ScopeBody = _identity_scope
) -> str:
    """A community's Highlights area.

    Carries a note when the community had widgets nobody ever wrote in. That
    difference exists in the source system and is invisible in the output
    otherwise -- a reader counting three pages has no way to know a fourth
    space was set aside and left empty.
    """
    if not highlights.page_ids:
        return ""
    heading = _escape(highlights.title or "Highlights")
    pages = "".join(
        _render_rich_content_page(highlights.pages[page_id], blob_bytes, scope_body)
        for page_id in highlights.page_ids
        if page_id in highlights.pages
    )
    empty = highlights.placed - highlights.initialized
    note = (
        f'<p class="hcl-rc-note">{empty} further rich content '
        f"{'area was' if empty == 1 else 'areas were'} placed on this community "
        "but never written in.</p>"
        if empty > 0
        else ""
    )
    return f'<section class="hcl-rc"><h1>{heading}</h1>{note}{pages}</section>'


# --- print CSS -------------------------------------------------------------


# Note on the footer: there is deliberately no CSS `@page { @bottom-right
# { content: string(sectitle) " · " counter(page); } }` footer here, keyed
# off `.pdf-sectitle`'s `string-set`. Chromium's print-to-PDF engine does
# NOT implement CSS named strings (`string`/`string-set`) in `@page`
# margin boxes, so such a footer renders silently empty -- no page number,
# no name. The real footer (page number + document title) is wired
# through Chromium's own `page.pdf(display_header_footer=...,...)`
# mechanism in `pdf/browser.py`'s `html_to_pdf` -- see that module for the
# `footer_template`. `.pdf-sectitle` stays on the heading markup (kept as
# a semantic marker for a possible future paged-media engine -- WeasyPrint,
# Prince -- that *does* support `string`/`target-counter`, which would
# also be what's needed for per-section running footer names and TOC "p.
# N" page numbers; neither is achievable with Chromium's print engine
# today), but the CSS rule that tried to use it for a footer is gone.
#: Token names a reader may override, without the `--pdf-` prefix. Derived from
#: the stylesheet itself so the two cannot drift: add a token below and it is
#: settable, with no second list to remember.
def style_token_names() -> list[str]:
    """Every `--pdf-*` token the stylesheet defines, without the prefix."""
    import re  # noqa: PLC0415

    return sorted({m.group(1) for m in re.finditer(r"--pdf-([a-z0-9-]+):", _STYLE)})


def style_overrides_css(overrides: dict[str, str]) -> str:
    """A `:root` block setting the named tokens, or "" for no overrides.

    Unknown names are a mistake worth reporting: a silently ignored setting is
    indistinguishable from one that did not work.
    """
    if not overrides:
        return ""
    # TOML keys read better with underscores (`body_size`), CSS custom
    # properties use hyphens. Accept either rather than making that a thing
    # anyone has to remember.
    overrides = {name.replace("_", "-"): value for name, value in overrides.items()}
    known = set(style_token_names())
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ValueError(
            f"unknown PDF style token(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}"
        )
    body = " ".join(f"--pdf-{name}: {value};" for name, value in sorted(overrides.items()))
    return f":root {{ {body} }}"


#: The design tokens are the supported surface for changing how a PDF looks.
#: Everything below is expressed in terms of them, so a reader can override a
#: handful of names without depending on our rule structure -- and so we can
#: restyle the internals without breaking anyone. `connections-export style
#: --dump` writes this stylesheet out, tokens first, for editing.
_STYLE = """
:root {
  /* Type */
  --pdf-font: sans-serif;
  --pdf-body-size: 10.5pt;
  --pdf-line-height: 1.4;
  /* Headings inside captured content. Chosen for A4 at the body size above;
     without these the browser's screen defaults apply (2em/1.5em/1.17em),
     which is how an h1 ended up at 24pt. */
  --pdf-h1-size: 17pt;
  --pdf-h2-size: 13.5pt;
  --pdf-h3-size: 11.5pt;
  /* Secondary text: link URLs, bylines, tag chips, comment headers. */
  --pdf-meta-size: 8.5pt;
  /* Contents list */
  --pdf-toc-title-size: 15pt;
  --pdf-toc-group-size: 11.5pt;
  --pdf-toc-entry-size: 10pt;
  --pdf-toc-indent: 1.2em;
  /* Colour */
  --pdf-text: #111;
  --pdf-muted: #444;
  --pdf-rule: #cbd2dc;
}
body {
  font-family: var(--pdf-font);
  font-size: var(--pdf-body-size);
  line-height: var(--pdf-line-height);
}
/* Headings in captured bodies. Scoped so the cover and TOC keep their own
   sizes, and so an author's own heading rules still win inside their page. */
.page-body h1, .post-body h1, .topic-body h1,
.hcl-wiki > h1, .hcl-blog > h1, .hcl-forum > h1 { font-size: var(--pdf-h1-size); }
.page-body h2, .post-body h2, .topic-body h2 { font-size: var(--pdf-h2-size); }
.page-body h3, .post-body h3, .topic-body h3 { font-size: var(--pdf-h3-size); }
/* --- Contents list -------------------------------------------------------
   These rules used to live only in the paged-media stylesheet, so the
   ordinary renderer produced a contents list with no hierarchy at all: its
   heading, the container names and every entry at the body size. They belong
   here, where both renderers see them; only the page-number machinery is
   specific to the paged one. */
#toc { color: var(--pdf-text); }
#toc ul { list-style: none; margin: 0; padding: 0; }
#toc a { text-decoration: none; color: inherit; }
#toc .toc-title {
  font-size: var(--pdf-toc-title-size); font-weight: 700; margin: 0 0 0.6em;
}
#toc .toc-group {
  font-size: var(--pdf-toc-group-size); font-weight: 700; margin-top: 0.9em;
  break-after: avoid;
}
#toc li { font-size: var(--pdf-toc-entry-size); line-height: 1.6; }
#toc .toc-d1 { padding-left: var(--pdf-toc-indent); }
#toc .toc-d2 { padding-left: calc(var(--pdf-toc-indent) * 2); }
#toc .toc-d3 { padding-left: calc(var(--pdf-toc-indent) * 3); }
/* Print safety: a PDF page can't scroll, so nothing may exceed the page's
   content box (paged.js/@page reserves the margins). Force wide content --
   images/media, preformatted text, tables, and long unbroken tokens/URLs --
   to wrap or scale down to the available width instead of being clipped off
   the right edge. Scoped to the captured author bodies so our own chrome
   (title page, TOC) is untouched. */
img, svg, video, canvas, iframe, object, embed {
  max-width: 100% !important; height: auto;
}
.page-body, .post-body, .topic-body, .reply-body, .comment-body {
  overflow-wrap: break-word; word-break: break-word;
}
.page-body *, .post-body *, .topic-body *, .reply-body *, .comment-body * {
  max-width: 100%; box-sizing: border-box;
}
.page-body pre, .post-body pre, .topic-body pre, .reply-body pre, .comment-body pre {
    margin: 0.75em 0 !important; padding: 0.6em !important;
    height: auto !important; min-height: 0 !important; max-height: none !important;
    break-inside: auto !important; page-break-inside: auto !important;
    white-space: pre-wrap !important; overflow-wrap: anywhere; word-break: break-word;
}
.page-body code, .post-body code, .topic-body code, .reply-body code, .comment-body code {
    max-width: 100%; height: auto !important; min-height: 0 !important;
    white-space: pre-wrap !important; overflow-wrap: anywhere; word-break: break-word;
}
.page-body table, .post-body table, .topic-body table,
.reply-body table, .comment-body table {
  table-layout: fixed; border-collapse: collapse;
}
.page-body td, .page-body th, .post-body td, .post-body th, .topic-body td,
.topic-body th, .reply-body td, .reply-body th, .comment-body td, .comment-body th {
  overflow-wrap: anywhere; word-break: break-word;
}
.hcl-link-url { word-break: break-all; }
/* Files: an inventory, not a rendering. */
.hcl-file-table { width: 100%; border-collapse: collapse; margin: 0.6em 0; }
.hcl-file-table th, .hcl-file-table td {
  text-align: left; padding: 0.25em 0.5em; border-bottom: 1px solid var(--pdf-rule);
  vertical-align: top;
}
.hcl-file-table th { font-size: var(--pdf-meta-size); color: var(--pdf-muted); }
.hcl-file-meta { font-size: var(--pdf-meta-size); color: var(--pdf-muted); white-space: nowrap; }
.hcl-file-note { font-size: var(--pdf-meta-size); color: var(--pdf-muted); }
.hcl-missing-image, .hcl-missing-attachment { color: #a00; font-style: italic; }
.hcl-link-url { font-size: var(--pdf-meta-size); color: var(--pdf-muted); }
/* Tag chips (wiki pages + blog posts). */
.hcl-tags { margin: 0.2em 0 0.6em; }
.hcl-tag {
  display: inline-block; font-size: var(--pdf-meta-size); color: #334; background: #eef1f6;
  border: 1px solid #d2d8e2; border-radius: 10px; padding: 0.05em 0.6em;
  margin: 0.15em 0.3em 0.15em 0;
}
/* Threaded/indented discussion view -- blog/wiki comments AND forum replies
   share it, so a forum reply thread is indented with the same connector line as
   a comment thread (per-level indent comes from an inline margin-left). */
.comment, .forum-reply {
  border-left: 2px solid var(--pdf-rule); padding-left: 0.6em; margin-top: 0.5em;
}
.comment-meta, .forum-reply .entry-meta {
  font-weight: bold; font-size: var(--pdf-meta-size); color: #333;
}
.comments > h3, .forum-replies > h3 {
  font-size: 0.95em; margin: 1em 0 0.3em; padding-bottom: 2px;
  border-bottom: 1px solid var(--pdf-rule);
}
@media print {
  .page-break { break-before: page; }
  /* Each wiki, blog and forum starts its own sheet. Without this a
     container's <h1> was stranded at the foot of the previous page, because
     the first item inside it carries `.page-break` and jumped to the next
     one -- a heading alone at the bottom, then its content overleaf. */
  .hcl-wiki, .hcl-blog, .hcl-forum, .hcl-files, .hcl-rc { break-before: page; }
  .hcl-wiki > h1, .hcl-blog > h1, .hcl-forum > h1, .hcl-files > h1,
  .hcl-rc > h1 { break-after: avoid; }
  /* The first item inside a container must NOT break again: its container
     just started a page, so breaking would leave that page empty but for the
     heading. */
  .hcl-wiki > .page-break:first-of-type,
  .hcl-blog > .page-break:first-of-type,
  .hcl-forum > .page-break:first-of-type { break-before: auto; }
  * { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
  @page {
    size: A4;
  }
}
"""


# --- top-level assembly ------------------------------------------------------


def _document_title(interchange: Interchange) -> str:
    """The document `<title>` -- rendered into the PDF footer by
    Chromium's `class="title"` template class (`pdf/browser.py`'s
    `_FOOTER_TEMPLATE`), so this is the one "name" the footer can
    actually carry. Per-section running names (the footer showing which
    wiki page/post/topic a given printed page came from) aren't
    achievable with Chromium's print engine -- see the `_STYLE` comment
    above -- so a single, export-level name is what "footer with a name"
    means here. Includes `base_url` when the interchange carries one, so
    the footer names *which* deployment this export came from."""
    if interchange.base_url:
        return f"HCL Connections Export — {interchange.base_url}"
    return "HCL Connections Export"


def _export_heading(interchange: Interchange) -> str:
    """The title-page heading, named for what the export actually contains --
    so a forum export doesn't say "Wiki Export"."""
    apps = []
    if interchange.wikis:
        apps.append("Wiki")
    if interchange.blogs:
        apps.append("Blog")
    if interchange.forums:
        apps.append("Forum")
    return f"HCL {apps[0]} Export" if len(apps) == 1 else "HCL Connections Export"


def _pluralize(count: int, noun: str) -> str:
    """`1 wiki` / `3 wikis`, and `1 file library` / `3 file libraries`.

    A plain trailing `s` covered every noun on the cover until `library`
    arrived and produced "3 file librarys". A consonant before a final `y`
    takes `-ies`; a vowel does not (`day` -> `days`).
    """
    if count == 1:
        return f"{count} {noun}"
    if noun.endswith("y") and len(noun) > 1 and noun[-2] not in "aeiou":
        return f"{count} {noun[:-1]}ies"
    return f"{count} {noun}s"


#: Inline, because both render paths (`html.py` and `browser_fidelity.py`)
#: share `_render_title_page` and neither `_STYLE` block defines
#: `#title-page` -- styling it here reaches both without duplicating a rule
#: into each. `page-break-after` matters most: without it the cover ran
#: straight into the first section instead of standing as its own page.
_COVER_STYLE = (
    "page-break-after: always; text-align: center; display: flex; "
    "flex-direction: column; align-items: center; justify-content: center; "
    # 24cm plus the page margins exceeded an A4 text block, so the cover
    # spilled a few millimetres onto a second sheet and produced a blank
    # page 2. 20cm fills the page without reaching its edge.
    "min-height: 20cm;"
)
_COVER_NAMES_STYLE = "margin: 0.4em 0 0; font-size: 1.05em; color: #333;"
_COVER_COUNTS_STYLE = "margin: 1em 0 0; font-size: 1.1em; color: #555;"
#: The metadata block sits well below the content lines; the second line
#: tucks under the first rather than repeating the gap.
_COVER_META_STYLE = "margin: 2em 0 0.15em; font-size: 0.9em; color: #666;"
_COVER_META_NEXT_STYLE = "margin: 0.15em 0 0; font-size: 0.9em; color: #666;"


def _cover_counts(interchange: Interchange) -> str:
    """The aggregate line: `1 wiki - 2 blogs - 47 pages`. The page total is
    the one figure the name lines above cannot convey, which is the reason
    this line earns its place next to them."""
    counts = []
    if interchange.wikis:
        counts.append(_pluralize(len(interchange.wikis), "wiki"))
    if interchange.blogs:
        counts.append(_pluralize(len(interchange.blogs), "blog"))
    if interchange.forums:
        counts.append(_pluralize(len(interchange.forums), "forum"))
    if interchange.file_libraries:
        counts.append(_pluralize(len(interchange.file_libraries), "file library"))
    # Each app's own leaf items, so the line still says something new on a
    # single-container export, where the container count alone would only
    # repeat the name line above it.
    for total, noun in (
        (sum(len(wiki.pages) for wiki in interchange.wikis), "page"),
        (sum(len(blog.posts) for blog in interchange.blogs), "post"),
        (sum(len(forum.topics) for forum in interchange.forums), "topic"),
        (sum(len(library.files) for library in interchange.file_libraries), "file"),
    ):
        if total:
            counts.append(_pluralize(total, noun))
    if not counts:
        return ""
    return f'<p style="{_COVER_COUNTS_STYLE}">' + " &middot; ".join(counts) + "</p>"


def _render_title_page(interchange: Interchange, generated_at: str) -> str:
    wiki_titles = ", ".join(_escape(wiki.title or wiki.label) for wiki in interchange.wikis)
    blogs = ", ".join(_escape(blog.title or blog.handle or blog.id) for blog in interchange.blogs)
    forums = ", ".join(_escape(forum.title or forum.id) for forum in interchange.forums)
    names = f' style="{_COVER_NAMES_STYLE}"'
    wikis_p = f"<p{names}>Wikis: {wiki_titles}</p>" if interchange.wikis else ""
    blogs_p = f"<p{names}>Blogs: {blogs}</p>" if interchange.blogs else ""
    forums_p = f"<p{names}>Forums: {forums}</p>" if interchange.forums else ""
    base_url = (
        f'<p style="{_COVER_META_NEXT_STYLE}">Source: {_escape(interchange.base_url)}</p>'
        if interchange.base_url
        else ""
    )
    return (
        f'<section id="title-page" style="{_COVER_STYLE}">'
        f'<h1 style="font-size: 2.4em; margin: 0; color: #111;">'
        f"{_escape(_export_heading(interchange))}</h1>"
        f"{wikis_p}{blogs_p}{forums_p}"
        f"{_cover_counts(interchange)}"
        f'<p style="{_COVER_META_STYLE}">Generated: {_escape(generated_at)}</p>'
        f"{base_url}"
        "</section>"
    )


def render_html(
    interchange: Interchange,
    *,
    blob_bytes: BlobBytes,
    generated_at: str = DEFAULT_GENERATED_AT,
    include_comments: bool = True,
    chrome: bool = True,
    style_overrides: dict[str, str] | None = None,
    extra_css: str | None = None,
) -> str:
    """Render `interchange` to one self-contained, print-ready HTML
    document (render_html (the core)). Pure: no
    browser, no filesystem, no wall clock.

    Pages appear in hierarchy + ordinal order (depth-first); each is a
    `<section id="p-{page_id}">` with its sanitized body, its comments
    inlined as a continuous thread, and its attachments. `blob_bytes`
    resolves a present asset's blob hash to bytes for `data:` URI
    embedding; a not-present or unresolved image is a visible marker,
    never a silent drop.
    """
    # `chrome=False` renders just the body sections -- no cover, no TOC -- for
    # the live per-entity tiles, where each entity is rendered on its own and
    # a repeated cover/TOC per tile would be noise. The real export keeps both.
    title_page = _render_title_page(interchange, generated_at) if chrome else ""
    toc = _render_toc(interchange) if chrome else ""
    # Comments are always ingested/archived; a caller (the PDF export) may omit
    # them from the rendered document. Forum replies are the thread body, not
    # secondary commentary, so they are never suppressed.
    body_sections = (
        "".join(
            _render_wiki(wiki, blob_bytes, include_comments=include_comments)
            for wiki in interchange.wikis
        )
        + "".join(
            _render_blog(blog, blob_bytes, include_comments=include_comments)
            for blog in interchange.blogs
        )
        + "".join(_render_forum(forum, blob_bytes) for forum in interchange.forums)
        + "".join(
            _render_rich_content(highlights, blob_bytes) for highlights in interchange.rich_content
        )
        + "".join(
            _render_file_library(library, blob_bytes) for library in interchange.file_libraries
        )
    )

    # Both go LAST, after the captured pages' own <style> blocks. Author CSS
    # is emitted inside the body and outranks anything in <head> at equal
    # specificity -- which is exactly how a sample page's `body { font: 16px }`
    # once decided the size of every export. A reader's overrides have to sit
    # after it to mean anything.
    reader_css = ""
    if style_overrides:
        reader_css += f"<style>{style_overrides_css(style_overrides)}</style>"
    if extra_css:
        reader_css += f"<style>{extra_css}</style>"

    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        f"<title>{_escape(_document_title(interchange))}</title>"
        f"<style>{_STYLE}</style>"
        "</head><body>"
        f"{title_page}{toc}{body_sections}"
        f"{reader_css}"
        "</body></html>"
    )
