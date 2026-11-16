"""The demo's links span the whole export.

A large PDF export is rendered in separately built parts and stitched together;
a link whose ends are far apart (first container to last, wiki to forum, and
back) is what shows the stitching preserved it. These tests read the DERIVED
interchange, so they hold only if each href form actually resolves to
`in_export` through the real pipeline.
"""

from __future__ import annotations

import pytest

from connections_export.gui.demo import run_demo


@pytest.fixture(scope="module")
def interchange(tmp_path_factory):
    return run_demo(
        lambda _e: None, delay=0, archive_dir=tmp_path_factory.mktemp("demo")
    ).interchange


def _containers(ic):
    """`{item id: (app, container ordinal)}` and the ordered item lists."""
    where: dict[str, tuple[str, int]] = {}
    items: list[tuple[object, str, int]] = []
    ordinal = 0
    for app, containers, attr in (
        ("wiki", ic.wikis, "pages"),
        ("blog", ic.blogs, "posts"),
        ("forum", ic.forums, "topics"),
    ):
        for container in containers:
            for item in getattr(container, attr).values():
                where[item.id] = (app, ordinal)
                items.append((item, app, ordinal))
            ordinal += 1
    return where, items, ordinal


def _far_links(where, items):
    """`(from app, from container, to app, to container)` for every in-export
    link between items of the model that leaves its own container."""
    out = []
    for item, app, container in items:
        for link in item.links:
            if link.scope != "in_export" or not link.target_page_id:
                continue
            target = where.get(link.target_page_id)
            if target and target != (app, container):
                out.append((app, container, *target))
    return out


def test_every_app_links_out_of_its_own_container(interchange):
    where, items, _n = _containers(interchange)
    links = _far_links(where, items)
    for app in ("wiki", "blog", "forum"):
        assert any(f == app for f, *_ in links), f"{app} has no link to another container"
    # ...and across apps, not only between containers of one app
    assert any(a != c for a, _b, c, _d in links)


def test_every_container_links_out_and_is_linked_to(interchange):
    where, items, n = _containers(interchange)
    links = _far_links(where, items)
    out = {(a, b) for a, b, _c, _d in links}
    into = {(c, d) for _a, _b, c, d in links}
    everything = set(where.values())
    assert out == everything
    assert into == everything
    assert n >= 3


def test_the_first_container_links_to_the_last_and_back(interchange):
    where, items, n = _containers(interchange)
    links = _far_links(where, items)
    first = ("wiki", 0)
    last = ("forum", n - 1)
    assert any((a, b) == first and (c, d) == last for a, b, c, d in links)
    assert any((a, b) == last and (c, d) == first for a, b, c, d in links)


def test_highlights_link_into_the_community(interchange):
    where, _items, _n = _containers(interchange)
    targets = {
        link.target_page_id
        for section in interchange.rich_content
        for page in section.pages.values()
        for link in page.links
        if link.scope == "in_export"
    }
    assert {where[t][0] for t in targets if t in where} == {"wiki", "blog", "forum"}


def test_every_in_export_target_exists_in_the_model(interchange):
    where, items, _n = _containers(interchange)
    pages = {p.id for s in interchange.rich_content for p in s.pages.values()}
    for item, _app, _container in items:
        for link in item.links:
            if link.scope == "in_export" and link.target_page_id:
                assert link.target_page_id in where or link.target_page_id in pages


def test_no_demo_cross_link_is_left_dangling(interchange):
    """A woven link that stayed `hcl_deployment` would mean its href form (or
    its target) does not match what the pipeline resolves."""
    _where, items, _n = _containers(interchange)
    dangling = [
        link.original_href
        for item, _app, _container in items
        for link in item.links
        if link.scope == "hcl_deployment"
        and any(k in link.original_href for k in ("/forums/html/topic", "/blogs/", "/page/"))
    ]
    assert not dangling


def test_the_demo_child_community_knows_its_parent(interchange):
    """Capture archives each community's own entry; derive reads the parent."""
    by_id = {c.id: c for c in interchange.communities}
    from connections_export.fakeserver.model import (
        DEMO_CHILD_COMMUNITY_UUID,
        DEMO_COMMUNITY_UUID,
    )

    assert by_id[DEMO_CHILD_COMMUNITY_UUID].parent_id == DEMO_COMMUNITY_UUID
    assert by_id[DEMO_CHILD_COMMUNITY_UUID].parent_title == by_id[DEMO_COMMUNITY_UUID].title
    assert by_id[DEMO_COMMUNITY_UUID].parent_id is None
