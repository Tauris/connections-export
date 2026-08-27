"""Rich Content in the demo: the community's front page.

An export that captures the wiki, blogs, forums and files but loses the page
people wrote *about* all of it has lost the thing that explains the rest. The
demo has to show that it does not.
"""

from __future__ import annotations

from connections_export.gui.demo import run_demo


def _run(**kwargs):
    events = []
    return run_demo(events.append, delay=0, **kwargs), events


def test_the_demo_captures_the_communitys_highlights(tmp_path):
    result, _ = _run(archive_dir=tmp_path)

    assert result.interchange.rich_content
    highlights = result.interchange.rich_content[0]
    assert highlights.page_ids


def test_the_pages_have_real_bodies(tmp_path):
    """A page with an empty body would demo nothing -- the body is the whole
    point, and it arrives inline in the entry."""
    result, _ = _run(archive_dir=tmp_path)
    highlights = result.interchange.rich_content[0]

    bodies = [highlights.pages[p].content_html or "" for p in highlights.page_ids]
    assert all(len(b) > 40 for b in bodies)
    assert any("<table" in b for b in bodies), "no page exercises table markup"


def test_the_demo_shows_a_widget_with_nothing_behind_it(tmp_path):
    """Placed and never written into. Real communities have these, and a demo
    where every widget has content would never exercise the reporting that
    keeps 'captured' from silently meaning 'all there was'."""
    result, _ = _run(archive_dir=tmp_path)
    highlights = result.interchange.rich_content[0]

    assert highlights.placed > highlights.initialized
    assert highlights.initialized == len(highlights.page_ids)


def test_rich_content_can_be_selected_alone(tmp_path):
    result, _ = _run(archive_dir=tmp_path, app_filter="rich_content")

    assert result.interchange.rich_content
    assert not result.interchange.wikis
    assert not result.interchange.blogs
    assert not result.interchange.forums
    assert not result.interchange.file_libraries


def test_excluding_it_leaves_the_other_apps_untouched(tmp_path):
    result, _ = _run(archive_dir=tmp_path, app_filter=("wiki", "blog"))

    assert not result.interchange.rich_content
    assert result.interchange.wikis


def test_a_demo_run_is_deterministic(tmp_path):
    first, _ = _run(archive_dir=tmp_path / "a")
    second, _ = _run(archive_dir=tmp_path / "b")

    def shape(result):
        highlights = result.interchange.rich_content[0]
        return [
            (highlights.pages[p].title, highlights.pages[p].version_label)
            for p in highlights.page_ids
        ]

    assert shape(first) == shape(second)
