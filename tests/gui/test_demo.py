"""`run_demo` runs the *genuine* crawler/derive machinery
against the in-process fakeserver -- only the server is synthetic
(Demo mode = the real pipeline). No JS/simulated events:
every event handed to `emit` is one of the crawler's own real event
types, and a real derived model results.
"""

from __future__ import annotations

from connections_export.crawler import events
from connections_export.derive import Interchange
from connections_export.gui.demo import run_demo


def _collect(**kwargs):
    collected: list = []
    result = run_demo(collected.append, delay=0, **kwargs)
    return collected, result


def test_run_demo_runs_the_real_pipeline_and_yields_a_real_event_stream(tmp_path):
    collected, result = _collect(seed=1, archive_dir=tmp_path / "archive")

    # Every event is one of the crawler's own real event types -- not
    # a dict, not a fabricated stand-in.
    assert collected, "run_demo emitted no events at all"
    for event in collected:
        assert isinstance(event, events.Event)

    assert isinstance(collected[0], events.RunStarted)
    assert isinstance(collected[-1], events.RunComplete)
    assert any(isinstance(e, events.PageDerived) for e in collected)
    assert any(isinstance(e, events.Discovered) for e in collected)
    assert any(isinstance(e, events.Fetched) for e in collected)


def test_run_demo_produces_a_real_archive_and_derived_model(tmp_path):
    archive_dir = tmp_path / "archive"
    collected, result = _collect(seed=2, archive_dir=archive_dir)

    # A real archive landed on disk -- not simulated.
    assert (archive_dir / "manifest.jsonl").exists()
    assert result.archive_dir == archive_dir

    # A real derived model resulted, with non-trivial counts.
    assert isinstance(result.interchange, Interchange)
    assert len(result.interchange.wikis) >= 2
    total_pages = sum(len(wiki.pages) for wiki in result.interchange.wikis)
    assert total_pages >= 6  # 2 wikis x (2 + 2*2) pages, per the demo seed shape

    report = result.crawl_result.report
    assert report.pages_crawled == total_pages
    assert result.crawl_result.ok


def test_run_demo_derives_blogs_and_forums_alongside_the_wikis(tmp_path):
    """Stage-2 phase 6: `serve --demo` shows all
    three apps, so `run_demo` now also synthesizes + crawls Blogs and
    Forums into the same archive and `derive` returns them alongside the
    (unchanged) curated wikis."""
    collected, result = _collect(seed=0, archive_dir=tmp_path / "archive")
    ic = result.interchange

    # Wikis unchanged: the curated prototype dataset (3 wikis, 20 pages).
    assert len(ic.wikis) == 3
    assert sum(len(w.pages) for w in ic.wikis) == 20

    # Blogs: a couple of blogs, each with a few posts carrying comments.
    assert len(ic.blogs) >= 2
    posts = [p for blog in ic.blogs for p in blog.posts.values()]
    assert len(posts) >= 4
    assert any(p.comments for p in posts), "no blog post carried comments"

    # Forums: a couple of forums, each with topics, and at least one topic
    # whose reply tree is genuinely nested (a reply with children).
    assert len(ic.forums) >= 2
    topics = [t for forum in ic.forums for t in forum.topics.values()]
    assert len(topics) >= 4
    assert any(t.replies for t in topics), "no forum topic carried replies"
    assert any(any(reply.child_ids for reply in t.replies.values()) for t in topics), (
        "no forum topic had a nested reply thread"
    )

    # The real crawler emitted the Stage-2 derived events too (they stream
    # through the same `emit`, so a live viewer sees them).
    assert any(isinstance(e, events.BlogPostDerived) for e in collected)
    assert any(isinstance(e, events.ForumTopicDerived) for e in collected)

    # The threaded-back crawl_result stays the WIKI crawl's result (its
    # pages_crawled is the wiki page count).
    assert result.crawl_result.report.pages_crawled == 20


def test_run_demo_presents_three_crawls_as_one_run_lifecycle(tmp_path):
    """Though the demo runs three crawls (wikis, blogs, forums), it emits a
    SINGLE run lifecycle so the live console isn't closed on the first
    crawl's `run_complete` before blog/forum nodes appear: exactly one
    `RunStarted` (first) and one `RunComplete` (last), with the blog/forum
    derived events arriving *before* that closing event."""
    collected, _ = _collect(seed=0, archive_dir=tmp_path / "archive")

    starts = [e for e in collected if isinstance(e, events.RunStarted)]
    completes = [e for e in collected if isinstance(e, events.RunComplete)]
    assert len(starts) == 1
    assert len(completes) == 1
    assert isinstance(collected[0], events.RunStarted)
    assert isinstance(collected[-1], events.RunComplete)

    # blog/forum nodes stream before the single closing event
    last_derived = max(
        i
        for i, e in enumerate(collected)
        if isinstance(e, events.BlogPostDerived | events.ForumTopicDerived)
    )
    assert last_derived < len(collected) - 1  # before the closing RunComplete


def test_run_demo_exercises_the_cross_app_asset_path(tmp_path):
    """the design: `hcl_hosts=["files.example.corp"]` "so the cross-app
    path is exercised for real". The synthesized body HTML embeds a
    `/files/basic/api/library/...` image reference (fakeserver/synth.py
    `_body_html`); the crawler must actually fetch it as an asset (a
    `same`-classified cross-app URL, since it resolves to the base
    host) rather than silently skip it."""
    collected, _ = _collect(seed=3, archive_dir=tmp_path / "archive")

    fetched_asset_urls = [
        e.url for e in collected if isinstance(e, events.Fetched) and e.kind == "asset"
    ]
    assert any("/files/basic/api/library/" in url for url in fetched_asset_urls)

    # No orphan/unexpected failures for the cross-app fetch itself.
    failed_asset_urls = [
        e.url
        for e in collected
        if isinstance(e, events.Failed) and "/files/basic/api/library/" in e.url
    ]
    assert failed_asset_urls == []


def test_run_demo_without_archive_dir_creates_its_own_temp_archive():
    collected, result = _collect(seed=4)

    assert result.archive_dir.exists()
    assert (result.archive_dir / "manifest.jsonl").exists()


def test_run_demo_is_deterministic_for_a_given_seed(tmp_path):
    _, first = _collect(seed=5, archive_dir=tmp_path / "a")
    _, second = _collect(seed=5, archive_dir=tmp_path / "b")

    assert first.crawl_result.report.pages_crawled == second.crawl_result.report.pages_crawled
    assert [w.title for w in first.interchange.wikis] == [w.title for w in second.interchange.wikis]


def test_run_demo_delay_paces_emission(tmp_path):
    slept: list[float] = []
    run_demo(
        lambda _event: None,
        seed=6,
        archive_dir=tmp_path / "archive",
        delay=0.01,
        sleep=slept.append,
    )

    assert slept, "a nonzero delay should invoke the injected sleep"
    assert all(s == 0.01 for s in slept)
