"""Slice a derived `Interchange` down to a single addressable unit, so an
export (PDF, reader) can render *just this thread / this page / this blog*
instead of the whole archive.

This is the print-time counterpart to `author_filter`: same shape (a pure
`Interchange -> Interchange` narrowing, raw archive untouched), but keyed by an
explicit `(kind, id)` the user picks in the reader -- the "single thread or the
whole to be printed" choice the export UI was missing.

Granularities, coarse to fine:

- `wiki` / `blog` / `forum` -- one whole container (every page/post/topic).
- `page` -- one wiki page **plus its ancestors** as `is_context`, so the tree
  path to it survives (mirrors `author_filter`); the page's own children are
  dropped -- "this page", not "this subtree".
- `post` -- one blog post.
- `topic` -- one forum topic (the whole thread: every reply at every depth,
  since replies live inside the topic already).

An unknown `kind`, or an `id` that matches nothing, yields an empty interchange
(no wikis/blogs/forums) rather than raising -- the caller renders an honest
"nothing in scope" document instead of a 500.
"""

from __future__ import annotations

from collections.abc import Iterable

from connections_export.derive.model import DerivedWiki, Interchange

_CONTAINER_KINDS = {
    "wiki": "wikis",
    "blog": "blogs",
    "forum": "forums",
    # A community file library is a container like the others: it can be
    # exported alone, and -- more importantly -- it must be DROPPED when
    # something else is exported alone.
    "files": "file_libraries",
    "rich_content": "rich_content",
}


def _empty(interchange: Interchange) -> Interchange:
    # Every container field, or narrowing leaks. `file_libraries` was missing
    # here, so "export just this forum" still carried the whole community
    # library -- every document in it -- into the export.
    return interchange.model_copy(
        update={
            "wikis": [],
            "blogs": [],
            "forums": [],
            "file_libraries": [],
            "rich_content": [],
        }
    )


def _only_container(interchange: Interchange, field: str, target_id: str) -> Interchange:
    kept = [c for c in getattr(interchange, field) if c.id == target_id]
    return _empty(interchange).model_copy(update={field: kept})


def _slice_page(wiki: DerivedWiki, page_id: str) -> DerivedWiki | None:
    """The single page plus its ancestor chain (context), children pruned."""
    if page_id not in wiki.pages:
        return None
    keep = {page_id}
    parent = wiki.pages[page_id].parent_id
    while parent and parent in wiki.pages and parent not in keep:
        keep.add(parent)
        parent = wiki.pages[parent].parent_id
    new_pages = {
        pid: wiki.pages[pid].model_copy(
            update={
                "child_ids": [c for c in wiki.pages[pid].child_ids if c in keep],
                "is_context": pid != page_id,
            }
        )
        for pid in keep
    }
    new_roots = [r for r in wiki.root_page_ids if r in keep]
    return wiki.model_copy(update={"pages": new_pages, "root_page_ids": new_roots})


def _scope_page(interchange: Interchange, page_id: str) -> Interchange:
    for wiki in interchange.wikis:
        sliced = _slice_page(wiki, page_id)
        if sliced is not None:
            return _empty(interchange).model_copy(update={"wikis": [sliced]})
    return _empty(interchange)


def _scope_post(interchange: Interchange, post_id: str) -> Interchange:
    for blog in interchange.blogs:
        if post_id in blog.posts:
            only = blog.model_copy(
                update={"posts": {post_id: blog.posts[post_id]}, "post_ids": [post_id]}
            )
            return _empty(interchange).model_copy(update={"blogs": [only]})
    return _empty(interchange)


def _scope_topic(interchange: Interchange, topic_id: str) -> Interchange:
    for forum in interchange.forums:
        if topic_id in forum.topics:
            only = forum.model_copy(
                update={"topics": {topic_id: forum.topics[topic_id]}, "topic_ids": [topic_id]}
            )
            return _empty(interchange).model_copy(update={"forums": [only]})
    return _empty(interchange)


def _scope_rich_content_page(interchange: Interchange, page_id: str) -> Interchange:
    """One Highlights page, on its own.

    `placed`/`initialized` are reset to this single page rather than carried
    over. Those two describe the COMMUNITY -- how many rich content areas exist
    and how many were written in -- and a one-page tile that still announced
    "3 placed, 2 written in" would be describing something other than the thing
    on screen.
    """
    for highlights in interchange.rich_content:
        if page_id in highlights.pages:
            only = highlights.model_copy(
                update={
                    "pages": {page_id: highlights.pages[page_id]},
                    "page_ids": [page_id],
                    "placed": 1,
                    "initialized": 1,
                }
            )
            return _empty(interchange).model_copy(update={"rich_content": [only]})
    return _empty(interchange)


_ITEM_SCOPERS = {
    "page": _scope_page,
    "post": _scope_post,
    "topic": _scope_topic,
    "rich_content_page": _scope_rich_content_page,
}


def _single_page(interchange: Interchange, page_id: str) -> Interchange:
    """Just this one page -- no ancestors, no children. Unlike `_slice_page`
    (which keeps the ancestor path for a standalone export), this is for the
    live preview's per-entity tiles, where each page is rendered on its own and
    repeating ancestors across tiles would duplicate them."""
    for wiki in interchange.wikis:
        if page_id in wiki.pages:
            only = wiki.pages[page_id].model_copy(
                update={"child_ids": [], "parent_id": None, "is_context": False}
            )
            new_wiki = wiki.model_copy(
                update={"pages": {page_id: only}, "root_page_ids": [page_id]}
            )
            return _empty(interchange).model_copy(update={"wikis": [new_wiki]})
    return _empty(interchange)


def single_node_interchange(interchange: Interchange, *, kind: str, target_id: str) -> Interchange:
    """One leaf entity, bare -- a wiki page (no ancestors), a blog post, a
    forum topic (its whole thread), or a Highlights page. For the append-only
    live preview, where each fetched entity becomes exactly one tile. Unknown
    kind / unmatched id -> empty interchange."""
    if kind == "page":
        return _single_page(interchange, target_id)
    if kind == "post":
        return _scope_post(interchange, target_id)
    if kind == "topic":
        return _scope_topic(interchange, target_id)
    if kind == "rich_content_page":
        return _scope_rich_content_page(interchange, target_id)
    return _empty(interchange)


def _subtree_ids(wiki: DerivedWiki, page_id: str) -> tuple[set[str], set[str]]:
    """`(asked, context)` for a subtree: the page and everything beneath it,
    plus the ancestor chain kept only to place it in the hierarchy."""
    if page_id not in wiki.pages:
        return set(), set()
    asked = {page_id}
    stack = [page_id]
    while stack:
        current = stack.pop()
        for child in wiki.pages[current].child_ids:
            if child in wiki.pages and child not in asked:
                asked.add(child)
                stack.append(child)
    context: set[str] = set()
    parent = wiki.pages[page_id].parent_id
    while parent and parent in wiki.pages and parent not in asked and parent not in context:
        context.add(parent)
        parent = wiki.pages[parent].parent_id
    return asked, context


def select_interchange(
    interchange: Interchange, selections: Iterable[tuple[str, str]]
) -> Interchange:
    """A new `Interchange` holding the union of `selections`.

    `scope_interchange` narrows to exactly ONE unit, so export could offer
    the whole archive or the single item open in the reader and nothing
    between -- exporting two of five forums meant exporting all five.

    Each selection is `(kind, id)` where kind is wiki/blog/forum/files/rich_content
    (a whole container), page/post/topic (one item), or `subtree` (a wiki page
    WITH its descendants, the shape a wiki hierarchy actually has).
    Selections of the same wiki merge rather than the last one winning.

    An empty or wholly unmatched selection yields an empty interchange, never
    the full one: failing open would export everything when the user asked
    for one thing that no longer exists.
    """
    wiki_asked: dict[str, set[str]] = {}
    wiki_context: dict[str, set[str]] = {}
    wiki_roots: dict[str, DerivedWiki] = {}
    blog_ids: list[str] = []
    forum_ids: list[str] = []
    library_ids: list[str] = []
    rich_content_ids: list[str] = []
    extras: list[Interchange] = []

    for kind, target_id in selections:
        if kind == "wiki":
            for wiki in interchange.wikis:
                if wiki.id == target_id or wiki.label == target_id:
                    wiki_roots[wiki.id] = wiki
                    wiki_asked.setdefault(wiki.id, set()).update(wiki.pages)
        elif kind == "subtree":
            for wiki in interchange.wikis:
                asked, context = _subtree_ids(wiki, target_id)
                if asked:
                    wiki_roots[wiki.id] = wiki
                    wiki_asked.setdefault(wiki.id, set()).update(asked)
                    wiki_context.setdefault(wiki.id, set()).update(context)
                    break
        elif kind == "blog":
            blog_ids.append(target_id)
        elif kind == "forum":
            forum_ids.append(target_id)
        elif kind == "files":
            library_ids.append(target_id)
        elif kind == "rich_content":
            rich_content_ids.append(target_id)
        else:
            # page/post/topic fall through to the single-unit scoper, whose
            # context rules (a page keeps its ancestors) already apply.
            extras.append(scope_interchange(interchange, kind=kind, target_id=target_id))

    wikis: list[DerivedWiki] = []
    for wiki_id, asked in wiki_asked.items():
        wiki = wiki_roots[wiki_id]
        # A page pulled in only to place another one is context; one that was
        # asked for stays content even if another selection also needs it as
        # an ancestor.
        context = wiki_context.get(wiki_id, set()) - asked
        keep = asked | context
        pages = {
            pid: wiki.pages[pid].model_copy(
                update={
                    "child_ids": [c for c in wiki.pages[pid].child_ids if c in keep],
                    "is_context": pid in context,
                }
            )
            for pid in keep
            if pid in wiki.pages
        }
        wikis.append(
            wiki.model_copy(
                update={
                    "pages": pages,
                    "root_page_ids": [r for r in wiki.root_page_ids if r in keep],
                }
            )
        )
    for extra in extras:
        wikis.extend(extra.wikis)

    blogs = [b for b in interchange.blogs if b.id in blog_ids]
    blogs += [b for extra in extras for b in extra.blogs]
    forums = [f for f in interchange.forums if f.id in forum_ids]
    forums += [f for extra in extras for f in extra.forums]
    libraries = [lib for lib in interchange.file_libraries if lib.id in library_ids]
    libraries += [lib for extra in extras for lib in extra.file_libraries]
    highlights = [rc for rc in interchange.rich_content if rc.id in rich_content_ids]
    highlights += [rc for extra in extras for rc in extra.rich_content]

    return interchange.model_copy(
        update={
            "wikis": wikis,
            "blogs": blogs,
            "forums": forums,
            "file_libraries": libraries,
            "rich_content": highlights,
        }
    )


def scope_interchange(interchange: Interchange, *, kind: str, target_id: str) -> Interchange:
    """A new `Interchange` narrowed to the single unit named by `(kind,
    target_id)`. `kind` is one of wiki/blog/forum/files/rich_content (a whole
    container) or
    page/post/topic (one item, with a wiki page keeping its ancestors as
    context). An unknown kind or unmatched id returns an empty interchange."""
    if kind in _CONTAINER_KINDS:
        return _only_container(interchange, _CONTAINER_KINDS[kind], target_id)
    scoper = _ITEM_SCOPERS.get(kind)
    if scoper is None:
        return _empty(interchange)
    return scoper(interchange, target_id)
