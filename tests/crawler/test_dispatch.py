"""Per-app crawl dispatch, in one place.

It was in three: `cli._run_selected_crawls`, and twice inside the console's
`_run_real` (a community branch and a single-app chain). They had different
coverage -- the CLI had no rich-content branch at all, so a component the
console offered and the CLI accepted did nothing -- and the `--into` cutoff was
dropped in one of them. A selection is a selection; what runs for it should not
depend on which front end asked.
"""

from __future__ import annotations

import pytest

from connections_export import apps
from connections_export.crawler import dispatch
from connections_export.crawler.report import CrawlReport


def _ok():
    from connections_export.crawler.engine import CrawlResult

    return CrawlResult(ok=True, report=CrawlReport(), run_id="r-test")


@pytest.fixture
def recorded(monkeypatch):
    """Replace every crawl entry point with a recorder.

    Patched at `dispatch._CRAWLERS`, the definition -- not at
    `connections_export.crawler.crawl_blogs`, which is an alias. The registry
    binds the callables at import, so patching the package attribute rebinds a
    name nothing reads.
    """
    calls: list[tuple[str, dict]] = []

    def recorder(name):
        def fake(**kwargs):
            calls.append((name, kwargs))
            return _ok()

        return fake

    # One recorder per REAL crawler, not per kind. `blog` and `ideation_blog`
    # share `crawl_blogs`, and the grouping under test keys on the callable
    # being the same object -- giving each kind its own fake would quietly
    # break the coalescing this fixture exists to observe.
    by_callable: dict[object, object] = {}
    for kind in apps.COMPONENT_KINDS:
        real = dispatch._CRAWLERS[kind]
        if real not in by_callable:
            by_callable[real] = recorder(kind)
        monkeypatch.setitem(dispatch._CRAWLERS, kind, by_callable[real])
    return calls


def test_a_blog_and_an_ideation_blog_are_one_crawl(recorded):
    """An ideation blog is a blog with a different `kind`. Asking for them
    separately would open two runs against the same feed."""
    dispatch.run_selection(
        {"blog": ["b-1"], "ideation_blog": ["b-2"]},
        config=None,
        client=None,
        archive=None,
    )

    assert len(recorded) == 1
    _, kwargs = recorded[0]
    assert sorted(kwargs["blog_uuids"]) == ["b-1", "b-2"]


def test_files_and_rich_content_run_once_per_community(recorded):
    """Both are addressed by community uuid, one community per call."""
    dispatch.run_selection(
        {"files": ["c-1", "c-2"]},
        config=None,
        client=None,
        archive=None,
    )

    assert [kwargs["community_uuid"] for _, kwargs in recorded] == ["c-1", "c-2"]


def test_the_cutoff_only_reaches_apps_whose_feeds_take_it(recorded):
    """Wikis cannot be asked "what changed since" -- passing `since` would be a
    TypeError in the one branch a unit test never reaches. Not passing it to an
    app that DOES take one is worse: a silent full re-crawl."""
    dispatch.run_selection(
        {"wiki": ["w-1"], "forum": ["f-1"]},
        config=None,
        client=None,
        archive=None,
        since="2026-08-10T21:13:00Z",
    )

    by_kind = {kind: kwargs for kind, kwargs in recorded}
    assert "since" not in by_kind["wiki"]
    assert by_kind["forum"]["since"] == "2026-08-10T21:13:00Z"


def test_every_selected_kind_actually_runs(recorded):
    """Every selectable kind must reach a branch that runs it.

    A kind the dispatcher has no branch for fails silently -- the run
    reports success and simply contains nothing of it -- so the check
    has to be that each one is dispatched, not that the run succeeded.
    """
    dispatch.run_selection(
        {kind: [f"id-{kind}"] for kind in apps.COMPONENT_KINDS},
        config=None,
        client=None,
        archive=None,
    )

    ran = [kind for kind, _ in recorded]
    assert "rich_content" in ran
    # blog and ideation_blog coalesce into one call, so six selected kinds
    # produce five crawls.
    assert len(recorded) == len(apps.COMPONENT_KINDS) - 1


def test_forums_are_crawled_before_the_slower_components(recorded):
    """Forums produce visible results fastest, so a community capture starts
    them first and the tree fills early. This was an ordered chain of
    hand-written branches in the console; it is registry data now, and the
    order has to survive that move."""
    dispatch.run_selection(
        {kind: [f"id-{kind}"] for kind in apps.COMPONENT_KINDS},
        config=None,
        client=None,
        archive=None,
    )

    ran = [kind for kind, _ in recorded]
    assert ran[0] == "forum", f"forums no longer run first: {ran}"
    assert ran.index("forum") < ran.index("wiki") < ran.index("files")


def test_an_empty_selection_runs_nothing(recorded):
    assert dispatch.run_selection({}, config=None, client=None, archive=None) == []
    assert recorded == []


def test_blank_ids_are_not_crawled(recorded):
    """A checkbox with no id behind it must not become a crawl of everything."""
    dispatch.run_selection(
        {"forum": ["", "  ", "f-1"]},
        config=None,
        client=None,
        archive=None,
    )

    assert len(recorded) == 1
    assert recorded[0][1]["forum_uuids"] == ["f-1"]
