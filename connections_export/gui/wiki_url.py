"""`parse_wiki_url`: identify an HCL
deployment + wiki from a wiki page URL a user drags or pastes into the
setup screen.

Pure, dependency-free, and deliberately conservative: it recognises two
shapes --

- the **Atom API URL**, `https://host/wikis/{auth}/api/wiki/{label}/...`
  (optionally with a port and/or a context root before `/wikis/`;
  `{auth}` is `basic`, `basic/anonymous`, `form`, or
  `oauth` -- `connections_export.config.AuthRoot`'s own values, mirroring
  `adapters/wikis.py`'s URL builders), and
- the **human UI URL**, either the hash-route form
  (`https://host/wikis/home?...#/wiki/{label}/page/...`) or the bare
  form (`https://host/wikis/.../wiki/{label}`).

Anything else returns `ok=False` with a `reason` -- never a guessed
`base_url`/`wiki_label` (Unknown shapes return a clear
'couldn't identify' result, never a wrong guess). The real UI URL forms are
unconfirmed, so this matcher is
deliberately a heuristic over the shapes we *do* know, easy to extend
with more shapes later rather than a parser that claims completeness.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

#: `connections_export.config.AuthRoot`'s single-segment values. `basic/anonymous`
#: is handled separately below since it is *two* path segments
#: (`.../wikis/basic/anonymous/api/...`), not one containing a literal
#: slash.
_AUTH_SINGLE_SEGMENT = {"basic", "form", "oauth"}


@dataclass(frozen=True)
class ParsedTarget:
    """What a dropped/pasted URL told us -- or didn't. `ok=False` means
    nothing was identified and `reason` explains why, so the setup screen
    can show it and let the user fill the fields in by hand.

    `app` is which HCL app the URL points at (`wiki` | `blog` | `forum`),
    with the app-specific target alongside: `wiki_label` for a wiki,
    `blog_handle` for a blog, `forum_uuid` for a forum (when the URL
    carried one). `base_url` is the deployment root (+ context root)."""

    base_url: str | None
    wiki_label: str | None
    auth_root: str | None
    ok: bool
    reason: str | None = None
    app: str | None = None
    community_uuid: str | None = None
    #: For wikis: the specific page label from a `.../page/{label}` URL, if the
    #: dropped URL points at one page rather than the whole wiki.
    page_label: str | None = None
    blog_handle: str | None = None
    forum_uuid: str | None = None
    #: ``"all"`` (root/homepage URL) or ``"single"`` (specific entry/page/thread).
    scope: str = "all"
    #: For blogs: the entry slug from a specific-entry URL, if ``scope=="single"``.
    entry_slug: str | None = None
    #: For forums: the topic id from a specific-thread URL, if ``scope=="single"``.
    topic_id: str | None = None


def _not_identified(reason: str) -> ParsedTarget:
    return ParsedTarget(base_url=None, wiki_label=None, auth_root=None, ok=False, reason=reason)


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _base_url(scheme: str, netloc: str, context_segments: list[str]) -> str:
    context_root = "/" + "/".join(context_segments) if context_segments else ""
    return f"{scheme}://{netloc}{context_root}"


def _try_api_shape(scheme: str, netloc: str, path_segments: list[str]) -> ParsedTarget | None:
    """`.../wikis/{auth}/api/wiki/{label}/...` -- the Atom API URL any
    adapter request in this codebase actually makes (`adapters/wikis.py`'s
    `*_url` builders)."""
    for index, segment in enumerate(path_segments):
        if segment != "wikis":
            continue
        rest = path_segments[index + 1 :]
        if len(rest) >= 3 and rest[0] == "basic" and rest[1] == "anonymous" and rest[2] == "api":
            auth_root = "basic/anonymous"
            after_api = rest[3:]
        elif len(rest) >= 2 and rest[0] in _AUTH_SINGLE_SEGMENT and rest[1] == "api":
            auth_root = rest[0]
            after_api = rest[2:]
        else:
            continue
        if len(after_api) >= 2 and after_api[0] == "wiki" and after_api[1]:
            return ParsedTarget(
                base_url=_base_url(scheme, netloc, path_segments[:index]),
                wiki_label=after_api[1],
                auth_root=auth_root,
                ok=True,
                app="wiki",
                page_label=_find_page_label(after_api),
                scope="single" if _find_page_label(after_api) else "all",
            )
    return None


def _try_community_shape(
    scheme: str, netloc: str, path_segments: list[str], query: str
) -> ParsedTarget | None:
    """Community start/overview URLs carrying ``communityUuid``."""
    if "communities" not in path_segments:
        return None
    from urllib.parse import parse_qs  # noqa: PLC0415

    community_uuid = (parse_qs(query).get("communityUuid") or [None])[0]
    if not community_uuid:
        return None
    index = path_segments.index("communities")
    return ParsedTarget(
        base_url=_base_url(scheme, netloc, path_segments[:index]),
        wiki_label=None,
        auth_root=None,
        ok=True,
        app="community",
        community_uuid=community_uuid,
        scope="all",
    )


def _find_page_label(segments: list[str]) -> str | None:
    """The `page/{label}` label in `segments` (API or human UI form), so a
    dropped single-page URL captures *that* page, not the wiki's first."""
    for index, segment in enumerate(segments):
        if segment == "page" and index + 1 < len(segments) and segments[index + 1]:
            return segments[index + 1]
    return None


def _find_wiki_label(segments: list[str]) -> str | None:
    """The first `wiki/{label}` pair in `segments` that isn't the API
    shape's `api/wiki/{label}` (handled separately, with its own
    `auth_root`) -- so a human-UI URL is never misread as an API one."""
    for index, segment in enumerate(segments):
        if segment != "wiki" or index + 1 >= len(segments):
            continue
        if index > 0 and segments[index - 1] == "api":
            continue
        label = segments[index + 1]
        if label:
            return label
    return None


def _try_human_shape(
    scheme: str, netloc: str, path_segments: list[str], fragment: str
) -> ParsedTarget | None:
    """The human UI URL: hash-route (`#/wiki/{label}/...`) or bare
    (`/wikis/.../wiki/{label}`) -- both always sit under `/wikis/...`,
    which anchors where the deployment's context root ends."""
    wikis_index = next((i for i, s in enumerate(path_segments) if s == "wikis"), None)
    if wikis_index is None:
        return None

    label = _find_wiki_label(path_segments)
    if label is None and fragment:
        label = _find_wiki_label(_segments(fragment))
    if label is None:
        return None

    page_label = _find_page_label(path_segments) or (
        _find_page_label(_segments(fragment)) if fragment else None
    )
    return ParsedTarget(
        base_url=_base_url(scheme, netloc, path_segments[:wikis_index]),
        wiki_label=label,
        auth_root=None,
        ok=True,
        app="wiki",
        page_label=page_label,
        scope="single" if page_label else "all",
    )


def parse_wiki_url(url: str) -> ParsedTarget:
    """Identify the deployment `base_url` and `wiki_label` (plus
    `auth_root`, for an API URL) from a dropped/pasted wiki page `url`.
    Never guesses: an unrecognised shape returns `ok=False` with a
    `reason`, and the caller (the setup screen, via `/api/identify`)
    lets the user fill the fields in manually."""
    try:
        split = urlsplit(url)
    except ValueError:
        return _not_identified("not a parseable URL")

    if split.scheme not in ("http", "https") or not split.netloc:
        return _not_identified("not an absolute http(s) URL")

    path_segments = _segments(split.path)

    api_result = _try_api_shape(split.scheme, split.netloc, path_segments)
    if api_result is not None:
        return api_result

    human_result = _try_human_shape(split.scheme, split.netloc, path_segments, split.fragment)
    if human_result is not None:
        return human_result

    return _not_identified(
        "couldn't identify a wiki page in this URL (no recognised /wiki/{label} shape)"
    )


def _try_blog_shape(scheme: str, netloc: str, path_segments: list[str]) -> ParsedTarget | None:
    """A Blogs URL: anything under `/blogs/{handle}/...` -- the Atom API
    (`/blogs/{handle}/feed/...`, `adapters/blogs.py`'s builders) and the
    human UI both sit under the blog's handle. `{handle}` doubles as the
    homepage handle for the list feed. The exact UI URL forms are
    unconfirmed (first contact), so this is a heuristic over `/blogs/`."""
    for index, segment in enumerate(path_segments):
        if segment != "blogs":
            continue
        rest = path_segments[index + 1 :]
        if not rest or not rest[0]:
            continue
        handle = rest[0]
        # Specific entry: /blogs/{handle}/entry/{slug}
        if len(rest) >= 3 and rest[1] == "entry" and rest[2]:
            return ParsedTarget(
                base_url=_base_url(scheme, netloc, path_segments[:index]),
                wiki_label=None,
                auth_root=None,
                ok=True,
                app="blog",
                blog_handle=handle,
                scope="single",
                entry_slug=rest[2],
            )
        return ParsedTarget(
            base_url=_base_url(scheme, netloc, path_segments[:index]),
            wiki_label=None,
            auth_root=None,
            ok=True,
            app="blog",
            blog_handle=handle,
            scope="all",
        )
    return None


def _try_forum_shape(
    scheme: str, netloc: str, path_segments: list[str], query: str
) -> ParsedTarget | None:
    """A Forums URL: anything under `/forums/...` (the Atom API is
    `/forums/atom/{forums,topics,replies}?...Uuid=`, `adapters/forums.py`).
    UI forms recognised:
    - `/forums/html/forum?id={forum_uuid}` → whole forum (scope=all)
    - `/forums/html/threadTopic?id={topic_uuid}` → single topic (scope=single)
    A `forumUuid` query param also scopes to one forum; a `topicUuid` query
    param (the Atom API single-topic/replies shape) scopes to one topic.

    The bare listing (`/forums/html/forums` or `/forums/atom/forums`, no id
    at all) is the one deliberate "whole app" shape -- it deliberately
    returns `forum_uuid=None` (setup screen: "leave blank for all forums",
    paired with an author filter for "capture everything I authored").
    Anything else under `/forums/...` that carries no recognisable id is
    reported unidentified (`None`, so `parse_url` falls through to
    "couldn't identify") rather than silently treated as that same
    "whole app" shape -- a forum URL this parser doesn't recognise (e.g. a
    community-hosted forum's own UI route) must never be silently widened
    into a company-wide crawl of every forum (An unrecognised URL
    is reported, not guessed)."""
    if "forums" not in path_segments:
        return None
    index = path_segments.index("forums")
    from urllib.parse import parse_qs  # noqa: PLC0415

    qs = parse_qs(query)
    id_value = (qs.get("id") or [None])[0]
    explicit_forum_uuid = (qs.get("forumUuid") or [None])[0]
    topic_uuid_value = (qs.get("topicUuid") or [None])[0]
    last_segment = path_segments[-1] if path_segments else ""

    if last_segment == "threadTopic":
        # Specific topic page: /forums/html/threadTopic?id={topic_uuid}.
        forum_uuid = explicit_forum_uuid
        topic_id = id_value
        scope = "single" if topic_id else "all"
    elif topic_uuid_value:
        # The Atom API single-topic shapes: /forums/atom/topic?topicUuid=
        # or /forums/atom/replies?topicUuid=.
        forum_uuid = explicit_forum_uuid
        topic_id = topic_uuid_value
        scope = "single"
    elif explicit_forum_uuid or id_value:
        # A known "whole forum" shape carrying an id: /forums/html/forum?id=,
        # or the Atom API /forums/atom/topics|entries?forumUuid=.
        forum_uuid = explicit_forum_uuid or id_value
        topic_id = None
        scope = "all"
    elif last_segment == "forums":
        # The bare "all forums" listing -- the one deliberate no-id shape.
        forum_uuid = None
        topic_id = None
        scope = "all"
    else:
        # Some other /forums/... URL with no id we recognise -- do not guess
        # "whole app"; report unidentified instead (see docstring).
        return None

    return ParsedTarget(
        base_url=_base_url(scheme, netloc, path_segments[:index]),
        wiki_label=None,
        auth_root=None,
        ok=True,
        app="forum",
        forum_uuid=forum_uuid,
        scope=scope,
        topic_id=topic_id,
    )


def parse_url(url: str) -> ParsedTarget:
    """Identify which HCL app (wiki | blog | forum) a dropped/pasted `url`
    points at, plus the deployment `base_url` and the app-specific target.
    Tries wiki (API then human UI), then blog, then forum -- the path
    prefixes (`/wikis/`|`/wiki/`, `/blogs/`, `/forums/`) don't overlap.
    Never guesses: an unrecognised URL returns `ok=False` + `reason`, and
    the setup screen lets the user fill the fields in by hand."""
    try:
        split = urlsplit(url)
    except ValueError:
        return _not_identified("not a parseable URL")
    if split.scheme not in ("http", "https") or not split.netloc:
        return _not_identified("not an absolute http(s) URL")

    segs = _segments(split.path)
    for result in (
        _try_community_shape(split.scheme, split.netloc, segs, split.query),
        _try_api_shape(split.scheme, split.netloc, segs),
        _try_human_shape(split.scheme, split.netloc, segs, split.fragment),
        _try_blog_shape(split.scheme, split.netloc, segs),
        _try_forum_shape(split.scheme, split.netloc, segs, split.query),
    ):
        if result is not None:
            return result

    return _not_identified(
        "couldn't identify a wiki, blog, or forum in this URL "
        "(no /wiki/{label}, /blogs/{handle}, or /forums/ shape)"
    )
