"""What more than one app's assembler needs from the archive.

Feed discovery, page-size recovery, comment threading, version and attachment
derivation, and the regexes that recognise an archived feed URL. Everything
here was already shared between two or more of the six assemblers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qs, parse_qsl, urlsplit, urlunsplit

from connections_export.adapters.model import (
    Attachment,
    Version,
)
from connections_export.archive.records import RunMetadata
from connections_export.archive.store import Archive
from connections_export.derive.assets import resolve_asset
from connections_export.derive.index import ArchiveIndex
from connections_export.derive.model import (
    DerivedAttachment,
    DerivedComment,
    DerivedVersion,
    ResolvedAsset,
)

_WIKIS_FEED_TAIL = "/wikis/feed"
_NAV_FEED_TAIL = "/nav/feed"

#: Path suffix of the Blogs "list all blogs" feed
#: (`/blogs/{homepage}/feed/blogs/atom`) -- the one archived URL that
#: reveals a blogs crawl's own starting point, the Blogs analogue of the
#: wikis feed. `/feed/entries/atom` (a blog's own posts) and
#: `/feed/entrycomments/...` (a post's comments) end differently, so this
#: never matches a downstream blog feed.
_BLOGS_LIST_TAIL = "/feed/blogs/atom"

#: Path suffix of the Forums "all forums" list feed (`/forums/atom/forums`).
_FORUMS_LIST_TAIL = "/forums/atom/forums"

#: `/blogs/{handle}/feed/entries/atom` -> `{handle}` (legacy handle-in-path form).
_BLOG_ENTRIES_HANDLE_RE = re.compile(r"/blogs/(?P<handle>[^/]+)/feed/entries/atom")

#: `/blogs/roller-ui/rendering/feed/{uuid}/entries/atom` -> `{uuid}`.
#: Real HCL Connections deployments emit this roller-ui form as the per-blog self link.
_BLOG_ROLLER_UUID_RE = re.compile(r"/blogs/roller-ui/rendering/feed/(?P<uuid>[^/?#]+)/entries/atom")

#: Page size to assume when deriving a scoped archive that never fetched
#: the wikis feed (whose `ps=` is the usual source) and whose other feeds
#: happen to carry no `ps=` either. Mirrors `config.Config.page_size`.
_DEFAULT_PAGE_SIZE = 500


class DeriveError(Exception):
    """Derivation could not even locate its starting point in the
    archive -- neither a `.../wikis/feed?ps=..&page=1` request nor any
    `.../wiki/{label}/nav/feed` was ever archived. Distinct from a
    per-page/per-artifact gap, which is always represented via
    `Provenance.note`, never raised."""


def _discover_root(index: ArchiveIndex) -> tuple[str, int] | None:
    """Find the API root (`{base_url}/wikis/{auth_root}/api`) and the
    page size the archive was crawled with, from the one archived URL
    that reveals both: the wikis-feed's own first page (the crawler's
    page-walker always sends `ps=`/`page=1`, even for a feed that
    turns out to have only one page).

    Returns `None` (rather than raising) when neither a wikis feed nor
    any nav feed was archived -- a blogs-/forums-only archive has no
    wiki starting point, but is still derivable. `derive` raises
    `DeriveError` only when *no* app (wiki, blog, or forum) yields a
    starting point at all."""
    for url in index.ok_urls():
        split = urlsplit(url)
        if not split.path.endswith(_WIKIS_FEED_TAIL):
            continue
        query = dict(parse_qsl(split.query))
        if query.get("page") != "1":
            continue
        ps = query.get("ps")
        if ps is None:
            continue
        api_root = urlunsplit(
            (split.scheme, split.netloc, split.path[: -len(_WIKIS_FEED_TAIL)], "", "")
        )
        return api_root, int(ps)
    # Fallback: a wiki-scoped crawl never fetches the
    # wikis feed, so recover the API root from any archived nav-feed URL
    # and the page size from any paginated feed's `ps=` (else the default).
    nav_roots = _nav_feed_roots_and_labels(index)
    if nav_roots:
        api_root = next(iter(nav_roots.values()))
        return api_root, _page_size_from_feeds(index)

    return None


def _discover_list_feed(index: ArchiveIndex, tail: str) -> str | None:
    """The archived first page of a list feed whose path ends with
    `tail` (`/feed/blogs/atom` for Blogs, `/forums/atom/forums` for
    Forums) -- returned as its query-stripped feed base URL, exactly the
    shape `ArchiveIndex.paginate` re-adds `?ps=&page=` onto. `None` when
    that feed was never archived (this app simply wasn't crawled). Keyed
    on `page=1` like `_discover_root`: the crawler's page-walker always
    fetches page 1 first, so it is the reliable entry-point marker."""
    for url in index.ok_urls():
        split = urlsplit(url)
        if not split.path.endswith(tail):
            continue
        if dict(parse_qsl(split.query)).get("page") != "1":
            continue
        return urlunsplit((split.scheme, split.netloc, split.path, "", ""))
    return None


def _base_url_before(feed_base_url: str, marker: str) -> str:
    """The deployment `base_url` a list-feed URL was built off: the
    scheme+host plus whatever path precedes `marker` (`/blogs/` for the
    blogs list, `/forums/atom/forums` for the forums list). Recovers the
    boundary/join base for a blogs-/forums-only archive the same way
    `_base_url_of` recovers it from a wiki api root."""
    split = urlsplit(feed_base_url)
    idx = split.path.find(marker)
    base_path = split.path[:idx] if idx != -1 else ""
    return urlunsplit((split.scheme, split.netloc, base_path, "", ""))


def _nav_feed_api_root_and_label(url: str) -> tuple[str, str] | None:
    """Split a `{api_root}/wiki/{label}/nav/feed[?...]` URL into its
    `(api_root, label)`. Returns `None` for anything that isn't a
    nav-feed URL. `/wikis/` never matches the `/wiki/` marker (it has a
    trailing `s`, not a `/`), so the deployment context is never
    mistaken for the page path."""
    split = urlsplit(url)
    path = split.path
    marker = "/wiki/"
    if not path.endswith(_NAV_FEED_TAIL) or marker not in path:
        return None
    api_path = path[: path.index(marker)]
    if not api_path.endswith("/api"):
        return None
    after = path[path.index(marker) + len(marker) :]  # "{label}/nav/feed"
    label = after.split("/", 1)[0]
    if not label:
        return None
    api_root = urlunsplit((split.scheme, split.netloc, api_path, "", ""))
    return api_root, label


def _nav_feed_roots_and_labels(index: ArchiveIndex) -> dict[str, str]:
    """`{wiki_label: api_root}` for every archived nav feed, in archive
    order (insertion-ordered dict). Empty when no nav feed was archived.
    All labels share one `api_root`; the map both enumerates the scoped
    wikis and yields that root."""
    found: dict[str, str] = {}
    for url in index.ok_urls():
        parsed = _nav_feed_api_root_and_label(url)
        if parsed is None:
            continue
        api_root, label = parsed
        found.setdefault(label, api_root)
    return found


def _page_size_from_feeds(index: ArchiveIndex) -> int:
    """The `ps=` a paginated feed was crawled with, from any archived
    feed URL that carries one, else `_DEFAULT_PAGE_SIZE`."""
    for url in index.ok_urls():
        ps = dict(parse_qsl(urlsplit(url).query)).get("ps")
        if ps is not None:
            try:
                return int(ps)
            except ValueError:
                continue
    return _DEFAULT_PAGE_SIZE


def _base_url_of(api_root: str) -> str | None:
    """`{base_url}/wikis/{auth_root}/api` -> `base_url`, informational
    only (populates `Interchange.base_url`) -- every URL assembly
    actually looks up is built straight off `api_root`, never
    reassembled from this split."""
    idx = api_root.rfind("/wikis/")
    return api_root[:idx] if idx != -1 else None


_RUN_METADATA_GLOB = "run-*.json"


def _latest_run_metadata(archive: Archive) -> RunMetadata | None:
    """Scan the archive root for `run-*.json` run-provenance files and return the most recent one,
    or `None` if the
    archive carries none at all -- an older archive, or a partial
    capture, in which case callers fall back to URL recovery.

    Ordering is the source's, not this caller's: a directory can order
    by mtime, a zip entry has only a two-second `date_time`, and the
    difference is exactly the kind of thing a reader should not know.
    `run_metadata` hands back entries oldest first, so "most recent"
    is the last one.
    """
    entries = archive.source.run_metadata()
    if not entries:
        return None
    return RunMetadata.model_validate_json(entries[-1].data)


def thread_comments(comments: list) -> list[DerivedComment]:
    """A comment carrying `in_reply_to` gets `parent_comment_id` set to it; a
    comment without reply info is flat (`None`). No validation that the
    referenced id exists in the same list -- `in_reply_to` is carried through
    best-effort, exactly as parsed.

    Serves wiki, blog and file comments alike. Blogs had a threader of its own
    until the derived timestamp spellings were unified, at which point the two
    functions differed only in the class they built.

    Both adapter shapes key on `id`. They did not always: a wiki `Comment`
    keyed on `uuid` and a `BlogComment` on `id`, both meaning "the bare uuid of
    this comment", and this function absorbed the difference with a `getattr`.
    The adapters agree now, so it does not have to.
    """
    return [
        DerivedComment(
            id=comment.id,
            author=comment.author,
            author_userid=comment.author_userid,
            contributors=list(comment.contributors),
            content_html=comment.content_html,
            created=comment.published,
            # Wiki comments carry an edit date; blog comments do not. This
            # getattr is a real difference in what the two FEEDS report, not a
            # naming disagreement, so it stays.
            modified=getattr(comment, "updated", None),
            parent_comment_id=comment.in_reply_to,
        )
        for comment in comments
    ]


def _derive_versions(versions: list[Version], *, index: ArchiveIndex) -> list[DerivedVersion]:
    out = []
    for version in versions:
        content_present = bool(version.content_src) and index.get(version.content_src) is not None
        out.append(
            DerivedVersion(
                id=version.uuid,
                version_label=version.version_label,
                author=version.author,
                created=version.created,
                content_present=content_present,
            )
        )
    return out


def _derive_attachments(
    attachments: list[Attachment], *, base_url: str, hcl_hosts: Iterable[str], index: ArchiveIndex
) -> list[DerivedAttachment]:
    out = []
    for attachment in attachments:
        href = attachment.enclosure_href
        if href:
            asset = resolve_asset(
                href, join_base=base_url, boundary_base=base_url, hcl_hosts=hcl_hosts, index=index
            )
        else:
            asset = ResolvedAsset(
                original_href="", resolved_url="", present=False, scope="external"
            )
        out.append(
            DerivedAttachment(
                id=attachment.uuid,
                filename=attachment.filename,
                content_type=attachment.content_type,
                asset=asset,
            )
        )
    return out


def _attachment_filename(asset: ResolvedAsset, attachments: list[Attachment]) -> str | None:
    """Match body/download URL variants by their shared HCL nodeId."""
    node_ids = {
        value
        for href in (asset.original_href, asset.resolved_url)
        for value in (parse_qs(urlsplit(href).query).get("nodeId") or [])
    }
    for attachment in attachments:
        if not attachment.filename or not attachment.enclosure_href:
            continue
        attachment_ids = parse_qs(urlsplit(attachment.enclosure_href).query).get("nodeId") or []
        if node_ids.intersection(attachment_ids):
            return attachment.filename
    return None


def _feed_note(index: ArchiveIndex, label: str, url: str, *, page_size: int) -> str | None:
    """What went wrong reading one feed, if anything -- for `Provenance.note`.

    Three different shortfalls, all of which would otherwise be invisible in
    the derived model (project.md principle 2: a failure is never a silent
    gap). Call this AFTER the feed has been walked: whether the walk hit the
    safety cap, and whether it had a recording to replay, are only known once
    it has run.

    Ranked by how actionable they are. "We could not fetch this" outranks
    everything; a reconstructed read comes last, because it is frequently
    benign and burying a real failure under it would be a regression in what
    this note is for.
    """
    failure = index.first_page_failure(url, page_size=page_size)
    if failure is not None:
        detail = f": {failure.error}" if failure.error else ""
        return f"{label} feed fetch failed{detail}"
    if index.hit_pagination_cap(url):
        # The crawler answers this same condition with a `pagination_cap`
        # warning; derive has no event stream, so the note is where it lands.
        return f"{label} feed truncated at the {index.max_pages}-page safety cap"
    if index.reconstruction_hit_a_gap(url):
        # Narrow on purpose. Being reconstructed is not itself a problem --
        # every archive captured before the crawler recorded its walks is, and
        # they derive correctly. What is a problem is a reconstruction that ran
        # into a page missing from the manifest: the crawler's own recording
        # would have read PAST that hole, knowing the later pages were fetched,
        # and reconstruction cannot know it, so it stops there and returns less
        # than the archive holds. Reporting every reconstruction instead would
        # put this line on almost every legacy feed and teach people to ignore
        # it.
        return (
            f"{label} feed had no recorded walk and its rebuilt page sequence "
            "stopped at a page missing from the archive -- content after that "
            "page was captured but not read back"
        )
    return None


def _community_uuid(content: bytes | str) -> str | None:
    """Read the optional community ownership marker from a feed."""
    from connections_export.adapters.search import community_uuid_from_feed  # noqa: PLC0415

    return community_uuid_from_feed(content)


def _community_title(content: bytes | str) -> str | None:
    """Read the owning community's display name from a feed, when it carries
    one -- so a community can be named without fetching a community
    document."""
    from connections_export.adapters.search import community_title_from_feed  # noqa: PLC0415

    return community_title_from_feed(content)
