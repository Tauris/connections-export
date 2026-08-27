"""Atom serializers: wikis feed, wiki-pages feed, page entry,
artifacts (comment/version/attachment/tag) feed, navigation entry.

Shapes are transcribed from the published API reference:

- Namespaces, the page-entry field list, and the navigation-entry
  sample are — transcribed as closely as a synthetic
  fixture allows.
- Comment/version/attachment *entry* contents are undocumented
  anywhere in the reference; those shapes are `# INFERRED`
  and marked at the point of emission below.
- `opensearch:totalResults` at feed level is `# INFERRED` — the
  client is known to parse it, but no doc page renders a Wikis sample
  containing it.

Built with the stdlib `xml.etree.ElementTree` rather than `lxml` (not
yet a project dependency; nothing here needs XPath, just well-formed,
namespaced output).
"""

from xml.etree import ElementTree as ET

from connections_export.fakeserver.model import (
    BlogSet,
    FakeAttachment,
    FakeBlog,
    FakeBlogPost,
    FakeComment,
    FakeForum,
    FakeForumTopic,
    FakePage,
    FakeVersion,
    FakeWiki,
    ForumSet,
    WikiSet,
    person_uid,
)

# Namespaces ("Namespaces"
# section). `ca` (composite-applications) is documented but unused by
# any shape we emit, so it is omitted.
ATOM_NS = "http://www.w3.org/2005/Atom"
TD_NS = "urn:ibm.com/td"
SNX_NS = "http://www.ibm.com/xmlns/prod/sn"
THR_NS = "http://purl.org/syndication/thread/1.0"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
# AtomPub, used by Blogs' `<app:control><snx:comments.../></app:control>`.
APP_NS = "http://www.w3.org/2007/app"

# Category schemes shared by Blogs/Forums entries (Forums "Identifiers
# and markers"). The adapter matches type by URI, and flags
# by this exact scheme.
SN_TYPE_SCHEME = "http://www.ibm.com/xmlns/prod/sn/type"
SN_FLAGS_SCHEME = "http://www.ibm.com/xmlns/prod/sn/flags"

ET.register_namespace("", ATOM_NS)
ET.register_namespace("td", TD_NS)
ET.register_namespace("snx", SNX_NS)
ET.register_namespace("thr", THR_NS)
ET.register_namespace("opensearch", OPENSEARCH_NS)
ET.register_namespace("app", APP_NS)


def _q(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def _sub(parent: ET.Element, ns: str, local: str, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, _q(ns, local))
    if text is not None:
        el.text = text
    return el


def _to_bytes(root: ET.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    ).encode("utf-8")


def _entry_id(uuid: str) -> str:
    return f"urn:lsid:ibm.com:td:{uuid}"


def _paginate(items: list, ps: int | None, page: int | None) -> tuple[list, bool]:
    """Slice `items` per `ps`/`page` (both 1-based `page`). Returns
    `(page_items, has_more)`; `has_more` is False exactly when there is
    nothing beyond the returned slice -- which is how the truncation
    signature (a full `ps`-sized page with no `rel="next"`) can be
    produced on demand, simply by requesting a `ps` that exactly
    exhausts the remaining items (Producible truncation
    signature)."""
    if ps is None:
        return items, False
    page_num = page or 1
    start = (page_num - 1) * ps
    end = start + ps
    page_items = items[start:end]
    has_more = end < len(items)
    return page_items, has_more


def _page_url(
    *, base_url: str, auth_root: str, wiki_label: str, page_label: str, suffix: str
) -> str:
    return f"{base_url}/wikis/{auth_root}/api/wiki/{wiki_label}/page/{page_label}/{suffix}"


def page_browser_url(*, base_url: str, auth_root: str, wiki_label: str, page_label: str) -> str:
    """A page's browser (HTML) URL -- the `link[rel="alternate"]` on a page
    entry, and the identical join key a Search result carries for the same page
    (so the naive scan and the Search comparison can be matched)."""
    return f"{base_url}/wikis/{auth_root}/wiki/{wiki_label}/page/{page_label}"


# --------------------------------------------------------------------
# Page entry
# --------------------------------------------------------------------


def _build_page_entry(
    wiki: FakeWiki, page: FakePage, *, auth_root: str, base_url: str
) -> ET.Element:
    entry = ET.Element(_q(ATOM_NS, "entry"))
    _sub(entry, ATOM_NS, "id", _entry_id(page.uuid))
    title = _sub(entry, ATOM_NS, "title", page.title)
    title.set("type", "text")
    _sub(entry, TD_NS, "label", page.label)
    summary = _sub(entry, ATOM_NS, "summary", page.title)
    summary.set("type", "text")

    content = _sub(entry, ATOM_NS, "content")
    content.set("type", "text/html")
    content.set(
        "src",
        _page_url(
            base_url=base_url,
            auth_root=auth_root,
            wiki_label=wiki.label,
            page_label=page.label,
            suffix="media",
        ),
    )

    _sub(entry, TD_NS, "visibility", page.visibility)
    _sub(entry, TD_NS, "versionLabel", str(page.version_label))
    _sub(entry, ATOM_NS, "published", page.created)
    _sub(entry, ATOM_NS, "updated", page.modified)

    author = _sub(entry, ATOM_NS, "author")
    _sub(author, ATOM_NS, "name", page.author)
    _sub(author, SNX_NS, "userid", person_uid(page.author))

    entry_url = _page_url(
        base_url=base_url,
        auth_root=auth_root,
        wiki_label=wiki.label,
        page_label=page.label,
        suffix="entry",
    )
    media_url = _page_url(
        base_url=base_url,
        auth_root=auth_root,
        wiki_label=wiki.label,
        page_label=page.label,
        suffix="media",
    )
    feed_url = _page_url(
        base_url=base_url,
        auth_root=auth_root,
        wiki_label=wiki.label,
        page_label=page.label,
        suffix="feed",
    )

    for rel, href in (
        ("self", entry_url),
        ("edit", entry_url),
        ("edit-media", media_url),
        ("enclosure", media_url),
    ):
        link = _sub(entry, ATOM_NS, "link")
        link.set("rel", rel)
        link.set("href", href)

    alternate = _sub(entry, ATOM_NS, "link")
    alternate.set("rel", "alternate")
    alternate.set("type", "text/html")
    alternate.set(
        "href",
        page_browser_url(
            base_url=base_url, auth_root=auth_root, wiki_label=wiki.label, page_label=page.label
        ),
    )

    replies = _sub(entry, ATOM_NS, "link")
    replies.set("rel", "replies")
    replies.set("href", feed_url)
    replies.set(_q(THR_NS, "count"), str(len(page.comments)))

    rank_counts = {
        "recommendations": page.recommendations,
        "comment": len(page.comments),
        "hit": page.hits,
        "anonymous_hit": page.anonymous_hits,
        "attachments": len(page.attachments),
        "versions": len(page.versions),
    }
    for suffix, count in rank_counts.items():
        rank = _sub(entry, SNX_NS, "rank", str(count))
        rank.set("scheme", f"http://www.ibm.com/xmlns/prod/sn/{suffix}")

    # td:parentUuid is the sole hierarchy pointer -- never rel="parent".
    _sub(entry, TD_NS, "parentUuid", page.parent_uuid or "")

    # Category scheme: the reference documents an internal
    # inconsistency between `.../sn/type` and `tag:ibm.com,2006:td/type`
    # ("Known documentation defects" #1). Emit the tag: form, as
    # instructed: "Emit `tag:ibm.com,2006:td/type` from the fake; parse
    # leniently in the client."
    category = _sub(entry, ATOM_NS, "category")
    category.set("term", "page")
    category.set("scheme", "tag:ibm.com,2006:td/type")

    return entry


def page_entry(wiki: FakeWiki, page: FakePage, *, auth_root: str, base_url: str) -> bytes:
    return _to_bytes(_build_page_entry(wiki, page, auth_root=auth_root, base_url=base_url))


# --------------------------------------------------------------------
# Navigation entry
# --------------------------------------------------------------------


def navigation_entry(wiki: FakeWiki, page: FakePage, *, auth_root: str, base_url: str) -> bytes:
    entry = ET.Element(_q(ATOM_NS, "entry"))
    _sub(entry, ATOM_NS, "id", _entry_id(page.uuid))

    category = _sub(entry, ATOM_NS, "category")
    category.set("term", "navigation")
    category.set("scheme", "tag:ibm.com,2006:td/type")
    category.set("label", "navigation")

    _sub(entry, TD_NS, "uuid", page.uuid)
    _sub(entry, TD_NS, "label", page.label)

    self_url = f"{base_url}/wikis/{auth_root}/api/wiki/{wiki.label}/navigation/{page.label}/entry"
    link = _sub(entry, ATOM_NS, "link")
    link.set("href", self_url)
    link.set("rel", "self")  # only rel="self" -- never rel="parent"

    title = _sub(entry, ATOM_NS, "title", page.title)
    title.set("type", "text")

    _sub(entry, TD_NS, "parentUuid", page.parent_uuid or "")
    _sub(entry, TD_NS, "documentUuid", page.uuid)

    return _to_bytes(entry)


# --------------------------------------------------------------------
# Wikis feed
# --------------------------------------------------------------------


def _community_markers(parent: ET.Element, container) -> None:
    """Emit the community-membership markers a container carries, if any.

    `snx:communityUuid` is what the derive layer groups containers by;
    `snx:communityTitle` beside it lets the community be named without
    fetching a community document. Both are not documented -- see
    `adapters.search.community_uuid_from_feed`.
    """
    if getattr(container, "community_uuid", None):
        _sub(parent, SNX_NS, "communityUuid", container.community_uuid)
    if getattr(container, "community_title", None):
        _sub(parent, SNX_NS, "communityTitle", container.community_title)


def _feed_root(*, feed_id: str, title: str, total_results: int) -> ET.Element:
    feed = ET.Element(_q(ATOM_NS, "feed"))
    _sub(feed, ATOM_NS, "id", feed_id)
    feed_title = _sub(feed, ATOM_NS, "title", title)
    feed_title.set("type", "text")
    # opensearch:totalResults # INFERRED: the client parses this
    # for Wikis feeds, but no doc page renders a feed-level sample.
    _sub(feed, OPENSEARCH_NS, "totalResults", str(total_results))
    return feed


def wikis_feed(
    wikiset: WikiSet,
    *,
    auth_root: str,
    base_url: str,
    ps: int | None = None,
    page: int | None = None,
) -> bytes:
    feed = _feed_root(
        feed_id=f"{base_url}/wikis/{auth_root}/api/wikis/feed",
        title="wikis",
        total_results=len(wikiset.wikis),
    )
    page_items, has_more = _paginate(wikiset.wikis, ps, page)
    for wiki in page_items:
        entry = ET.SubElement(feed, _q(ATOM_NS, "entry"))
        _sub(entry, ATOM_NS, "id", _entry_id(wiki.uuid))
        title = _sub(entry, ATOM_NS, "title", wiki.title)
        title.set("type", "text")
        _sub(entry, TD_NS, "label", wiki.label)
        _community_markers(entry, wiki)
        _sub(entry, ATOM_NS, "published", wiki.created)
        _sub(entry, ATOM_NS, "updated", wiki.modified)
        self_url = f"{base_url}/wikis/{auth_root}/api/wiki/{wiki.label}/feed"
        link = _sub(entry, ATOM_NS, "link")
        link.set("rel", "self")
        link.set("href", self_url)

    if has_more:
        next_link = ET.SubElement(feed, _q(ATOM_NS, "link"))
        next_link.set("rel", "next")
        next_page = (page or 1) + 1
        next_link.set(
            "href", f"{base_url}/wikis/{auth_root}/api/wikis/feed?ps={ps}&page={next_page}"
        )

    return _to_bytes(feed)


def wiki_pages_feed(
    wiki: FakeWiki, *, auth_root: str, base_url: str, ps: int | None = None, page: int | None = None
) -> bytes:
    """The "pages in a wiki" feed, `/wikis/{auth}/api/wiki/{wiki-label}/feed`.
    Each item is a page entry."""
    feed = _feed_root(
        feed_id=f"{base_url}/wikis/{auth_root}/api/wiki/{wiki.label}/feed",
        title=wiki.title,
        total_results=len(wiki.pages),
    )
    page_items, has_more = _paginate(wiki.pages, ps, page)
    for p in page_items:
        feed.append(_build_page_entry(wiki, p, auth_root=auth_root, base_url=base_url))

    if has_more:
        next_link = ET.SubElement(feed, _q(ATOM_NS, "link"))
        next_link.set("rel", "next")
        next_page = (page or 1) + 1
        next_link.set(
            "href",
            f"{base_url}/wikis/{auth_root}/api/wiki/{wiki.label}/feed?ps={ps}&page={next_page}",
        )

    return _to_bytes(feed)


# --------------------------------------------------------------------
# Artifacts feed: comments / versions / attachments / tags
# --------------------------------------------------------------------
# Single page-feed URL, `category` selects which artifact list; the
# reference's default (no category) is comments. The comment / version
# / attachment / tag *entry* schemas are undocumented anywhere in the
# reference -- these are plausible Atom entries, not transcribed
# ones. # INFERRED


def _comment_entry(comment: FakeComment) -> ET.Element:
    # # INFERRED: no comment entry schema is published. Plausible
    # shape: id/title/content/author(snx:userid)/
    # published/updated.
    entry = ET.Element(_q(ATOM_NS, "entry"))
    _sub(entry, ATOM_NS, "id", _entry_id(comment.uuid))
    title = _sub(entry, ATOM_NS, "title", comment.title or comment.uuid)
    title.set("type", "text")
    content = _sub(entry, ATOM_NS, "content", comment.content_html)
    content.set("type", "text/html")
    author = _sub(entry, ATOM_NS, "author")
    _sub(author, ATOM_NS, "name", comment.author)
    # The person's id, not their name: a deployment's `snx:userid` is an
    # identifier, and emitting the display name taught the opposite -- visibly,
    # as "M. Lindqvist (uid: M. Lindqvist)" in the author list.
    _sub(author, SNX_NS, "userid", person_uid(comment.author))
    _sub(entry, ATOM_NS, "published", comment.published)
    _sub(entry, ATOM_NS, "updated", comment.updated)
    return entry


def _version_entry(version: FakeVersion) -> ET.Element:
    # # INFERRED: no version entry schema is published; only
    # `<td:versionLabel>` on the *page* entry is. Extending
    # that field onto a standalone version entry is our own inference.
    entry = ET.Element(_q(ATOM_NS, "entry"))
    _sub(entry, ATOM_NS, "id", _entry_id(version.uuid))
    title = _sub(entry, ATOM_NS, "title", f"version {version.version_label}")
    title.set("type", "text")
    _sub(entry, TD_NS, "versionLabel", str(version.version_label))
    content = _sub(entry, ATOM_NS, "content", version.content_html)
    content.set("type", "text/html")
    author = _sub(entry, ATOM_NS, "author")
    _sub(author, ATOM_NS, "name", version.author)
    _sub(author, SNX_NS, "userid", person_uid(version.author))
    _sub(entry, ATOM_NS, "published", version.created)
    _sub(entry, ATOM_NS, "updated", version.created)
    return entry


def _attachment_entry(attachment: FakeAttachment) -> ET.Element:
    # # INFERRED: no attachment entry schema is published; we
    # know only that attachments are counted via
    # `snx:rank scheme=".../attachments"` on the page entry.
    entry = ET.Element(_q(ATOM_NS, "entry"))
    _sub(entry, ATOM_NS, "id", _entry_id(attachment.uuid))
    title = _sub(entry, ATOM_NS, "title", attachment.filename)
    title.set("type", "text")
    link = _sub(entry, ATOM_NS, "link")
    link.set("rel", "enclosure")
    link.set("type", attachment.content_type)
    # A real, same-origin http href (relative, resolved against base_url by the
    # crawler) so the attachment's bytes are actually fetched and archived --
    # not the old `urn:` placeholder the crawler skipped, which left every
    # attachment uncaptured and undownloadable.
    link.set("href", f"/wikis/attachment/{attachment.uuid}/{attachment.filename}")
    author = _sub(entry, ATOM_NS, "author")
    _sub(author, ATOM_NS, "name", attachment.author)
    _sub(author, SNX_NS, "userid", person_uid(attachment.author))
    _sub(entry, ATOM_NS, "published", attachment.created)
    _sub(entry, ATOM_NS, "updated", attachment.created)
    return entry


_CATEGORY_BUILDERS = {
    "comment": lambda page: [_comment_entry(c) for c in page.comments],
    "version": lambda page: [_version_entry(v) for v in page.versions],
    "attachment": lambda page: [_attachment_entry(a) for a in page.attachments],
}


def _tags_feed(wiki: FakeWiki, page: FakePage, *, auth_root: str, base_url: str) -> bytes:
    """The `?category=tag` feed: the page's
    tags as feed-level `<category term=.. label=..>` (no scheme, no per-tag
    `<entry>`)."""
    feed = _feed_root(
        feed_id=_page_url(
            base_url=base_url,
            auth_root=auth_root,
            wiki_label=wiki.label,
            page_label=page.label,
            suffix="feed",
        ),
        title=f"{page.title} - tag",
        total_results=len(page.tags),
    )
    for tag in page.tags:
        category = _sub(feed, ATOM_NS, "category")
        category.set("term", tag)
        category.set("label", tag)
    return _to_bytes(feed)


def artifacts_feed(
    wiki: FakeWiki,
    page: FakePage,
    *,
    category: str | None,
    auth_root: str,
    base_url: str,
    ps: int | None = None,
    page_num: int | None = None,
) -> bytes:
    """Comments/versions/attachments/tags from the single page-feed
    URL, disambiguated by `category`. Omitted `category` defaults to
    comments (reference: "If this parameter is not provided then all
    page comments are returned.")."""
    effective_category = category or "comment"
    if effective_category == "tag":
        # tags are feed-level, scheme-less
        # <category term=.. label=..> on the <feed> -- not one <entry> per tag.
        return _tags_feed(wiki, page, auth_root=auth_root, base_url=base_url)
    builder = _CATEGORY_BUILDERS.get(effective_category, _CATEGORY_BUILDERS["comment"])
    all_entries = builder(page)

    feed = _feed_root(
        feed_id=_page_url(
            base_url=base_url,
            auth_root=auth_root,
            wiki_label=wiki.label,
            page_label=page.label,
            suffix="feed",
        ),
        title=f"{page.title} - {effective_category}",
        total_results=len(all_entries),
    )
    page_items, has_more = _paginate(all_entries, ps, page_num)
    for entry in page_items:
        feed.append(entry)

    if has_more:
        next_link = ET.SubElement(feed, _q(ATOM_NS, "link"))
        next_link.set("rel", "next")
        next_page = (page_num or 1) + 1
        base = _page_url(
            base_url=base_url,
            auth_root=auth_root,
            wiki_label=wiki.label,
            page_label=page.label,
            suffix="feed",
        )
        next_link.set("href", f"{base}?category={effective_category}&ps={ps}&page={next_page}")

    return _to_bytes(feed)


# ====================================================================
# Blogs & Forums (Stage 2 -- all not documented; no literal XML sample
# exists for any Blogs or Forums shape. Field *lists* are
# from the published API reference; the assembled entry/feed
# *shapes* below are inferred, mirroring the adapter fixtures under
# tests/adapters/fixtures/. These serializers are the round-trip
# counterpart the adapter tests never had.)
# ====================================================================


def _blog_entry_id(uuid: str) -> str:
    """`urn:lsid:ibm.com:blogs:entry-<uuid>`."""
    return f"urn:lsid:ibm.com:blogs:entry-{uuid}"


def _forum_entry_id(uuid: str) -> str:
    """`urn:lsid:ibm.com:forum:<uuid>`."""
    return f"urn:lsid:ibm.com:forum:{uuid}"


def _append_next(feed: ET.Element, href: str) -> None:
    """Append a feed-level `<link rel="next" href=...>`. # INFERRED: no
    documented Blogs/Forums feed sample renders `rel="next"` (Blogs doc
    explicitly notes none observed; Forums renders no feed sample at
    all), but the crawler's page-walk relies on it, so the fake emits it
    for pagination consistency with the Wikis feeds."""
    link = ET.SubElement(feed, _q(ATOM_NS, "link"))
    link.set("rel", "next")
    link.set("href", href)


def _content_html_el(parent: ET.Element, html_text: str) -> None:
    """`<content type="html">` carrying author HTML (Blogs/Forums use
    `type="html"`, not Wikis' `text/html`)."""
    content = _sub(parent, ATOM_NS, "content", html_text)
    content.set("type", "html")


def _author_el(parent: ET.Element, name: str, userid: str | None = None) -> None:
    """An `atom:author` with its `snx:userid`.

    The id defaults to the person's, so every component credits the same human
    with the same identifier -- which is what a deployment does, and what the
    author filter matches on. Emitting the author without one taught that some
    content simply has no identifiable author, and left the author picker
    unable to offer those people at all.
    """
    author = _sub(parent, ATOM_NS, "author")
    _sub(author, ATOM_NS, "name", name)
    _sub(author, SNX_NS, "userid", userid or person_uid(name))
    _sub(author, SNX_NS, "userState", "active")


# --------------------------------------------------------------------
# Blogs
# --------------------------------------------------------------------


def blogs_list_feed(
    blogset: BlogSet, *, base_url: str, ps: int | None = None, page: int | None = None
) -> bytes:
    """The "list all blogs" feed (`/blogs/{homepage}/feed/blogs/atom`).
    # INFERRED: no field list is documented for the blog *container*;
    only the cross-app "Genuinely shared" fields (id, title, self link)
    are emitted -- exactly what `parse_blogs_feed` reads."""
    feed = _feed_root(
        feed_id=f"{base_url}/blogs/{blogset.homepage}/feed/blogs/atom",
        title="All Blogs",
        total_results=len(blogset.blogs),
    )
    page_items, has_more = _paginate(blogset.blogs, ps, page)
    for blog in page_items:
        entry = ET.SubElement(feed, _q(ATOM_NS, "entry"))
        _sub(entry, ATOM_NS, "id", _blog_entry_id(blog.uuid))
        _community_markers(entry, blog)
        title = _sub(entry, ATOM_NS, "title", blog.title)
        title.set("type", "text")
        _sub(entry, ATOM_NS, "published", blog.created)
        _sub(entry, ATOM_NS, "updated", blog.modified)
        link = _sub(entry, ATOM_NS, "link")
        link.set("rel", "self")
        # real HCL Connections deployments emit
        # roller-ui/rendering/feed/{uuid}/entries/atom
        # as the per-blog self link (confirmed via a working client's blog-URL
        # discovery).
        link.set("href", f"{base_url}/blogs/roller-ui/rendering/feed/{blog.uuid}/entries/atom")
    if has_more:
        next_page = (page or 1) + 1
        _append_next(
            feed,
            f"{base_url}/blogs/{blogset.homepage}/feed/blogs/atom?ps={ps}&page={next_page}",
        )
    return _to_bytes(feed)


def _build_post_entry(blog: FakeBlog, post: FakeBlogPost, *, base_url: str) -> ET.Element:
    entry = ET.Element(_q(ATOM_NS, "entry"))
    _sub(entry, ATOM_NS, "id", _blog_entry_id(post.uuid))
    title = _sub(entry, ATOM_NS, "title", post.title)
    title.set("type", "text")
    _author_el(entry, post.author, post.author_userid)
    _sub(entry, ATOM_NS, "published", post.published)
    _sub(entry, ATOM_NS, "updated", post.updated)
    summary = _sub(entry, ATOM_NS, "summary", post.title)
    summary.set("type", "text")
    _content_html_el(entry, post.body_html)

    # Bare user-tag categories (no scheme).
    for tag in post.tags:
        cat = _sub(entry, ATOM_NS, "category")
        cat.set("term", tag)

    # app:edited + app:control/snx:comments enabled=yes|no.
    _sub(entry, APP_NS, "edited", post.updated)
    control = _sub(entry, APP_NS, "control")
    comments = _sub(control, SNX_NS, "comments")
    comments.set("enabled", "yes" if post.comments_enabled else "no")
    comments.set("days", "30" if post.comments_enabled else "0")

    self_link = _sub(entry, ATOM_NS, "link")
    self_link.set("rel", "self")
    self_link.set("href", f"{base_url}/blogs/{blog.handle}/feed/entry/atom?entryid={post.uuid}")
    # The post's own browser URL, the same shape its body links use.
    # Without it a derived post has no address, so a link from one post to
    # another in the SAME exported blog cannot be recognised as internal,
    # and the sample data would demonstrate the wrong behaviour.
    alternate = _sub(entry, ATOM_NS, "link")
    alternate.set("rel", "alternate")
    alternate.set("type", "text/html")
    alternate.set("href", f"{base_url}/blogs/{blog.handle}/entry/{post.slug}")
    replies = _sub(entry, ATOM_NS, "link")
    replies.set("rel", "replies")
    replies.set("href", f"{base_url}/blogs/{blog.handle}/feed/entrycomments/{post.slug}/atom")
    replies.set(_q(THR_NS, "count"), str(len(post.comments)))

    for scheme_suffix, count in (
        ("recommendations", post.recommendations),
        ("comment", len(post.comments)),
        ("hit", post.hits),
    ):
        rank = _sub(entry, SNX_NS, "rank", str(count))
        rank.set("scheme", f"http://www.ibm.com/xmlns/prod/sn/{scheme_suffix}")
    return entry


def blog_entries_feed(
    blog: FakeBlog, *, base_url: str, ps: int | None = None, page: int | None = None
) -> bytes:
    """A blog's entries feed (`/blogs/{handle}/feed/entries/atom`).
    for the entry; the assembled shape is
    Not documented."""
    feed = _feed_root(
        feed_id=f"{base_url}/blogs/{blog.handle}/feed/entries/atom",
        title=blog.title,
        total_results=len(blog.posts),
    )
    page_items, has_more = _paginate(blog.posts, ps, page)
    for post in page_items:
        feed.append(_build_post_entry(blog, post, base_url=base_url))
    if has_more:
        # Blog pages start at 0 (confirmed); use `page + 1` not `(page or 1) + 1`.
        # Also emit roller-ui URL so crawlers following roller-ui self_url get
        # consistent next links.
        next_page = (page if page is not None else 0) + 1
        _append_next(
            feed,
            f"{base_url}/blogs/roller-ui/rendering/feed/{blog.uuid}/entries/atom?ps={ps}&page={next_page}",
        )
    return _to_bytes(feed)


def blog_all_comments_feed(
    blog: FakeBlog,
    *,
    base_url: str,
    ps: int | None = None,
    page: int | None = None,
) -> bytes:
    """Every comment in a blog, newest first
    (`/blogs/roller-ui/rendering/feed/{uuid}/comments/atom`).

    asked with `since` and `sortBy=modified` it
    returns just the comments that moved, so one request per blog answers
    "which discussions grew" -- where the per-post feed costs one request per
    post whether or not anything happened.

    Unlike the per-post feed, EVERY comment carries `<thr:in-reply-to>` here:
    the parent is not implied by the URL, so a comment that did not name one
    could not be traced back to its post. `ref` is the immediate parent, which
    for a reply is a sibling comment rather than the entry.
    """
    feed = _feed_root(
        feed_id=f"{base_url}/blogs/roller-ui/rendering/feed/{blog.uuid}/comments/atom",
        title=f"Comments on {blog.title}",
        total_results=sum(len(post.comments) for post in blog.posts),
    )
    pairs = [(post, comment) for post in blog.posts for comment in post.comments]
    page_items, has_more = _paginate(pairs, ps, page)
    for post, comment in page_items:
        entry = ET.SubElement(feed, _q(ATOM_NS, "entry"))
        _sub(entry, ATOM_NS, "id", _blog_entry_id(comment.uuid))
        _author_el(entry, comment.author, comment.author_userid)
        _sub(entry, ATOM_NS, "published", comment.published)
        _sub(entry, ATOM_NS, "updated", comment.published)
        _content_html_el(entry, comment.content_html)
        reply_to = _sub(entry, THR_NS, "in-reply-to")
        # `ref` ONLY, and deliberately: `atom.in_reply_to`'s own note records
        # that "Blogs only ever populates `ref` in practice". Emitting `source`
        # as well would hand the crawler the post id for free and leave the
        # path that actually has to walk the chain untested -- the reply's ref
        # names its SIBLING comment, not the entry.
        reply_to.set("ref", _blog_entry_id(comment.in_reply_to or post.uuid))
        reply_to.set(
            "href",
            f"{base_url}/blogs/{blog.handle}/feed/entrycomments/{post.slug}/atom",
        )
    if has_more:
        next_page = (page or 1) + 1
        _append_next(
            feed,
            f"{base_url}/blogs/roller-ui/rendering/feed/{blog.uuid}/comments/atom"
            f"?ps={ps}&page={next_page}",
        )
    return _to_bytes(feed)


def blog_comments_feed(
    blog: FakeBlog,
    post: FakeBlogPost,
    *,
    base_url: str,
    ps: int | None = None,
    page: int | None = None,
) -> bytes:
    """A blog entry's comments feed
    (`/blogs/{handle}/feed/entrycomments/{slug}/atom`). One-level
    threading via `<thr:in-reply-to ref=>`. The mechanism is documented;
    the literal shape here is a reconstruction."""
    feed = _feed_root(
        feed_id=f"{base_url}/blogs/{blog.handle}/feed/entrycomments/{post.slug}/atom",
        title=f"Comments on {post.title}",
        total_results=len(post.comments),
    )
    page_items, has_more = _paginate(post.comments, ps, page)
    for comment in page_items:
        entry = ET.SubElement(feed, _q(ATOM_NS, "entry"))
        _sub(entry, ATOM_NS, "id", _blog_entry_id(comment.uuid))
        _author_el(entry, comment.author)
        _sub(entry, ATOM_NS, "published", comment.published)
        _content_html_el(entry, comment.content_html)
        if comment.in_reply_to is not None:
            reply_to = _sub(entry, THR_NS, "in-reply-to")
            reply_to.set("ref", _blog_entry_id(comment.in_reply_to))
            reply_to.set("source", _blog_entry_id(post.uuid))
            reply_to.set(
                "href",
                f"{base_url}/blogs/{blog.handle}/feed/comment/atom?commentid={comment.in_reply_to}",
            )
    if has_more:
        next_page = (page or 1) + 1
        _append_next(
            feed,
            f"{base_url}/blogs/{blog.handle}/feed/entrycomments/{post.slug}/atom"
            f"?ps={ps}&page={next_page}",
        )
    return _to_bytes(feed)


# --------------------------------------------------------------------
# Forums
# --------------------------------------------------------------------


def _flag_categories(entry: ET.Element, flags: list[str]) -> None:
    for flag in flags:
        cat = _sub(entry, ATOM_NS, "category")
        cat.set("term", flag)
        cat.set("scheme", SN_FLAGS_SCHEME)


def _type_category(entry: ET.Element, term: str) -> None:
    cat = _sub(entry, ATOM_NS, "category")
    cat.set("term", term)
    cat.set("scheme", SN_TYPE_SCHEME)


def forums_list_feed(
    forumset: ForumSet, *, base_url: str, ps: int | None = None, page: int | None = None
) -> bytes:
    """The "all forums" feed (`/forums/atom/forums`). # INFERRED: the
    forums adapter (`forums.py`) has a `forums_list_url` builder but no
    `parse_forums_feed` -- unlike Blogs, whose list *is* parsed. So only
    the cross-app shared fields (id, title, self link) are emitted; this
    feed exists so the URL round-trips as well-formed Atom, but there is
    no adapter parser to assert against."""
    feed = _feed_root(
        feed_id=f"{base_url}/forums/atom/forums",
        title="All Forums",
        total_results=len(forumset.forums),
    )
    page_items, has_more = _paginate(forumset.forums, ps, page)
    for forum in page_items:
        entry = ET.SubElement(feed, _q(ATOM_NS, "entry"))
        _sub(entry, ATOM_NS, "id", _forum_entry_id(forum.uuid))
        title = _sub(entry, ATOM_NS, "title", forum.title)
        title.set("type", "text")
        _sub(entry, ATOM_NS, "published", forum.created)
        _sub(entry, ATOM_NS, "updated", forum.modified)
        link = _sub(entry, ATOM_NS, "link")
        link.set("rel", "self")
        link.set("href", f"{base_url}/forums/atom/topics?forumUuid={forum.uuid}")
    if has_more:
        next_page = (page or 1) + 1
        _append_next(feed, f"{base_url}/forums/atom/forums?ps={ps}&page={next_page}")
    return _to_bytes(feed)


def _build_topic_entry(topic: FakeForumTopic, *, base_url: str) -> ET.Element:
    entry = ET.Element(_q(ATOM_NS, "entry"))
    _sub(entry, ATOM_NS, "id", _forum_entry_id(topic.uuid))
    title = _sub(entry, ATOM_NS, "title", topic.title)
    title.set("type", "text")
    _author_el(entry, topic.author)
    _sub(entry, ATOM_NS, "published", topic.published)
    _content_html_el(entry, topic.content_html)
    _type_category(entry, "forum-topic")
    # The topic's own browser URL, for the same reason blog entries
    # carry one: without an address a derived topic cannot be the target of a
    # link from anywhere else in the export.
    alternate = _sub(entry, ATOM_NS, "link")
    alternate.set("rel", "alternate")
    alternate.set("type", "text/html")
    alternate.set("href", f"{base_url}/forums/html/topic?id={topic.uuid}")
    _flag_categories(entry, topic.flags)
    for tag in topic.tags:
        cat = _sub(entry, ATOM_NS, "category")
        cat.set("term", tag)
    # THE TRAP: a topic entry also carries <thr:in-reply-to>, whose ref
    # is the parent *forum's* LSID -- data, never a type signal.
    reply_to = _sub(entry, THR_NS, "in-reply-to")
    reply_to.set("ref", _forum_entry_id(topic.forum_uuid))
    return entry


def forum_topics_feed(
    forum: FakeForum, *, base_url: str, ps: int | None = None, page: int | None = None
) -> bytes:
    """A forum's topics feed (`/forums/atom/topics?forumUuid=<uuid>`).
    ."""
    feed = _feed_root(
        feed_id=f"{base_url}/forums/atom/topics?forumUuid={forum.uuid}",
        title=forum.title,
        total_results=len(forum.topics),
    )
    _community_markers(feed, forum)
    page_items, has_more = _paginate(forum.topics, ps, page)
    for topic in page_items:
        feed.append(_build_topic_entry(topic, base_url=base_url))
    if has_more:
        next_page = (page or 1) + 1
        _append_next(
            feed, f"{base_url}/forums/atom/topics?forumUuid={forum.uuid}&ps={ps}&page={next_page}"
        )
    return _to_bytes(feed)


def forum_topic_entry(topic: FakeForumTopic, *, base_url: str) -> bytes:
    """A single topic document (`/forums/atom/topic?topicUuid=<uuid>`) --
    a bare `<entry>` (no `<feed>` wrapper), per `forums.py`'s
    `parse_single_topic` contract. Reuses `_build_topic_entry` (the same
    entry shape the topics-list feed embeds) so a topic's fields are
    byte-identical whichever endpoint served it."""
    return _to_bytes(_build_topic_entry(topic, base_url=base_url))


def forum_replies_feed(
    topic: FakeForumTopic, *, base_url: str, ps: int | None = None, page: int | None = None
) -> bytes:
    """A topic's FLAT replies feed (`/forums/atom/replies?topicUuid=<uuid>`).
    Nesting is carried per-entry on `<thr:in-reply-to>` (ref = immediate
    parent, source = top-level topic), never by XML nesting -- the
    replies feed is FLAT. The mechanism is documented; the literal shape
    here is a reconstruction."""
    feed = _feed_root(
        feed_id=f"{base_url}/forums/atom/replies?topicUuid={topic.uuid}",
        title=f"Replies to {topic.title}",
        total_results=len(topic.replies),
    )
    page_items, has_more = _paginate(topic.replies, ps, page)
    for reply in page_items:
        entry = ET.SubElement(feed, _q(ATOM_NS, "entry"))
        _sub(entry, ATOM_NS, "id", _forum_entry_id(reply.uuid))
        _author_el(entry, reply.author)
        _sub(entry, ATOM_NS, "published", reply.published)
        _content_html_el(entry, reply.content_html)
        _type_category(entry, "forum-reply")
        _flag_categories(entry, reply.flags)
        reply_to = _sub(entry, THR_NS, "in-reply-to")
        reply_to.set("ref", _forum_entry_id(reply.ref))
        reply_to.set("source", _forum_entry_id(reply.source_topic))
        reply_to.set("href", f"{base_url}/forums/atom/reply?replyUuid={reply.ref}")
    if has_more:
        next_page = (page or 1) + 1
        _append_next(
            feed, f"{base_url}/forums/atom/replies?topicUuid={topic.uuid}&ps={ps}&page={next_page}"
        )
    return _to_bytes(feed)


# --------------------------------------------------------------------
# Files (a community's library). Field names are from two live
# captures; the XML carrying them is not documented, so the fake serves exactly
# what `adapters/files.py` reads and no more -- if the real shape differs,
# both change together and the mismatch is one edit, not a hunt.
# --------------------------------------------------------------------


def _file_entry_id(uuid: str) -> str:
    return f"urn:lsid:ibm.com:files:document-{uuid}"


def community_library_feed(
    library, *, base_url: str, ps: int | None = None, page: int | None = None
) -> bytes:
    """A community's files (`/files/basic/api/communitylibrary/{uuid}/feed`).

    The FLAT feed: every file, whether filed in a folder or not. A live capture
    settled that -- 19 ids here, all 19 also reachable through collections --
    which is why the crawler takes content from this feed alone.
    """
    feed = _feed_root(
        feed_id=f"{base_url}/files/basic/api/communitylibrary/{library.community_uuid}/feed",
        title=library.title,
        total_results=len(library.files),
    )
    page_items, has_more = _paginate(library.files, ps, page)
    for item in page_items:
        entry = ET.SubElement(feed, _q(ATOM_NS, "entry"))
        _sub(entry, ATOM_NS, "id", _file_entry_id(item.uuid))
        title = _sub(entry, ATOM_NS, "title", item.title)
        title.set("type", "text")
        _author_el(entry, item.author)
        _sub(entry, ATOM_NS, "published", item.published)
        _sub(entry, ATOM_NS, "updated", item.updated)
        _sub(entry, TD_NS, "modified", item.updated)
        _sub(entry, TD_NS, "label", item.name)
        _sub(entry, TD_NS, "libraryId", library.library_id)
        _sub(entry, TD_NS, "libraryType", "communityFiles")
        _sub(entry, TD_NS, "objectTypeName", item.content_type)
        _sub(entry, TD_NS, "versionLabel", item.version_label)
        _sub(entry, TD_NS, "totalMediaSize", str(item.size))
        _sub(entry, TD_NS, "isFiledInFolder", "true" if item.filed_in_folder else "false")
        for tag in item.tags:
            cat = _sub(entry, ATOM_NS, "category")
            cat.set("term", tag)
        download = (
            f"{base_url}/files/basic/anonymous/api/library/{library.library_id}"
            f"/document/{item.uuid}/media/{item.name}"
        )
        for rel, href in (
            ("self", f"{base_url}/files/basic/api/document/{item.uuid}/entry"),
            # The shape a real deployment serves, not
            # the `/files/app#/file/{id}` fragment this fake invented. A fake
            # that emits a shape the deployment does not produce means every
            # test below it describes a fiction -- and this one had the id in
            # a fragment where the deployment puts it in the path.
            ("alternate", f"{base_url}/files/app/file/{item.uuid}"),
            ("enclosure", download),
            ("replies", f"{base_url}/files/basic/api/document/{item.uuid}/feed"),
        ):
            link = _sub(entry, ATOM_NS, "link")
            link.set("rel", rel)
            link.set("href", href)
            if rel == "enclosure":
                link.set("type", item.content_type)
                link.set("length", str(item.size))
    if has_more:
        next_page = (page or 1) + 1
        _append_next(
            feed,
            f"{base_url}/files/basic/api/communitylibrary/{library.community_uuid}"
            f"/feed?ps={ps}&page={next_page}",
        )
    return _to_bytes(feed)


def community_widgets_feed(rte, *, base_url: str, community_uuid: str) -> bytes:
    """A community's persisted widget layout
    (`/communities/service/atom/community/widgets?communityUuid={uuid}`).

    DISCOVERY for Rich Content, and readable by an ordinary member.
    Deliberately carries widgets that are NOT rich content (a forum, a files
    widget) and one rich content widget with no `widgetResourceId` -- the
    uninitialized case, which a live capture found alongside seven initialized
    ones. A parser that matched on the `conn-rte` prefix or assumed every
    widget has a resource id would pass against a tidier fake and fail here.
    """
    container = f"conn-rte#{community_uuid}"
    pages = rte.pages if rte else []
    uninitialized = rte.uninitialized if rte else 0
    feed = _feed_root(
        feed_id=f"{base_url}/communities/service/atom/community/widgets"
        f"?communityUuid={community_uuid}",
        title="Widgets",
        total_results=len(pages) + uninitialized + 2,
    )

    def widget(*, title: str, term: str, container_id: str, resource_id: str | None) -> None:
        entry = ET.SubElement(feed, _q(ATOM_NS, "entry"))
        _sub(entry, ATOM_NS, "id", f"urn:lsid:ibm.com:communities:widget-{title}")
        entry_title = _sub(entry, ATOM_NS, "title", title)
        entry_title.set("type", "text")
        cat = _sub(entry, ATOM_NS, "category")
        cat.set("term", term)
        cat.set("scheme", "http://www.ibm.com/xmlns/prod/sn/type")
        _sub(entry, SNX_NS, "widgetResourceContainerId", container_id)
        if resource_id:
            _sub(entry, SNX_NS, "widgetResourceId", resource_id)

    # Other applications' widgets: present in every real layout, and each one
    # carries a resource id of its own. Fetching those as rich content pages
    # would be nonsense, so the parser must select on the container id.
    widget(
        title="Forums",
        term="Forum",
        container_id=f"conn-forums#{community_uuid}",
        resource_id="forum-widget-resource",
    )
    widget(
        title="Files",
        term="Files",
        container_id=f"conn-files#{community_uuid}",
        resource_id="files-widget-resource",
    )
    for page in pages:
        widget(
            title=page.title,
            term="RichContent",
            container_id=container,
            resource_id=page.resource_id,
        )
    for i in range(uninitialized):
        widget(
            title=f"Rich Content {i + 1}",
            term="RichContent",
            container_id=container,
            resource_id=None,
        )
    return _to_bytes(feed)


def rich_content_page_entry(page, *, base_url: str, community_uuid: str) -> bytes:
    """One Rich Content page
    (`/connections/rte/community/{uuid}/page/{widgetResourceId}/entry`).

    The body is INLINE: a live capture confirmed the entry carries the HTML,
    so there is no second fetch for content.
    """
    entry = ET.Element(_q(ATOM_NS, "entry"))
    _sub(entry, ATOM_NS, "id", f"urn:lsid:ibm.com:td:page-{page.resource_id}")
    title = _sub(entry, ATOM_NS, "title", page.title)
    title.set("type", "text")
    _author_el(entry, page.author)
    _sub(entry, ATOM_NS, "published", page.published)
    _sub(entry, ATOM_NS, "updated", page.updated)
    cat = _sub(entry, ATOM_NS, "category")
    cat.set("term", "page")
    cat.set("scheme", "http://www.ibm.com/xmlns/prod/sn/type")
    _sub(entry, TD_NS, "versionLabel", page.version_label)
    content = _sub(entry, ATOM_NS, "content", page.body_html)
    content.set("type", "html")
    for rel, href in (
        (
            "self",
            f"{base_url}/connections/rte/community/{community_uuid}/page/{page.resource_id}/entry",
        ),
        ("enclosure", f"{base_url}/connections/rte/media/{page.resource_id}"),
        (
            "alternate",
            f"{base_url}/communities/service/html/communityview?communityUuid={community_uuid}",
        ),
    ):
        link = _sub(entry, ATOM_NS, "link")
        link.set("rel", rel)
        link.set("href", href)
    return _to_bytes(entry)


def community_collection_feed(library, *, base_url: str) -> bytes:
    """A community's folders (`/files/basic/api/communitycollection/{uuid}/feed`).

    Deliberately includes one entry for a file the library feed does NOT carry.
    A live capture found exactly that -- 22 collection entries against 19
    library files, the extras being someone's personal documents -- and an
    export that merged the two feeds would put private files in a community
    archive. The demo reproduces the hazard so the filter is exercised.
    """
    feed = _feed_root(
        feed_id=f"{base_url}/files/basic/api/communitycollection/{library.community_uuid}/feed",
        title=f"{library.title} folders",
        total_results=len(library.folders),
    )
    for folder in library.folders:
        entry = ET.SubElement(feed, _q(ATOM_NS, "entry"))
        _sub(entry, ATOM_NS, "id", f"urn:lsid:ibm.com:files:collection-{folder.uuid}")
        _sub(entry, ATOM_NS, "title", folder.name)
        _sub(entry, TD_NS, "folderName", folder.name)
        members = list(folder.file_uuids)
        if folder is library.folders[0]:
            members = [*members, "personal-file-not-in-this-library"]
        for file_uuid in members:
            child = ET.SubElement(entry, _q(ATOM_NS, "entry"))
            _sub(child, ATOM_NS, "id", _file_entry_id(file_uuid))
    return _to_bytes(feed)


def subcommunities_feed(children: list[tuple[str, str]], *, base_url: str) -> bytes:
    """A community's children (`/communities/service/atom/community/subcommunities`).

    Entries carry the child's title, its community UUID, and the normal
    community links.

    An empty list is a real answer rather than a 404 -- a community with no
    children still has this feed, and "no children" must not look the same as
    "no such community", because only one of those is a reason to stop.
    """
    feed = _feed_root(
        feed_id=f"{base_url}/communities/service/atom/community/subcommunities",
        title="Subcommunities",
        total_results=len(children),
    )
    for uuid, title in children:
        entry = ET.SubElement(feed, _q(ATOM_NS, "entry"))
        _sub(entry, ATOM_NS, "id", f"urn:lsid:ibm.com:communities:community-{uuid}")
        entry_title = _sub(entry, ATOM_NS, "title", title)
        entry_title.set("type", "text")
        _sub(entry, ATOM_NS, "updated", "2026-08-01T09:00:00.000Z")
        _sub(entry, SNX_NS, "communityUuid", uuid)
        link = _sub(entry, ATOM_NS, "link")
        link.set("rel", "self")
        link.set(
            "href",
            f"{base_url}/communities/service/atom/community/instance?communityUuid={uuid}",
        )
    return _to_bytes(feed)


def profile_service_document(name: str, *, base_url: str) -> bytes:
    """The Profiles service document -- "who am I".

    The shape the published API reference gives: an AtomPub `<service>` whose
    `<collection href=...>` points at `profile.do?userid=<uuid>&output=vcard`
    and carries `<snx:userid>` inline. That inline id is the whole point of
    the document: it is the cheapest way for an export running as a person to
    learn their own directory GUID, and it is the id an author filter matches
    on.
    """
    userid = person_uid(name)
    service = ET.Element(_q(APP_NS, "service"))
    workspace = ET.SubElement(service, _q(APP_NS, "workspace"))
    ET.SubElement(workspace, _q(ATOM_NS, "title")).text = "Profiles"
    collection = ET.SubElement(workspace, _q(APP_NS, "collection"))
    collection.set("href", f"{base_url}/profiles/atom/profile.do?userid={userid}&output=vcard")
    title = ET.SubElement(collection, _q(ATOM_NS, "title"))
    title.set("type", "text")
    title.text = name
    ET.SubElement(collection, _q(SNX_NS, "userid")).text = userid
    ET.SubElement(collection, _q(SNX_NS, "editableFields")).text = ""
    return _to_bytes(service)
