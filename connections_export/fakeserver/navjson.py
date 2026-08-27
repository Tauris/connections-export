"""The navigation feed: JSON, not Atom.

Real HCL 8.0 shape:

    {
      "identifier": "<tree-root-id>", "label": "<wiki-label>",
      "incremental": false, "fullTree": true, "clean": true,
      "items": [
        {"parElem": "<parent-id-or-root>", "children": [],
         "root": "<tree-root-id>", "id":..., "label":...,
         "title":..., "type": "item", "childSize": N},
...
      ],
      "breadcrumbs": [{"root":..., "id":..., "label":..., "title":...}],
      "timestamp": 1539328731587
    }

Key first-contact corrections over the older reference-doc sample:
  * the parent pointer is **`parElem`**, not `parent`;
  * there is **no** synthetic `{"id": "tree", "root": "true"}` item —
    top-level pages carry `parElem == identifier` (the tree root id);
  * even with `tree=true` the `items[]` are **flat** with `children`
    empty, so sibling order is the items-array order (the nav sequence).

`timestamp` must come from the caller (here: the seed-derived
`WikiSet.base_timestamp_ms`), never a wall-clock read. `tree` and
`parent=<id>` remain the only shaping params; the bare case returns
top-level `"stub"` items a client expands via successive `parent=`.
"""

from connections_export.fakeserver.model import FakePage, FakeWiki


def _preorder(wiki: FakeWiki) -> list[FakePage]:
    """Pages in nav sequence: a depth-first pre-order walk following
    `children_of` (ordinal-sorted), so siblings appear in their authoritative
    order and the parser's items-array ordinal reproduces it."""
    out: list[FakePage] = []
    seen: set[str] = set()

    def rec(parent_uuid: str | None) -> None:
        for page in wiki.children_of(parent_uuid):
            if page.uuid in seen:
                continue
            seen.add(page.uuid)
            out.append(page)
            rec(page.uuid)

    rec(None)
    # Pages unreachable from the root (e.g. an orphan whose parent is a ghost
    # id) still appear in the feed, so they surface as orphans downstream.
    for page in wiki.pages:
        if page.uuid not in seen:
            seen.add(page.uuid)
            out.append(page)
    return out


def _root_id(wiki: FakeWiki) -> str:
    """The tree root id a top-level page's `parElem` points at. The wiki's
    own uuid is a natural, page-disjoint root, so the parser resolves such a
    pointer to a top-level page (parent None), never an orphan."""
    return wiki.uuid


def _item(page: FakePage, wiki: FakeWiki, *, stub: bool) -> dict:
    children = wiki.children_of(page.uuid)
    root_id = _root_id(wiki)
    return {
        # top-level pages point at the tree root id (not another page)
        "parElem": page.parent_uuid or root_id,
        "children": [],  # flat items[] even under tree=true
        "root": root_id,
        "id": page.uuid,
        "label": page.label,
        "title": page.title,
        "type": "stub" if stub else "item",
        "childSize": len(children),
    }


def nav_feed(
    wiki: FakeWiki,
    *,
    tree: bool = False,
    parent: str | None = None,
    timestamp: int,
) -> dict:
    """Build the nav-feed JSON document for `wiki`.

    - `tree=True`: the full tree, every page as a flat `"item"` (children
      empty; hierarchy via `parElem`).
    - `parent=<id>` (tree not set): the immediate children of `<id>`,
      each as an `"item"` -- expands a stub returned by the bare case.
    - neither set: top-level pages only, as `"stub"` items.
    """
    if tree:
        # Emit in nav sequence: a pre-order walk by `children_of` (which sorts
        # siblings by explicit ordinal), so the flat items-array order the
        # parser reads back reproduces the authoritative sibling order.
        items = [_item(p, wiki, stub=False) for p in _preorder(wiki)]
        full_tree = True
        incremental = False
    elif parent is not None:
        items = [_item(p, wiki, stub=False) for p in wiki.children_of(parent)]
        full_tree = False
        incremental = True
    else:
        items = [_item(p, wiki, stub=True) for p in wiki.top_level_pages()]
        full_tree = False
        incremental = True

    return {
        "identifier": _root_id(wiki),
        "label": wiki.label,
        "incremental": incremental,
        "fullTree": full_tree,
        "clean": True,
        "items": items,
        "breadcrumbs": [],
        "timestamp": timestamp,
    }
