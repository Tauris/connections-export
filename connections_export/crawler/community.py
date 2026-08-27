"""Discover what a community is made of: its wiki, blogs and forums.

Lifted out of the console's `/api/community-components` endpoint, which was the
only implementation -- so the command line could not expand a community URL by
itself and had to be handed the components by whoever already knew them.

The two things that made it console-only are injected rather than imported:

  `fetch(url) -> bytes | None`
      Retrieve a URL, or `None` for anything that failed. The console passes an
      authenticated lookup; a crawl passes its own client.
  `redirect_location(url) -> str | None`
      The `Location` header of a redirect, or `None`. A community wiki is often
      named only by the redirect from `/wikis/communitywiki?communityUuid=...`,
      and following it needs a real client -- which this module deliberately
      does not build.

Returns `{"components": [...], "forums": [...], "community": <title|None>}`,
each component carrying the `wiki`/`blog`/`ideation_blog`/`forum` kind that the
console and the CLI both use to name a selection.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: Every `kind` this module can put on a discovered component. Declared rather
#: than inferred, because it is the SOURCE of the `--component kind:id`
#: vocabulary the console echoes and the CLI parses: the console builds its
#: checkbox values straight from what discovery returns, so a kind invented
#: here and unknown to `apps.APPS` produces a command that looks right and is
#: rejected. That is not hypothetical -- `rich_content` was emitted here and
#: missing from the CLI's list for as long as both existed.
#:
#: `tests/gui/test_cli_echo.py` pins this against `cli.COMPONENT_KINDS`.
EMITTED_COMPONENT_KINDS: frozenset[str] = frozenset(
    {"wiki", "blog", "ideation_blog", "forum", "files", "rich_content"}
)


def discover_components(
    *,
    community_uuid: str,
    base_url: str,
    fetch: Callable[[str], bytes | None],
    redirect_location: Callable[[str], str | None] | None = None,
) -> dict:
    """What `community_uuid` contains, as far as `fetch` can see."""
    community_uuid = (community_uuid or "").strip()
    if not community_uuid or not base_url:
        return {"components": [], "forums": [], "detail": "missing community or base URL"}

    import re  # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
    from urllib.parse import urljoin, urlsplit  # noqa: PLC0415

    import lxml.etree  # noqa: PLC0415

    from connections_export.adapters import atom  # noqa: PLC0415
    from connections_export.adapters.atom import feed_total_results  # noqa: PLC0415
    from connections_export.adapters.blogs import (  # noqa: PLC0415
        blogs_list_url,
        entries_feed_url,
        parse_blogs_feed,
    )
    from connections_export.adapters.forums import (  # noqa: PLC0415
        forums_list_url,
        parse_forums_feed,
        parse_topics_feed,
        topics_url,
    )
    from connections_export.adapters.wikis import parse_wikis_feed, wikis_feed_url  # noqa: PLC0415

    components: list[dict[str, Any]] = []

    def parse_or_empty(parser, payload: bytes | None):
        if not payload:
            return []
        try:
            return parser(payload)
        except Exception:  # noqa: BLE001
            return []

    def community_service_components(payload: bytes | None) -> list[dict[str, str]]:
        """Read component links from the community Atom instance document."""
        if not payload:
            return []
        try:
            root = lxml.etree.fromstring(payload)
        except lxml.etree.XMLSyntaxError:
            return []
        out: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for link in root.xpath("//*[local-name()='link'][@href]"):
            href = urljoin(base_url, link.get("href") or "")
            path = urlsplit(href).path
            title = " ".join(
                link.xpath(
                    "ancestor::*[local-name()='entry' or local-name()='collection'][1]"
                    "/*[local-name()='title']/text()"
                )
            )
            kind = None
            identifier = None
            if "/wikis/" in path and "/wiki/" in path:
                kind = "wiki"
                identifier = path.split("/wiki/", 1)[1].split("/", 1)[0]
            elif "/blogs/" in path:
                kind = "ideation_blog" if "ideation" in title.lower() else "blog"
                identifier = path.split("/blogs/", 1)[1].split("/", 1)[0]
            elif "/forums/" in path:
                kind = "forum"
                identifier = link.get("data-forum-uuid") or ""
            if kind and identifier and (kind, identifier) not in seen:
                out.append({"kind": kind, "id": identifier, "title": title or identifier})
                seen.add((kind, identifier))
        raw = payload.decode("utf-8", "replace")
        escaped = raw.replace("\\/", "/")
        blog_match = re.search(r"/blogs/([0-9a-f-]{36})(?:[?\\\"'])", escaped, re.I)
        if blog_match and ("blog", blog_match.group(1)) not in seen:
            out.append({"kind": "blog", "id": blog_match.group(1), "title": "Blog"})
            seen.add(("blog", blog_match.group(1)))
        wiki_match = re.search(
            r"/wikis/communitywiki\?communityUuid=([0-9a-f-]{36})", escaped, re.I
        )
        if wiki_match and ("wiki", "communitywiki") not in seen:
            out.append({"kind": "wiki", "id": "communitywiki", "title": "Wiki"})
            seen.add(("wiki", "communitywiki"))
        return out

    def service_components(url: str, kind: str) -> list[dict[str, str]]:
        payload = fetch(url)
        if not payload:
            return []
        try:
            root = lxml.etree.fromstring(payload)
        except lxml.etree.XMLSyntaxError:
            return []
        out: list[dict[str, str]] = []
        for workspace in root.xpath("//*[local-name()='workspace']"):
            terms = {value for value in workspace.xpath(".//*[local-name()='category']/@term")}
            if kind == "blog" and not terms.intersection({"communityblog", "ideationblog"}):
                continue
            entry_links = workspace.xpath(
                ".//*[local-name()='collection']"
                "[.//*[local-name()='category' and @term='entries']]/@href"
            )
            if not entry_links:
                continue
            href = urljoin(base_url, entry_links[0])
            match = re.search(r"/blogs/([^/]+)/", urlsplit(href).path)
            if not match:
                continue
            blog_kind = "ideation_blog" if "ideationblog" in terms else "blog"
            out.append(
                {
                    "kind": blog_kind,
                    "id": match.group(1),
                    "title": " ".join(workspace.xpath("./*[local-name()='title']/text()"))
                    or match.group(1),
                }
            )
        return out

    feed_urls = {
        "blogs": blogs_list_url(base_url=base_url, homepage="homepage"),
        "forums": forums_list_url(base_url=base_url) + "?communityUuid=" + community_uuid,
        "wikis": wikis_feed_url(base_url=base_url, auth_root="basic"),
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        fetched = dict(zip(feed_urls, pool.map(fetch, feed_urls.values()), strict=True))

    community_service = fetch(
        f"{base_url}/communities/service/atom/community/instance?communityUuid={community_uuid}"
    )
    community_feeds = fetch(
        f"{base_url}/communities/service/atom/community/feeds?communityUuid={community_uuid}"
    )
    if community_feeds:
        try:
            feeds_root = lxml.etree.fromstring(community_feeds)
            existing = {(c["kind"], c["id"]) for c in components}
            for entry in feeds_root.xpath("//*[local-name()='entry']"):
                title = " ".join(entry.xpath("./*[local-name()='title']/text()"))
                for link in entry.xpath("./*[local-name()='link'][@href]"):
                    href = urljoin(base_url, link.get("href") or "")
                    path = urlsplit(href).path
                    kind = None
                    identifier = None
                    if "/blogs/roller-ui/rendering/feed/" in path:
                        kind = "blog"
                        identifier = path.split("/blogs/roller-ui/rendering/feed/", 1)[1].split(
                            "/", 1
                        )[0]
                    elif "/forums/atom/topics" in path:
                        kind = "forum"
                    if kind == "forum":
                        continue
                    if kind and identifier and (kind, identifier) not in existing:
                        components.append(
                            {"kind": kind, "id": identifier, "title": title or identifier}
                        )
                        existing.add((kind, identifier))
        except lxml.etree.XMLSyntaxError:
            pass
    community_service_url = None
    if community_service:
        try:
            instance_root = lxml.etree.fromstring(community_service)
            service_links = instance_root.xpath(
                "//*[local-name()='link'][contains(@rel, '/service')]/@href"
            )
            community_service_url = urljoin(base_url, service_links[0]) if service_links else None
        except lxml.etree.XMLSyntaxError:
            pass
    if community_service_url:
        for component in service_components(community_service_url, "blog"):
            if (component["kind"], component["id"]) not in {
                (c["kind"], c["id"]) for c in components
            }:
                components.append(component)
    for component in community_service_components(community_service):
        components.append(component)

    service_blogs = service_components(f"{base_url}/blogs/api", "blog")
    service_blog_titles = {
        (component["kind"], component["id"]): component["title"] for component in service_blogs
    }
    for component in components:
        key = (component["kind"], component["id"])
        if key in service_blog_titles:
            component["title"] = service_blog_titles[key]

    blogs = parse_or_empty(parse_blogs_feed, fetched["blogs"])
    for blog in blogs:
        if blog.community_uuid == community_uuid:
            kind = (
                "ideation_blog"
                if blog.blog_type in {"ideationblog", "communityideationblog"}
                else "blog"
            )
            component = {"kind": kind, "id": blog.uuid, "title": blog.title or blog.uuid}
            if (kind, blog.uuid) not in {(c["kind"], c["id"]) for c in components}:
                components.append(component)

    forums = parse_or_empty(
        parse_forums_feed,
        fetched["forums"],
    )
    if not forums:
        service = fetch(f"{base_url}/forums/atom/service")
        if service:
            try:
                service_root = lxml.etree.fromstring(service)
                forum_links = service_root.xpath(
                    "//*[local-name()='collection']"
                    "[.//*[local-name()='category' and @term='Forums']]/@href"
                )
                if forum_links:
                    forums = parse_or_empty(
                        parse_forums_feed, fetch(urljoin(base_url, forum_links[0]))
                    )
            except lxml.etree.XMLSyntaxError:
                pass
    if not forums:
        community_topics_url = (
            f"{base_url}/communities/service/atom/community/forum/topics"
            f"?communityUuid={community_uuid}"
        )
        community_topics = parse_or_empty(parse_topics_feed, fetch(community_topics_url))
        forum_ids = []
        for topic in community_topics:
            if topic.forum_uuid and topic.forum_uuid not in forum_ids:
                forum_ids.append(topic.forum_uuid)
        forums = [
            type(
                "CommunityForum",
                (),
                {
                    "uuid": forum_id,
                    "title": atom.feed_title(
                        fetch(topics_url(base_url=base_url, forum_uuid=forum_id))
                    )
                    or forum_id,
                },
            )()
            for forum_id in forum_ids
        ]
    components.extend(
        {"kind": "forum", "id": forum.uuid, "title": forum.title or forum.uuid} for forum in forums
    )

    wikis = parse_or_empty(parse_wikis_feed, fetched["wikis"])
    components.extend(
        {"kind": "wiki", "id": wiki.label, "title": wiki.title or wiki.label}
        for wiki in wikis
        if getattr(wiki, "community_uuid", None) == community_uuid
    )

    if not any(c["kind"] == "wiki" for c in components) or not any(
        c["kind"] in {"blog", "ideation_blog"} for c in components
    ):
        import lxml.html  # noqa: PLC0415

        community_page = fetch(
            f"{base_url}/communities/service/html/communitystart?communityUuid={community_uuid}"
        )
        if community_page:
            try:
                tree = lxml.html.fromstring(community_page)
                seen: set[tuple[str, str]] = {
                    (component["kind"], component["id"]) for component in components
                }
                for anchor in tree.xpath("//a[@href]"):
                    href = urljoin(base_url, anchor.get("href") or "")
                    text = " ".join(anchor.itertext()).strip()
                    path = urlsplit(href).path
                    if "/wikis/" in path and "/wiki/" in path:
                        label = path.split("/wiki/", 1)[1].split("/", 1)[0]
                        key = ("wiki", label)
                        if key not in seen:
                            components.append({"kind": "wiki", "id": label, "title": text or label})
                            seen.add(key)
                    elif "/blogs/" in path:
                        kind = "ideation_blog" if "ideation" in text.lower() else "blog"
                        blog_id = path.split("/blogs/", 1)[1].split("/", 1)[0]
                        key = (kind, blog_id)
                        if blog_id and key not in seen:
                            components.append(
                                {"kind": kind, "id": blog_id, "title": text or blog_id}
                            )
                            seen.add(key)
                raw = community_page.decode("utf-8", "replace").replace("\\/", "/")
                blog_match = re.search(r"/blogs/([0-9a-f-]{36})(?:[?\\\"'])", raw, re.I)
                if blog_match:
                    components[:] = [
                        component
                        for component in components
                        if component["kind"] not in {"blog", "ideation_blog"}
                    ]
                    components.append({"kind": "blog", "id": blog_match.group(1), "title": "Blog"})
                wiki_match = re.search(r"/wikis/home/wiki/([^/\"?]+)", raw, re.I)
                if not wiki_match:
                    wiki_page = fetch(
                        f"{base_url}/wikis/communitywiki?communityUuid={community_uuid}"
                    )
                    if wiki_page:
                        wiki_match = re.search(
                            r"/wikis/home/wiki/([^/\"?]+)",
                            wiki_page.decode("utf-8", "replace"),
                            re.I,
                        )
                if not wiki_match and redirect_location is not None:
                    # A community wiki is often named only by the redirect from
                    # `/wikis/communitywiki?communityUuid=...`. Injected, so this
                    # module needs no HTTP client of its own and no import back
                    # into whichever caller has one.
                    location = (
                        redirect_location(
                            f"{base_url}/wikis/communitywiki?communityUuid={community_uuid}"
                        )
                        or ""
                    )
                    redirect_match = re.search(r"/wikis/home/wiki/([^/?]+)", location, re.I)
                    if redirect_match:
                        wiki_match = redirect_match
                if wiki_match:
                    components[:] = [
                        component for component in components if component["kind"] != "wiki"
                    ]
                    components.append({"kind": "wiki", "id": wiki_match.group(1), "title": "Wiki"})
            except (TypeError, ValueError, lxml.etree.ParserError):
                pass

    def count_url(component: dict[str, str]) -> str:
        if component["kind"] == "forum":
            return topics_url(base_url=base_url, forum_uuid=component["id"])
        if component["kind"] == "blog":
            return entries_feed_url(base_url=base_url, blog_uuid=component["id"])
        if component["kind"] == "ideation_blog":
            return entries_feed_url(base_url=base_url, blog_uuid=component["id"])
        return f"{base_url}/wikis/basic/api/wiki/{component['id']}/feed"

    def component_count(component: dict[str, str]) -> None:
        raw = fetch(count_url(component))
        try:
            component["count"] = feed_total_results(raw) if raw else None
        except Exception:  # noqa: BLE001
            component["count"] = None

    with ThreadPoolExecutor(max_workers=max(1, len(components))) as pool:
        list(pool.map(component_count, components))
    # Files. Unlike the others, nothing has to be DISCOVERED: the library is
    # addressed by the community uuid itself, which the caller already has. So
    # this asks the library feed one question -- does it exist, and how much is
    # in it -- rather than hunting for an identifier.
    #
    # A live capture settled that the community service document names no Files
    # link at all, so there is nothing here to parse out of it.
    from connections_export.adapters.files import (  # noqa: PLC0415
        community_library_url,
        parse_library_feed,
    )

    library_body = fetch(community_library_url(base_url=base_url, community_uuid=community_uuid))
    if library_body:
        try:
            library_files = parse_library_feed(library_body)
        except Exception:  # noqa: BLE001 - an unreadable library is "no files"
            library_files = []
        if library_files:
            components.append(
                {
                    "kind": "files",
                    "id": community_uuid,
                    "title": "Files",
                    # An int, like every other kind's count: the picker sums
                    # counts for its "all N items" total and drops anything
                    # that is not a finite number, so a string count would go
                    # missing from the total rather than show up wrong.
                    "count": len(library_files),
                }
            )

    # Rich Content. Like Files, nothing has to be DISCOVERED in the sense of
    # hunting for an identifier: the widget layout is addressed by the
    # community uuid the caller already has. Unlike Files, the layout is not a
    # content feed -- it lists every widget the community has, of every
    # application, so the rich content ones are selected out of it.
    #
    # The layout is readable by an ordinary member, which is what
    # makes this offerable to everyone rather than only to community owners.
    from connections_export.adapters.rte import (  # noqa: PLC0415
        parse_widget_layout,
        widget_layout_url,
    )

    layout_url = widget_layout_url(base_url=base_url, community_uuid=community_uuid)
    layout_body = fetch(layout_url)
    # Why Rich Content was or was not offered. A community that plainly HAS
    # rich content and is offered none gives a user nothing to go on -- and the
    # three causes (never answered / answered with something unreadable /
    # answered with no RTE widgets in it) need completely different fixes.
    rte_note: str | None = None
    if not layout_body:
        rte_note = f"no response from the widget layout ({layout_url})"
    else:
        try:
            widgets = parse_widget_layout(layout_body, community_uuid=community_uuid)
        except Exception as exc:  # noqa: BLE001 - never fail the whole lookup
            widgets = []
            rte_note = f"the widget layout could not be read: {exc}"
        written = [w for w in widgets if w.initialized]
        if not widgets:
            rte_note = rte_note or (
                "the widget layout was read but named no rich content for this "
                f"community ({len(layout_body)} bytes)"
            )
        elif not written:
            rte_note = (
                f"{len(widgets)} rich content areas are placed on this community, "
                "none of them written in"
            )
        if written:
            components.append(
                {
                    "kind": "rich_content",
                    "id": community_uuid,
                    "title": "Highlights",
                    # What would actually be CAPTURED, not what was placed. An
                    # area an owner never wrote in has no page to fetch, so
                    # counting it here would promise content that cannot come.
                    "count": len(written),
                    # The gap is still reported, because it is the difference
                    # between "this community has two pages" and "two pages and
                    # a space nobody used".
                    "placed": len(widgets),
                }
            )

    # The community's own name, for whoever needs to call this run
    # something -- the archive directory does.
    community_title = None
    if community_service:
        try:
            community_title = " ".join(
                lxml.etree.fromstring(community_service).xpath("/*/*[local-name()='title']/text()")
            ).strip()
        except lxml.etree.XMLSyntaxError:
            community_title = None
    return {
        "components": components,
        "forums": [component for component in components if component["kind"] == "forum"],
        "community": community_title or None,
        # Present only when Rich Content was NOT offered, saying which of the
        # three causes it was. A community that plainly has rich content and is
        # offered none is otherwise indistinguishable from one that has none.
        **({"rich_content_note": rte_note} if rte_note else {}),
    }
