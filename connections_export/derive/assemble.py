"""`derive`: read one archive, assemble the interchange model.

The six per-app assemblers live in `derive/apps/`, one module each, mirroring
the `adapters/` norm. This module held all of them in 1,924 lines; what stays
here is the orchestration that calls them and stitches the result together --
which is a different job from any one app's assembly, and the reason it did
not move.
"""

from __future__ import annotations

from collections.abc import Iterable

from connections_export.adapters.model import (
    WikiRef,
)
from connections_export.adapters.wikis import (
    parse_wikis_feed,
)
from connections_export.archive.store import Archive
from connections_export.derive.apps._shared import (
    _BLOGS_LIST_TAIL,
    _FORUMS_LIST_TAIL,
    _WIKIS_FEED_TAIL,
    _base_url_before,
    _base_url_of,
    _discover_list_feed,
    _discover_root,
    _latest_run_metadata,
    _nav_feed_roots_and_labels,
    _page_size_from_feeds,
    thread_comments,
)
from connections_export.derive.apps.blogs import (
    _assemble_blog,
    _assemble_blogs,
    _discover_direct_blog_entries,
)
from connections_export.derive.apps.communities import (
    _assemble_communities,
    _community_logo,
)
from connections_export.derive.apps.files import (
    _COMMUNITY_LIBRARY_RE,
    _assemble_file_library,
    _discover_community_libraries,
)
from connections_export.derive.apps.forums import (
    _assemble_direct_topics,
    _assemble_forum,
    _assemble_forums,
    _discover_archived_topic_feeds,
    _discover_direct_forum_topics,
)
from connections_export.derive.apps.rich_content import (
    _assemble_rich_content,
    _discover_rich_content,
)
from connections_export.derive.apps.wikis import _assemble_wiki
from connections_export.derive.index import ArchiveIndex
from connections_export.derive.model import (
    DerivedBlog,
    DerivedForum,
    DerivedWiki,
    Interchange,
)


class DeriveError(Exception):
    """Derivation could not even locate its starting point in the
    archive -- neither a `.../wikis/feed?ps=..&page=1` request nor any
    `.../wiki/{label}/nav/feed` was ever archived. Distinct from a
    per-page/per-artifact gap, which is always represented via
    `Provenance.note`, never raised."""


def _community_for_label(archive, label: str) -> tuple[str | None, str | None]:
    """Which community a scoped wiki capture was for, from the run records.

    The wikis LIST feed is the only place `snx:communityUuid` appears, and a
    scoped crawl skips that feed by design, so the marker is not in the
    archive to be read. What IS in the archive is what each run was asked to
    do: a community capture records the community and the components it was
    given. This reads that back rather than guessing at a feed nobody fetched.

    `(None, None)` for a wiki captured on its own -- which belongs to no
    community, and saying so is the honest answer.
    """
    from connections_export.archive.records import RunMetadata  # noqa: PLC0415

    # Through the source, so this reads a zipped archive as readily as a
    # directory.
    for entry in archive.source.run_metadata():
        try:
            run = RunMetadata.model_validate_json(entry.data)
        except (OSError, ValueError):
            continue
        if not run.community_uuid:
            continue
        for component in run.components:
            if component.kind == "wiki" and component.id == label:
                return run.community_uuid, run.community_title
    return None, None


def derive(
    archive: Archive,
    *,
    base_url: str | None = None,
    hcl_hosts: Iterable[str] | None = None,
    run_id: str | None = None,
    source_version: str | None = None,
) -> Interchange:
    """Read `archive` and assemble the tool-agnostic interchange model. The only required argument
    is the
    archive itself -- auth_root and page_size are always recovered
    from what was actually archived, not re-supplied out of band.

    `base_url`/`hcl_hosts`/`source_version`/`run_id` resolve in this
    precedence, highest wins:

    1. **Explicit kwargs** here -- for tests/unusual cases. Passing
       `hcl_hosts=[]` explicitly still counts as explicit (overrides
       even a non-empty run metadata) -- the default is `None`
       ("not passed"), not ``, specifically so this is distinguishable.
    2. **Run metadata** -- the most recent `run-*.json` in the archive,
       written by the crawler at run start, if any is present. Makes
       the archive self-describing about its own deployment host set: a cross-app asset/link on a
       second host classifies correctly without being told out of band.
    3. **URL recovery** -- today's behaviour, unchanged: `base_url` is
       recovered from the archived wikis-feed URL, `hcl_hosts` is
       empty, `source_version`/`run_id` are `None`. This is what an
       older archive (crawled before this change) or a partial capture
       still falls back to.
    """
    index = ArchiveIndex.open(archive)

    # Each app's starting point is discovered independently -- an archive
    # may hold any combination of wikis, blogs, and forums (blogs/forums
    # were crawled by their own `crawl_blogs`/`crawl_forums`, which never
    # touch the wikis feed). A missing wiki root is no longer fatal; the
    # archive is only undeivable when *no* app yields a starting point.
    wiki_root = _discover_root(index)
    blogs_feed_url = _discover_list_feed(index, _BLOGS_LIST_TAIL)
    forums_feed_url = _discover_list_feed(index, _FORUMS_LIST_TAIL)
    # Community blogs crawled via the roller-ui entries feed directly (e.g.
    # community blogs not in the homepage list) are detected from the entries
    # feeds that are in ok_urls.
    direct_blog_entries: list[tuple[str, str]] = []
    if blogs_feed_url is None:
        direct_blog_entries = _discover_direct_blog_entries(index)
    # Direct-topic crawl path: always scan for directly-archived topic replies,
    # even when a forums list feed is present. A community forum's topics may
    # not appear in the general list; this catches them as a supplement.
    direct_forum_topics: list[str] = _discover_direct_forum_topics(index)

    # A run may capture only a community's files, which has none of the above.
    community_library_uuids = _discover_community_libraries(index)
    # Likewise for a run that captured only a community's Rich Content.
    rich_content_uuids = _discover_rich_content(index)

    if (
        wiki_root is None
        and blogs_feed_url is None
        and not direct_blog_entries
        and forums_feed_url is None
        and not direct_forum_topics
        and not community_library_uuids
        and not rich_content_uuids
    ):
        raise DeriveError(
            "archive contains no derivable starting point -- no "
            "'.../wikis/feed?ps=..&page=1' or '.../wiki/{label}/nav/feed' (wikis), "
            "no '.../feed/blogs/atom' or roller-ui entries feed (blogs), "
            "no '.../forums/atom/forums' or direct topic replies feed (forums), "
            "no '.../files/basic/api/communitylibrary/{uuid}/feed' (files), "
            "and no '.../communities/service/atom/community/widgets?communityUuid=..' "
            "(rich content)"
        )

    # Page size + recovered base_url come from whichever app was crawled;
    # the wiki root reveals both when present, otherwise recover the page
    # size from any paginated feed and the base URL from the list feed.
    if wiki_root is not None:
        api_root, page_size = wiki_root
        recovered_base_url = _base_url_of(api_root) or api_root
    else:
        api_root = None
        page_size = _page_size_from_feeds(index)
        if blogs_feed_url is not None:
            recovered_base_url = _base_url_before(blogs_feed_url, "/blogs/")
        elif direct_blog_entries:
            # Recover base_url from the roller-ui path:
            # `.../blogs/roller-ui/rendering/feed/{uuid}/entries/atom` ->
            # everything before `/blogs/`.
            recovered_base_url = _base_url_before(direct_blog_entries[0][0], "/blogs/")
        elif direct_forum_topics:
            # Recover base_url from a replies feed URL:
            # `.../forums/atom/replies?topicUuid=...` -> everything before `/forums/`.
            for _u in index.ok_urls():
                if "/forums/atom/replies" in _u:
                    recovered_base_url = _base_url_before(_u, "/forums/")
                    break
            else:
                recovered_base_url = ""
        elif forums_feed_url is not None:
            recovered_base_url = _base_url_before(forums_feed_url, _FORUMS_LIST_TAIL)
        else:
            # Files-only: recover from the community library feed, the same way
            # every other app recovers from its own list feed. Without this the
            # chain fell through to the forums branch holding None.
            recovered_base_url = ""
            for _u in index.ok_urls():
                if _COMMUNITY_LIBRARY_RE.search(_u):
                    recovered_base_url = _base_url_before(_u, "/files/")
                    break

    run_metadata = _latest_run_metadata(archive)

    resolved_base_url = (
        base_url
        if base_url is not None
        else (run_metadata.base_url if run_metadata is not None else recovered_base_url)
    )
    resolved_hcl_hosts = (
        list(hcl_hosts)
        if hcl_hosts is not None
        else (list(run_metadata.hcl_hosts) if run_metadata is not None else [])
    )
    resolved_source_version = (
        source_version
        if source_version is not None
        else (run_metadata.source_system_version if run_metadata is not None else None)
    )
    resolved_run_id = (
        run_id
        if run_id is not None
        else (run_metadata.run_id if run_metadata is not None else None)
    )
    #: What the run was asked to keep. An item absent BECAUSE of this is
    #: absent by instruction, and the model has to be able to say so -- "not
    #: captured" alone reads as a failure.
    resolved_author_filter = run_metadata.author_filter if run_metadata is not None else None

    wikis: list[DerivedWiki] = []
    if api_root is not None:
        wikis_url = f"{api_root}{_WIKIS_FEED_TAIL}"
        wikirefs = index.paginate(wikis_url, parse_wikis_feed, page_size=page_size)

        if not wikirefs:
            # Scoped/partial archive: no wikis feed to
            # enumerate from, so recover the wikis from the archived nav
            # feeds.
            #
            # The label must not double as the title. The human title lives
            # in the absent wikis-feed entry, so falling back to the label
            # shows "eng-handbook" everywhere the name belongs: the reader,
            # the table of contents, the PDF body. The wiki's OWN feed carries
            # that title and a scoped crawl archives it, so read it from
            # there, and fall back to the label only when it was never
            # captured (an older archive).
            from connections_export.adapters.atom import feed_title  # noqa: PLC0415

            wikirefs = []
            for label in _nav_feed_roots_and_labels(index):
                title = None
                response = index.get(f"{api_root}/wiki/{label}/feed")
                if response is not None:
                    try:
                        title = feed_title(response.content)
                    except Exception:  # noqa: BLE001 - a name is never fatal
                        title = None
                # The community, from what the run recorded rather than from
                # a feed this capture never fetched. A scoped crawl skips the
                # wikis LIST feed -- the only place `snx:communityUuid`
                # appears -- so without this a community's own wiki arrives
                # attributed to nothing, and the reader files it under "Not in
                # a community" while the verdict's row for that community
                # shows no pages.
                wikirefs.append(
                    WikiRef(
                        uuid=label,
                        label=label,
                        title=title or label,
                        community_uuid=_community_for_label(archive, label)[0],
                        community_title=_community_for_label(archive, label)[1],
                    )
                )

        wikis = [
            _assemble_wiki(
                index,
                wikiref,
                api_root=api_root,
                page_size=page_size,
                base_url=resolved_base_url,
                hcl_hosts=resolved_hcl_hosts,
            )
            for wikiref in wikirefs
        ]

    blogs: list[DerivedBlog] = []
    if blogs_feed_url is not None:
        blogs = _assemble_blogs(
            index,
            blogs_feed_url=blogs_feed_url,
            page_size=page_size,
            base_url=resolved_base_url,
            hcl_hosts=resolved_hcl_hosts,
            max_items=run_metadata.max_items if run_metadata is not None else None,
        )
    elif direct_blog_entries:
        # No list feed: assemble each discovered blog directly from its
        # entries feed URL (community blogs, roller-ui-only archives).
        for entries_url, uuid in direct_blog_entries:
            from connections_export.adapters.model import BlogRef  # noqa: PLC0415

            blogref = BlogRef(uuid=uuid, title=None, self_url=entries_url)
            blog = _assemble_blog(
                index,
                blogref,
                page_size=page_size,
                base_url=resolved_base_url,
                hcl_hosts=resolved_hcl_hosts,
                max_items=run_metadata.max_items if run_metadata is not None else None,
            )
            if blog is not None:
                blogs.append(blog)

    forums: list[DerivedForum] = []
    if forums_feed_url is not None:
        forums = _assemble_forums(
            index,
            forums_feed_url=forums_feed_url,
            page_size=page_size,
            base_url=resolved_base_url,
            hcl_hosts=resolved_hcl_hosts,
        )
    # Supplement with any archived per-forum topics feeds whose forum UUID is
    # not already covered -- this catches community forums that appeared in the
    # full-forum crawl but not in the general forums list.
    archived_topic_feeds = _discover_archived_topic_feeds(index)
    covered_forum_ids: set[str] = {f.id for f in forums}
    for forum_uuid, feed_base_url in archived_topic_feeds:
        if forum_uuid in covered_forum_ids:
            continue
        from connections_export.adapters.forums import ForumRef as _FR  # noqa: PLC0415
        from connections_export.adapters.forums import topics_url as _tu  # noqa: PLC0415

        fref = _FR(
            uuid=forum_uuid,
            title=None,
            self_url=_tu(base_url=feed_base_url or resolved_base_url, forum_uuid=forum_uuid),
        )
        assembled = _assemble_forum(
            index,
            fref,
            forums_base_url=feed_base_url or resolved_base_url,
            page_size=page_size,
            base_url=resolved_base_url,
            hcl_hosts=resolved_hcl_hosts,
        )
        if assembled is not None:
            forums.append(assembled)
            covered_forum_ids.add(forum_uuid)

    # Supplement with any directly-archived individual topics (reply feeds)
    # not already covered by any assembled forum above.
    if direct_forum_topics:
        covered_topic_ids: set[str] = {tid for f in forums for tid in (f.topic_ids or [])}
        uncovered = [t for t in direct_forum_topics if t not in covered_topic_ids]
        if uncovered:
            forums += _assemble_direct_topics(
                index,
                uncovered,
                page_size=page_size,
                base_url=resolved_base_url,
                hcl_hosts=resolved_hcl_hosts,
            )

    communities = _assemble_communities(wikis, blogs, forums)

    # Files are community-scoped, so they can only be assembled once the
    # communities are known. A community without an archived library feed
    # yields None -- not every community has Files, and not every run selects
    # them.
    file_libraries = []
    by_community = {c.id: c for c in communities if c.id}
    for community_uuid in community_library_uuids:
        library = _assemble_file_library(
            index,
            community_uuid=community_uuid,
            base_url=resolved_base_url,
            hcl_hosts=resolved_hcl_hosts,
            page_size=page_size,
            author_filter=resolved_author_filter,
        )
        if library is None:
            continue
        community = by_community.get(community_uuid)
        if community is not None:
            library.community_title = community.title
            community.file_library_id = library.id
        file_libraries.append(library)

    # Rich Content, on the same footing and for the same reason: it is
    # community-scoped, so it is assembled once the communities are known.
    rich_content = []
    for community_uuid in rich_content_uuids:
        highlights = _assemble_rich_content(
            index,
            community_uuid=community_uuid,
            base_url=resolved_base_url,
            hcl_hosts=resolved_hcl_hosts,
        )
        if highlights is None:
            continue
        community = by_community.get(community_uuid)
        if community is not None:
            highlights.community_title = community.title
            community.rich_content_id = highlights.id
        rich_content.append(highlights)

    for community in communities:
        if community.id:
            community.logo = _community_logo(
                index,
                community_uuid=community.id,
                base_url=resolved_base_url,
                hcl_hosts=resolved_hcl_hosts,
            )

    interchange = Interchange(
        base_url=resolved_base_url,
        author_filter=resolved_author_filter,
        source_version=resolved_source_version,
        run_id=resolved_run_id,
        hcl_hosts=resolved_hcl_hosts,
        wikis=wikis,
        blogs=blogs,
        forums=forums,
        communities=communities,
        file_libraries=file_libraries,
        rich_content=rich_content,
    )
    # Only now is everything known. A link from the first wiki to the last
    # forum cannot be resolved while either is still being assembled, and
    # blogs and forums resolved theirs against nothing at all -- so the last
    # step is to look again, across the whole export. See derive/crosslink.py.
    from connections_export.derive.crosslink import crosslink  # noqa: PLC0415

    crosslink(interchange)
    return interchange


#: `thread_comments` lives in `derive.apps._shared` now -- it serves wiki, blog
#: and file comments alike -- but it has been importable from here since it was
#: written, and callers should not have to care that the assemblers moved.
__all__ = ["DeriveError", "derive", "thread_comments"]
