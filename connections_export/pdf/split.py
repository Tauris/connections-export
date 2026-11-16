"""Several PDFs instead of one: where to cut, proposed before anything renders.

One PDF of thousands of pages is hard to handle -- slow to open, too big to
mail or upload. So the export can also produce several PDFs, each a complete
document of its own, cut along boundaries a reader recognises: a community
(with its subcommunities), an app within it (wiki, blogs, forums, files,
Highlights), a container (one wiki, one blog, one forum), and only inside a
very large container, its parts (a wiki's top-level page subtrees, a blog's or
a forum's entries in date order).

`propose()` estimates every node's size from the model alone -- text length,
images, item count; nothing is rendered -- and keeps together whatever fits
the limits the person set, cutting only as deep as needed. Estimates are
approximate and labelled so; the proposal is what the person accepts or
adjusts before the export runs.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from connections_export.derive.model import Interchange

#: Characters of running text on one printed page, roughly (A4, 10.5 pt).
CHARS_PER_PAGE = 3500
#: A typical photo takes this share of a page.
PAGES_PER_IMAGE = 0.4
#: What an image is assumed to weigh in the PDF when its size is unknown.
DEFAULT_IMAGE_BYTES = 150_000
#: PDF bytes per character of text (fonts are subset once per file).
BYTES_PER_CHAR = 0.6

GROUPINGS = ("auto", "community", "app", "container")

_APP_TITLES = {
    "wiki": "Wiki",
    "blog": "Blogs",
    "forum": "Forums",
    "rich_content": "Highlights",
    "files": "Files",
}
_TAG = re.compile(r"<[^>]+>")


@dataclass
class Node:
    """A piece of the export that could be a file of its own, or part of one."""

    level: str  # root | community | app | container | chunk
    title: str
    #: `select_interchange` selections that make up this node.
    selections: list[tuple[str, str]] = field(default_factory=list)
    children: list[Node] = field(default_factory=list)
    pages: float = 0.0
    size: float = 0.0
    #: The item ids this node holds (for links between files).
    item_ids: set[str] = field(default_factory=set)
    parent: Node | None = field(default=None, repr=False, compare=False)

    def total(self) -> Node:
        """Sum the children into this node, recursively."""
        if self.children:
            for child in self.children:
                child.total()
            self.pages = sum(c.pages for c in self.children)
            self.size = sum(c.size for c in self.children)
            self.selections = [s for c in self.children for s in c.selections]
            self.item_ids = set().union(*(c.item_ids for c in self.children))
        return self


@dataclass
class FilePlan:
    """One proposed PDF."""

    name: str
    title: str
    nodes: list[Node]

    @property
    def pages(self) -> int:
        return max(1, round(sum(n.pages for n in self.nodes)))

    @property
    def size(self) -> int:
        return round(sum(n.size for n in self.nodes))

    @property
    def selections(self) -> list[tuple[str, str]]:
        return [s for n in self.nodes for s in _selections(n)]

    @property
    def item_ids(self) -> set[str]:
        return set().union(*(n.item_ids for n in self.nodes)) if self.nodes else set()

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "pages": self.pages,
            "size": self.size,
            "contents": [n.title for n in self.nodes],
            "selections": [list(s) for s in self.selections],
        }


@dataclass(frozen=True)
class Limits:
    """What the person asked for. None means no limit."""

    max_pages: int | None = None
    max_mb: float | None = None
    grouping: str = "auto"

    def fits(self, pages: float, size: float) -> bool:
        if self.max_pages is not None and pages > self.max_pages:
            return False
        return not (self.max_mb is not None and size > self.max_mb * 2**20)


# --- estimating -----------------------------------------------------------


def _text_chars(html: str | None) -> int:
    return len(_TAG.sub("", html or ""))


def _estimate(entity, blob_size: Callable[[str], int | None] | None) -> tuple[float, float]:
    """Pages and PDF bytes for one item, from its text and images."""
    chars = _text_chars(getattr(entity, "content_html", None))
    for comment in getattr(entity, "comments", None) or []:
        chars += _text_chars(getattr(comment, "content_html", None))
    for reply in (getattr(entity, "replies", None) or {}).values():
        chars += _text_chars(getattr(reply, "content_html", None))
    images = 0
    image_bytes = 0.0
    for asset in getattr(entity, "assets", None) or []:
        if not asset.present:
            continue
        images += 1
        known = blob_size(asset.blob_hash) if (blob_size and asset.blob_hash) else None
        image_bytes += known if known is not None else DEFAULT_IMAGE_BYTES
    # Every item starts on a new sheet.
    pages = max(1, math.ceil(chars / CHARS_PER_PAGE + images * PAGES_PER_IMAGE))
    return pages, chars * BYTES_PER_CHAR + image_bytes


def _leaf(title: str, selections, entities, blob_size) -> Node:
    pages = size = 0.0
    ids: set[str] = set()
    for entity in entities:
        p, s = _estimate(entity, blob_size)
        pages += p
        size += s
        ids.add(entity.id)
    return Node("chunk", title, list(selections), pages=pages, size=size, item_ids=ids)


def _wiki_node(wiki, blob_size) -> Node:
    """A wiki, cut into its page subtrees -- recursively, so a wiki whose
    pages all hang under one root page can still be cut further down."""

    def page_node(page_id: str) -> Node:
        page = wiki.pages[page_id]
        title = page.title or page.label or page_id
        own = _leaf(title, [("page", page_id)], [page], blob_size)
        children = [c for c in page.child_ids if c in wiki.pages]
        if not children:
            own.whole = [("subtree", page_id)]
            return own
        node = Node("chunk", title, children=[own, *(page_node(c) for c in children)])
        node.total()
        node.whole = [("subtree", page_id)]
        return node

    chunks = [page_node(root) for root in wiki.root_page_ids if root in wiki.pages]
    node = Node("container", wiki.title or wiki.label, children=chunks)
    node.total()
    # The whole wiki is one selection when it goes in one file.
    node.whole = [("wiki", wiki.id)]
    return node


def _entries_node(kind: str, container, ids: list[str], items: dict, title: str, blob_size) -> Node:
    """A blog or forum, its entries as chunks (in feed order)."""
    item_kind = {"blog": "post", "forum": "topic"}[kind]
    chunks = [
        _leaf(items[i].title or i, [(item_kind, i)], [items[i]], blob_size)
        for i in ids
        if i in items
    ]
    node = Node("container", title, children=chunks)
    node.total()
    node.whole = [(kind, container.id)]
    return node


def _single_node(kind: str, container, title: str, entities, blob_size) -> Node:
    node = _leaf(title, [(kind, container.id)], entities, blob_size)
    node.level = "container"
    node.whole = [(kind, container.id)]
    return node


def build_tree(
    interchange: Interchange, *, blob_size: Callable[[str], int | None] | None = None
) -> Node:
    """The export as root -> communities (subcommunities inside their parent)
    -> apps -> containers -> chunks, every node with its estimated size."""
    containers: list[tuple[str, str | None, Node]] = []
    for wiki in interchange.wikis:
        containers.append(("wiki", wiki.community_uuid, _wiki_node(wiki, blob_size)))
    for blog in interchange.blogs:
        title = blog.title or blog.handle or blog.id
        node = _entries_node("blog", blog, blog.post_ids, blog.posts, title, blob_size)
        containers.append(("blog", blog.community_uuid, node))
    for forum in interchange.forums:
        title = forum.title or forum.id
        node = _entries_node("forum", forum, forum.topic_ids, forum.topics, title, blob_size)
        containers.append(("forum", forum.community_uuid, node))
    for section in interchange.rich_content:
        pages = [section.pages[i] for i in section.page_ids if i in section.pages]
        node = _single_node(
            "rich_content", section, section.title or "Highlights", pages, blob_size
        )
        containers.append(("rich_content", section.community_uuid, node))
    for library in interchange.file_libraries:
        node = _single_node("files", library, library.title or "Files", [], blob_size)
        node.item_ids = set(library.files or {})
        # A listing: about forty files a page.
        node.pages = max(1.0, len(library.files or {}) / 40)
        node.size = node.pages * 4000
        containers.append(("files", library.community_uuid, node))

    titles = {c.id: c.title or c.id for c in interchange.communities}
    parents = {c.id: c.parent_id for c in interchange.communities if c.parent_id}

    def apps_of(community: str | None) -> list[Node]:
        apps = []
        for kind, label in _APP_TITLES.items():
            members = [n for k, cid, n in containers if k == kind and cid == community]
            if members:
                app = Node("app", label, children=members)
                apps.append(app)
        return apps

    communities = [cid for cid in titles if cid in {c for _k, c, _n in containers}]
    # Communities that only appear on containers.
    for _kind, cid, _node in containers:
        if cid is not None and cid not in titles:
            titles[cid] = cid
            communities.append(cid)

    nodes: dict[str, Node] = {}
    for cid in communities:
        nodes[cid] = Node("community", titles[cid], children=apps_of(cid))
    # Subcommunities go inside their parent, after its own apps.
    top: list[Node] = []
    for cid in communities:
        parent = parents.get(cid)
        if parent in nodes and parent != cid:
            nodes[parent].children.append(nodes[cid])
        else:
            top.append(nodes[cid])
    loose = apps_of(None)
    if loose:
        top.append(Node("community", "Outside any community", children=loose))
    root = Node("root", "Whole export", children=top)
    root.total()
    _link_parents(root)
    return root


def _link_parents(node: Node) -> None:
    for child in node.children:
        child.parent = node
        _link_parents(child)


# --- proposing ------------------------------------------------------------

_LEVELS = ("root", "community", "app", "container", "chunk")


def _cut(node: Node, limits: Limits, force_to: str) -> list[list[Node]]:
    """Groups of nodes, each group one file."""
    if force_to == "community" and node.level == "community":
        # One file per community means subcommunities too: the community's
        # own apps in one file, each subcommunity in files of its own.
        own = [c for c in node.children if c.level != "community"]
        subs = [c for c in node.children if c.level == "community"]
        groups = [own] if own else []
        for sub in subs:
            groups.extend(_cut(sub, limits, force_to))
        if all(limits.fits(sum(n.pages for n in g), sum(n.size for n in g)) for g in groups):
            return groups
        # Too big even so: cut further inside, still never merging across.
        out: list[list[Node]] = []
        for group in groups:
            if limits.fits(sum(n.pages for n in group), sum(n.size for n in group)):
                out.append(group)
            else:
                for child in group:
                    out.extend(_cut(child, limits, "app"))
        return out
    must_split = _LEVELS.index(node.level) < _LEVELS.index(force_to)
    if not must_split and limits.fits(node.pages, node.size):
        return [[node]]
    if not node.children:
        return [[node]]  # nothing smaller exists: an oversized single item
    groups: list[list[Node]] = []
    for child in node.children:
        groups.extend(_cut(child, limits, force_to))
    if must_split and force_to != "auto":
        # The person asked for one file per <level>: never merge across it.
        return groups
    # Neighbours that fit together share a file -- but only whole children of
    # this node: the pieces of a child that had to be cut were already
    # combined at its level, and joining one of them to the next child would
    # make a file that is neither ("Vendor Contacts and Product Updates").
    merged: list[list[Node]] = []
    for group in groups:
        whole = all(n.parent is node for n in group)
        if merged and whole and all(n.parent is node for n in merged[-1]):
            candidate = merged[-1] + group
            if limits.fits(sum(n.pages for n in candidate), sum(n.size for n in candidate)):
                merged[-1] = candidate
                continue
        merged.append(group)
    return merged


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def safe_filename(title: str, limit: int = 80) -> str:
    """A file name every operating system accepts."""
    name = _UNSAFE.sub(" ", title).strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:limit].rstrip(" .")
    return name or "export"


def _name(node: Node) -> str:
    """What a file holding `node` is called: the most specific name that
    still describes all of it -- an app with one container is that
    container, an export of one community is that community."""
    if node.level in ("root", "app") and len(node.children) == 1:
        return _name(node.children[0])
    if node.level == "app":
        return _group_title_plain(node.children)
    return node.title


def _group_title(nodes: list[Node]) -> str:
    parent = nodes[0].parent
    if parent is not None and all(n.parent is parent for n in nodes):
        own = [c for c in parent.children if c.level != "community"]
        # Everything a community holds itself (its subcommunities went to
        # files of their own) is still that community.
        if parent.level == "community" and nodes == own:
            return parent.title
        # Every piece of one container, or one container's run of pieces:
        # say whose pieces they are.
        if nodes[0].level == "chunk" and parent.level in ("container", "chunk"):
            inner = _group_title_plain(nodes)
            container = parent
            while container.level != "container" and container.parent is not None:
                container = container.parent
            if parent is container and len(nodes) == len(parent.children):
                return container.title
            return f"{container.title} – {inner}"
    return _group_title_plain(nodes)


def _group_title_plain(nodes: list[Node]) -> str:
    if len(nodes) == 1:
        return _name(nodes[0])
    if len(nodes) == 2:
        return f"{_name(nodes[0])} and {_name(nodes[1])}"
    return f"{_name(nodes[0])} … {_name(nodes[-1])}"


def propose(
    interchange: Interchange,
    limits: Limits,
    *,
    blob_size: Callable[[str], int | None] | None = None,
) -> list[FilePlan]:
    """The files to make: everything that fits the limits stays together;
    cut by community, then app, then container, then inside a container."""
    if limits.grouping not in GROUPINGS:
        raise ValueError(f"unknown grouping {limits.grouping!r}; one of {', '.join(GROUPINGS)}")
    root = build_tree(interchange, blob_size=blob_size)
    force_to = "root" if limits.grouping == "auto" else limits.grouping
    # With one community, "per community" still means one file.
    groups = _cut(root, limits, force_to if limits.grouping != "auto" else "root")
    groups = [g for g in groups if g]
    width = len(str(len(groups)))
    files = []
    for n, group in enumerate(groups, 1):
        title = _group_title(group)
        name = f"{n:0{width}d} - {safe_filename(title)}.pdf"
        files.append(FilePlan(name=name, title=title, nodes=group))
    return files


def _selections(node: Node) -> list[tuple[str, str]]:
    """What to select for `node`: a container that goes whole is selected
    whole (one heading, its own order), anything above it is its children's,
    a chunk is its own."""
    whole = getattr(node, "whole", None)
    if whole:
        return list(whole)
    if node.children:
        return [s for child in node.children for s in _selections(child)]
    return list(node.selections)


def select_for_file(interchange: Interchange, selections) -> Interchange:
    """The part of `interchange` one file holds.

    Whole containers and wiki subtrees go through `select_interchange`; posts
    and topics are gathered per blog or forum, so a file holding twelve posts
    of one blog shows one blog with twelve posts -- in the blog's own order."""
    from connections_export.derive.scope import select_interchange  # noqa: PLC0415

    posts = {i for kind, i in selections if kind == "post"}
    topics = {i for kind, i in selections if kind == "topic"}
    own = ("post", "topic", "wiki", "subtree", "page")
    rest = [(kind, i) for kind, i in selections if kind not in own]
    picked = (
        select_interchange(interchange, rest)
        if rest
        else interchange.model_copy(
            update={
                "wikis": [],
                "blogs": [],
                "forums": [],
                "file_libraries": [],
                "rich_content": [],
            }
        )
    )
    whole_blogs = {b.id for b in picked.blogs}
    whole_forums = {f.id for f in picked.forums}
    blogs = []
    for blog in interchange.blogs:
        if blog.id in whole_blogs:
            blogs.append(blog)
        elif posts & set(blog.post_ids):
            ids = [i for i in blog.post_ids if i in posts]
            blogs.append(
                blog.model_copy(
                    update={
                        "post_ids": ids,
                        "posts": {i: blog.posts[i] for i in ids if i in blog.posts},
                    }
                )
            )
    forums = []
    for forum in interchange.forums:
        if forum.id in whole_forums:
            forums.append(forum)
        elif topics & set(forum.topic_ids):
            ids = [i for i in forum.topic_ids if i in topics]
            forums.append(
                forum.model_copy(
                    update={
                        "topic_ids": ids,
                        "topics": {i: forum.topics[i] for i in ids if i in forum.topics},
                    }
                )
            )
    wikis = [w for w in (_slice_wiki(w, selections) for w in interchange.wikis) if w is not None]
    return picked.model_copy(update={"wikis": wikis, "blogs": blogs, "forums": forums})


def _slice_wiki(wiki, selections):
    """Exactly the pages asked for, re-rooted where their parent is not in
    this file. Unlike a "choose components" export, no ancestor comes along
    as context: in a set of files it is printed in the file that holds it."""
    asked: set[str] = set()
    for kind, target in selections:
        if kind == "wiki" and target in (wiki.id, wiki.label):
            asked.update(wiki.pages)
        elif kind == "page" and target in wiki.pages:
            asked.add(target)
        elif kind == "subtree" and target in wiki.pages:
            stack = [target]
            while stack:
                current = stack.pop()
                if current in asked:
                    continue
                asked.add(current)
                stack.extend(c for c in wiki.pages[current].child_ids if c in wiki.pages)
    if not asked:
        return None
    order: list[str] = []

    def walk(page_id: str) -> None:
        order.append(page_id)
        for child in wiki.pages[page_id].child_ids:
            if child in wiki.pages:
                walk(child)

    for root in wiki.root_page_ids:
        if root in wiki.pages:
            walk(root)
    pages = {
        pid: wiki.pages[pid].model_copy(
            update={"child_ids": [c for c in wiki.pages[pid].child_ids if c in asked]}
        )
        for pid in order
        if pid in asked
    }
    roots = [pid for pid in order if pid in asked and wiki.pages[pid].parent_id not in asked]
    return wiki.model_copy(update={"pages": pages, "root_page_ids": roots})


def file_of_item(files: Iterable[FilePlan]) -> dict[str, int]:
    """Item id -> the 0-based index of the file holding it."""
    out: dict[str, int] = {}
    for index, plan in enumerate(files):
        for item_id in plan.item_ids:
            out.setdefault(item_id, index)
    return out
