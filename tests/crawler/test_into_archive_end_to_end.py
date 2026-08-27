"""`--into` has to reach the feeds, not just the plan object.

`resolve_update_plan` was well tested and entirely correct, and the cutoff it
returned was dropped one line later. Every existing test asserted against the
returned plan, so the whole feature could be inert without a red test. These
assert on what the crawls were actually handed.
"""

from __future__ import annotations

import connections_export.cli as cli
from connections_export.crawler.report import CrawlReport
from connections_export.crawler.session import UpdatePlan


def _ok():
    from connections_export.crawler.engine import CrawlResult

    return CrawlResult(ok=True, report=CrawlReport(), run_id="r-test")


def test_the_cutoff_reaches_the_date_filterable_feeds(monkeypatch):
    """Blogs and Forums are the two apps whose feeds take `since`.

    Patched at `dispatch._CRAWLERS`, which is the definition, not at
    `connections_export.crawler.crawl_blogs`, which is an alias. The registry
    binds the callables at import, so patching the package attribute rebinds a
    name nothing reads -- the same by-value trap that let a test lower the
    crawler's page cap without reaching derive's.
    """
    from connections_export.crawler import dispatch

    seen: dict[str, object] = {}

    def recorder(name):
        def fake(**kwargs):
            seen[name] = kwargs.get("since", "<absent>")
            return _ok()

        return fake

    for kind in ("wiki", "forum", "blog", "ideation_blog", "files", "rich_content"):
        monkeypatch.setitem(dispatch._CRAWLERS, kind, recorder(kind))

    cli._run_selected_crawls(
        config=None,
        client=None,
        archive=None,
        emit=None,
        selected={"wiki": ["w-1"], "forum": ["f-1"], "blog": ["b-1"]},
        identity={"single": {}},
        max_entries=None,
        author=None,
        since="2026-08-10T21:13:00Z",
    )

    assert seen["blog"] == "2026-08-10T21:13:00Z"
    assert seen["forum"] == "2026-08-10T21:13:00Z"
    # Wikis have no `since` parameter at all -- sending one would be a
    # TypeError in the branch a unit test never reaches.
    assert seen["wiki"] == "<absent>"


def test_crawl_main_hands_the_plans_cutoff_to_the_crawls(tmp_path, monkeypatch):
    """The actual regression: crawl_main computed `plan.since` and dropped it."""
    captured: dict[str, object] = {}

    def fake_run_selected(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "_run_selected_crawls", fake_run_selected)
    monkeypatch.setattr(
        "connections_export.crawler.session.resolve_update_plan",
        lambda config: UpdatePlan(
            fetch="update", output_dir=tmp_path, since="2026-08-10T21:13:00Z"
        ),
    )

    cli.crawl_main(
        [
            "--base-url",
            "https://connections.example.invalid",
            "--component",
            "blog:b-1",
        ],
        env={},
        client=object(),
        archive=object(),
    )

    assert captured["since"] == "2026-08-10T21:13:00Z"
