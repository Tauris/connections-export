"""Filter a derived `Interchange` down to one user's involvement.

The naive "capture everything I authored" pass: we crawl everything, then keep only the
entities the target user authored or took part in -- each with its **full**
surrounding chain (every comment/reply, versions, attachments, tags), never an
isolated snippet. The raw archive stays complete; only the derived model handed
to export/serve is narrowed.

Authorship in HCL Connections is recorded as **both `atom:author` and
`atom:contributor`**, each carrying a display name and a stable `snx:userid`.
A target string matches an entity if it case-insensitively equals the entity's
author name, its `author_userid`, or any of its `contributors`.

Why client-side: the Search API's person filter is a *superset* -- "associated
with / relevant to" means author OR contributor OR community membership, not
exact authorship. So even once we use search
to narrow candidates (a later optimization), the exact "authored/participated"
decision stays here, on the derived model.
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedForum,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
)
from connections_export.identity import norm_identity as _norm


def _involves(target: str, obj: object) -> bool:
    """True if `target` (already normalized) matches `obj`'s author name,
    `author_userid`, or any of its `contributors` (case-insensitively)."""
    identities = {
        _norm(obj.author),  # type: ignore[attr-defined]
        _norm(getattr(obj, "author_userid", None)),
    }
    identities.update(_norm(c) for c in getattr(obj, "contributors", ()) or ())
    return target in (identities - {None})


def _page_involved(page: DerivedPage, target: str) -> bool:
    return _involves(target, page) or any(_involves(target, c) for c in page.comments)


def _post_involved(post: DerivedBlogPost, target: str) -> bool:
    return _involves(target, post) or any(_involves(target, c) for c in post.comments)


def _topic_involved(topic: DerivedForumTopic, target: str) -> bool:
    return _involves(target, topic) or any(
        _involves(target, reply) for reply in topic.replies.values()
    )


def _filter_wiki(wiki: DerivedWiki, target: str) -> DerivedWiki | None:
    """Keep pages the user authored/commented on, plus their ancestors (as
    context) so the tree path survives. `None` if nothing is kept."""
    authored = {pid for pid, page in wiki.pages.items() if _page_involved(page, target)}
    if not authored:
        return None

    keep = set(authored)
    for pid in authored:
        parent = wiki.pages[pid].parent_id
        while parent and parent in wiki.pages and parent not in keep:
            keep.add(parent)
            parent = wiki.pages[parent].parent_id

    new_pages = {
        pid: wiki.pages[pid].model_copy(
            update={
                "child_ids": [c for c in wiki.pages[pid].child_ids if c in keep],
                "is_context": pid not in authored,
            }
        )
        for pid in keep
    }
    new_roots = [r for r in wiki.root_page_ids if r in keep]
    return wiki.model_copy(update={"pages": new_pages, "root_page_ids": new_roots})


def _filter_blog(blog: DerivedBlog, target: str) -> DerivedBlog | None:
    kept = {pid for pid, post in blog.posts.items() if _post_involved(post, target)}
    if not kept:
        return None
    return blog.model_copy(
        update={
            "posts": {pid: blog.posts[pid] for pid in kept},
            "post_ids": [pid for pid in blog.post_ids if pid in kept],
        }
    )


def _filter_forum(forum: DerivedForum, target: str) -> DerivedForum | None:
    kept = {tid for tid, topic in forum.topics.items() if _topic_involved(topic, target)}
    if not kept:
        return None
    return forum.model_copy(
        update={
            "topics": {tid: forum.topics[tid] for tid in kept},
            "topic_ids": [tid for tid in forum.topic_ids if tid in kept],
        }
    )


def filter_by_author(interchange: Interchange, *, author: str) -> Interchange:
    """A new `Interchange` keeping only content the user `author` authored or
    participated in (matched by name, `snx:userid`, or contributor), each with
    its full chain. Wiki ancestors are kept as `is_context` pages so the tree
    path survives. Containers with nothing kept are dropped. An empty `author`
    is a no-op (returns the interchange unchanged)."""
    target = _norm(author)
    if target is None:
        return interchange
    return interchange.model_copy(
        update={
            "wikis": [w for w in (_filter_wiki(w, target) for w in interchange.wikis) if w],
            "blogs": [b for b in (_filter_blog(b, target) for b in interchange.blogs) if b],
            "forums": [f for f in (_filter_forum(f, target) for f in interchange.forums) if f],
        }
    )
