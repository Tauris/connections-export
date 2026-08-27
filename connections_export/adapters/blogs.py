"""Blogs parsers and URL construction.
The thick, app-specific layer on top of `atom.py`'s shared Atom core --
same pattern as `wikis.py`.

Lower confidence than wikis.py: the reference doc gives field lists but no literal
XML sample for any Blogs entry shape, so every fixture this module's
tests use is not documented (assembled from the field list), not a
byte-verbatim anchor. There is no fakeserver/round-trip for Blogs yet
-- these fixtures are the validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from connections_export.adapters import atom
from connections_export.adapters.model import BlogComment, BlogPost, BlogRef

# --------------------------------------------------------------------
# URL construction
# --------------------------------------------------------------------
# Blogs addresses a blog by handle in the path, with a feed/api split -- a
# different addressing philosophy from Forums, which uses query parameters.


def blogs_list_url(*, base_url: str, homepage: str) -> str:
    """`/blogs/{homepage}/feed/blogs/atom` -- "List all
    blogs"."""
    return f"{base_url}/blogs/{homepage}/feed/blogs/atom"


def entries_feed_url(*, base_url: str, blog_uuid: str, since: str | None = None) -> str:
    """`/blogs/roller-ui/rendering/feed/{uuid}/entries/atom` --
    the real HCL Connections blog entries feed URL. The blog UUID comes
    from the `<id>` LSID in the list feed (stripped to bare uuid by
    `entry_uuid`). Source: a working client's blog-URL discovery
    helpers; they use this roller-ui shape, not a handle-in-path one.
    The documented API reference stated a handle-in-path shape
    (`/blogs/{handle}/feed/entries/atom`) which appears to be wrong or
    a legacy path — the working client always uses the UUID form.

    `since` (RFC 3339) asks only for entries changed
    after that moment, with `sortBy=modified` so the server answers in
    modification order -- without it "changed since" and "published since"
    quietly differ for an edited old post."""
    url = f"{base_url}/blogs/roller-ui/rendering/feed/{blog_uuid}/entries/atom"
    if since:
        from connections_export.adapters import profiles  # noqa: PLC0415
        from connections_export.adapters.since import encode_for  # noqa: PLC0415

        return f"{url}?since={quote(encode_for(profiles.BLOGS, since), safe='')}&sortBy=modified"
    return url


def all_comments_feed_url(*, base_url: str, blog_uuid: str, since: str | None = None) -> str:
    """Every comment in one blog:
    `/blogs/roller-ui/rendering/feed/{uuid}/comments/atom`.

    Verified with `ps=150&page=0&since=...&sortBy=modified`:
    it returned the comment made during the experiment, and a future cutoff
    returned an empty feed rather than an error.

    This is what makes an incremental blog update possible. A blog comment
    moves neither the post nor the dated entries feed, so a dated walk cannot
    find new discussion. Without this feed the only way is to re-read every
    post's comments -- one request per post, on every update, to discover that
    almost none changed. One request per blog answers the same question.
    """
    url = f"{base_url}/blogs/roller-ui/rendering/feed/{blog_uuid}/comments/atom"
    if since:
        from connections_export.adapters import profiles  # noqa: PLC0415
        from connections_export.adapters.since import encode_for  # noqa: PLC0415

        return f"{url}?since={quote(encode_for(profiles.BLOGS, since), safe='')}&sortBy=modified"
    return url


@dataclass(frozen=True)
class ChangedComment:
    """One comment the aggregate feed reported, and what it hangs off.

    `parent_ref` is `<thr:in-reply-to@ref>`: the IMMEDIATE parent, which for a
    reply is a sibling comment rather than the entry. `parent_source` is the
    top-level entry when the server names one -- documented, and not always
    populated for blogs. A caller that cannot resolve either to a post it knows
    must fall back rather than guess: a comment filed against the wrong post is
    worse than a request saved.
    """

    id: str | None
    parent_ref: str | None
    parent_source: str | None


def parse_changed_comments(data: bytes | str) -> list[ChangedComment]:
    """The comments an aggregate feed reported, with their parent references.

    Deliberately not a `BlogComment`: this feed is used as a change INDEX, and
    the comment itself is re-read from its post's own feed afterwards. Parsing
    it as content would invite using a body that arrived without the thread it
    belongs to.
    """
    root = atom.parse_xml(data)
    changed: list[ChangedComment] = []
    for entry in atom.entries(root):
        reply_to = atom.in_reply_to(entry) or {}
        changed.append(
            ChangedComment(
                id=atom.entry_uuid(atom.find_text(entry, "atom:id")),
                parent_ref=atom.entry_uuid(reply_to.get("ref")) if reply_to.get("ref") else None,
                parent_source=(
                    atom.entry_uuid(reply_to.get("source")) if reply_to.get("source") else None
                ),
            )
        )
    return changed


def legacy_entries_feed_url(*, base_url: str, handle: str) -> str:
    """`/blogs/{handle}/feed/entries/atom` -- the handle-in-path
    form from the API reference doc. May work on older deployments or as an
    alternate URL, but a working client uses the roller-ui form (see
    `entries_feed_url`).
    Kept as a fallback; prefer `entries_feed_url` with the blog UUID."""
    return f"{base_url}/blogs/{handle}/feed/entries/atom"


def entry_comments_url(*, base_url: str, handle: str, entry_slug: str) -> str:
    """`/blogs/{handle}/feed/entrycomments/{entry-slug}/atom`."""
    return f"{base_url}/blogs/{handle}/feed/entrycomments/{entry_slug}/atom"


def service_doc_url(*, base_url: str) -> str:
    """`/blogs/api` (the legacy `/services/atom` form lacks
    the My Blogs workspace, per the reference doc)."""
    return f"{base_url}/blogs/api"


def single_entry_subscription_url(*, base_url: str, handle: str, entry_id: str) -> str:
    """[SAMPLE] `/blogs/{handle}/feed/entry/atom?entryid=<uuid>`. No
    documented page describes single-entry subscription retrieval;
    this literal path text appears in `rel="self"` links inside a
    documented sample feed (the sample feed itself isn't reproduced in
    the reference doc, only this path)."""
    return f"{base_url}/blogs/{handle}/feed/entry/atom?entryid={entry_id}"


# --------------------------------------------------------------------
# Small internal helpers
# --------------------------------------------------------------------


def _comments_enabled(entry) -> bool | None:
    """`<app:control><snx:comments enabled="yes|no".../></app:control>`."""
    el = entry.find("app:control/snx:comments", namespaces=atom.NS)
    if el is None:
        return None
    value = el.get("enabled")
    if value is None:
        return None
    return value.strip().lower() == "yes"


# --------------------------------------------------------------------
# Blogs list
# --------------------------------------------------------------------


def parse_blogs_feed(data: bytes | str) -> list[BlogRef]:
    """Parse the "list all blogs" feed into `BlogRef`s. # INFERRED: no
    field list is documented for this resource; only the generic
    cross-app Atom fields are read (see `BlogRef`)."""
    root = atom.parse_xml(data, expected_root="feed")
    refs: list[BlogRef] = []
    for entry in atom.entries(root):
        uuid, _raw = atom.require_entry_id(entry, what="blogs entry")
        refs.append(
            BlogRef(
                uuid=uuid,
                title=atom.find_text(entry, "atom:title"),
                self_url=atom.link_href(entry, "self"),
                community_uuid=atom.find_text(entry, "snx:communityUuid"),
                community_title=atom.find_text(entry, "snx:communityTitle"),
                blog_type=atom.find_text(entry, "snx:blogType"),
            )
        )
    return refs


# --------------------------------------------------------------------
# Entries feed
# --------------------------------------------------------------------


def parse_entries_feed(data: bytes | str) -> list[BlogPost]:
    """Parse a blog entries feed into `BlogPost`s. Required: `<id>`; everything else degrades to
    None/empty rather than raising."""
    root = atom.parse_xml(data, expected_root="feed")
    posts: list[BlogPost] = []
    for entry in atom.entries(root):
        uuid, raw_id = atom.require_entry_id(entry, what="blogs entry")
        posts.append(
            BlogPost(
                id=uuid,
                title=atom.find_text(entry, "atom:title"),
                author=atom.author_name(entry),
                author_userid=atom.author_userid(entry),
                contributors=atom.contributor_names(entry),
                content_html=atom.content_html(entry),
                published=atom.find_text(entry, "atom:published"),
                updated=atom.find_text(entry, "atom:updated"),
                tags=atom.bare_category_terms(entry),
                comments_enabled=_comments_enabled(entry),
                ranks=atom.ranks(entry),
                provenance=raw_id,
                alternate_url=atom.link_href(entry, "alternate"),
            )
        )
    return posts


# --------------------------------------------------------------------
# Entry comments feed
# --------------------------------------------------------------------


def parse_entry_comments_feed(data: bytes | str) -> list[BlogComment]:
    """Parse an entry-comments feed into `BlogComment`s. Threading is
    arbitrary-depth via `thr:in-reply-to/@ref`: blog comments use the same
    RFC 4685 threading as forum replies.
    `in_reply_to` is `None` for a top-level comment, or a sibling
    comment id for a reply (the derive layer reconstructs the tree)."""
    root = atom.parse_xml(data, expected_root="feed")
    comments: list[BlogComment] = []
    for entry in atom.entries(root):
        uuid, _raw = atom.require_entry_id(entry, what="blogs entry")
        reply_to = atom.in_reply_to(entry)
        # `ref` is normalized through entry_uuid so it's directly
        # comparable to sibling comments' `.id`. Blog comments use
        # `thr:in-reply-to/@ref` identically to forum replies.
        in_reply_to = atom.entry_uuid(reply_to["ref"]) if reply_to and reply_to["ref"] else None
        comments.append(
            BlogComment(
                id=uuid,
                author=atom.author_name(entry),
                author_userid=atom.author_userid(entry),
                contributors=atom.contributor_names(entry),
                content_html=atom.content_html(entry),
                published=atom.find_text(entry, "atom:published"),
                in_reply_to=in_reply_to,
            )
        )
    return comments


def parse_entries_with_comment_urls(data: bytes | str) -> list[tuple[BlogPost, str | None]]:
    """Parse a blog entries feed into `(BlogPost, comments_feed_url)` pairs --
    one per entry, in feed order -- so a caller can walk each post's comments.

    `parse_entries_feed` gives the normalized `BlogPost` (the load-bearing
    model, used for counts and the derived event) but does not surface the
    per-entry `rel="replies"` link, which is exactly what traversal needs and
    which the entries feed -- not the post's own model -- is the authority for.

    Both the crawler and derive need this identical read: the crawler to follow
    the link, derive to replay what the crawler followed. They had a copy each,
    byte-for-byte identical, and the derive copy explained itself as
    "replicated here (derive is forbidden to import crawler internals)". That
    constraint is real; the conclusion was not. It is a feed parser, so it
    belongs in the adapter, which both layers may import.

    Returning one item per entry keeps this a drop-in paginator parser -- the
    short-page stop condition still counts entries correctly.
    """
    posts = parse_entries_feed(data)
    root = atom.parse_xml(data, expected_root="feed")
    hrefs = [atom.link_href(entry, "replies") for entry in atom.entries(root)]
    return list(zip(posts, hrefs, strict=True))
