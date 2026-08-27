"""The demo can show capture -> time passes -> update, and gain content.

This is how anyone without a deployment sees the feature work at all, so it has
to hold end to end rather than in pieces.

The trap it exists to catch: a demo run stamps its provenance with a clock,
and the cutoff for the next update is read back from that stamp. If the run's
clock and the dataset's timeline disagree -- a run stamped "now" against
content dated three years ago -- every update filters out the entire dataset
and reports, perfectly consistently, that nothing has changed.
"""

from __future__ import annotations

from connections_export.crawler.provenance import update_cutoff
from connections_export.gui.demo import run_demo


def _posts(result) -> int:
    return sum(len(blog.posts) for blog in result.interchange.blogs)


def _topics(result) -> int:
    return sum(len(forum.topics) for forum in result.interchange.forums)


def test_an_ordinary_demo_still_shows_everything(tmp_path):
    """Holding content back is something the update story opts into. A plain
    demo must be exactly what it always was."""
    whole = run_demo(lambda _e: None, delay=0, archive_dir=tmp_path)
    held = run_demo(lambda _e: None, delay=0, archive_dir=tmp_path / "held", wave=0)

    assert _posts(whole) > _posts(held)


def test_the_demo_cutoff_sits_inside_the_datasets_own_timeline(tmp_path):
    """The bug this pins: a run stamped with today's date against content
    dated years ago produces a cutoff that excludes everything, and an update
    that always finds nothing while looking entirely correct."""
    result = run_demo(lambda _e: None, delay=0, archive_dir=tmp_path, wave=0)
    cutoff = update_cutoff(result.archive)

    assert cutoff is not None
    published = [
        post.created
        for blog in result.interchange.blogs
        for post in blog.posts.values()
        if post.created
    ]
    assert published
    assert min(published) < cutoff, "the cutoff predates the content it should exclude"


def test_an_update_after_time_passes_gains_content(tmp_path):
    """The whole demonstration, in one assertion."""
    first = run_demo(lambda _e: None, delay=0, archive_dir=tmp_path, wave=0)
    before = _posts(first) + _topics(first)

    second = run_demo(lambda _e: None, delay=0, archive_dir=tmp_path, update=True, wave=1)
    after = _posts(second) + _topics(second)

    assert after > before, "letting time pass and updating found nothing new"


def test_an_update_without_time_passing_finds_nothing_new(tmp_path):
    """The control. If this also gained content, the previous test would be
    proving something other than what it claims."""
    first = run_demo(lambda _e: None, delay=0, archive_dir=tmp_path, wave=0)
    before = _posts(first) + _topics(first)

    again = run_demo(lambda _e: None, delay=0, archive_dir=tmp_path, update=True, wave=0)

    assert _posts(again) + _topics(again) == before
