"""The Archives screen says an archive can come from outside the folder.

The capability existed and nothing announced it. The line that did was on
Select & Tailor -- the capture screen, which is not where anyone looks when
they want to READ something they were sent. And the drag veil armed only that
screen's panel, so dragging an archive over Archives produced no feedback at
all: the page looked like it was ignoring you until you let go.

Dragging is also not the only way people work. The same zone offers a file
picker and a field for a path or a link, so nothing here requires a mouse
gesture.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_css, served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()
CSS = served_console_css()
ARCHIVES = HTML[HTML.index('id="panel-archives"') : HTML.index('id="panel-select"')]


def test_the_archives_screen_says_you_can_drop_one():
    assert 'id="archive-drop"' in ARCHIVES
    assert ".zip" in ARCHIVES


def test_it_names_where_an_archive_can_come_from():
    """A folder, a zip, a link -- the three things people actually have."""
    lowered = ARCHIVES.lower()
    assert "folder" in lowered
    assert "link" in lowered


def test_it_says_the_archive_need_not_live_in_this_folder():
    """The whole point: it is not filed, and does not have to be."""
    assert "anywhere" in ARCHIVES.lower()


def test_there_is_a_way_in_without_dragging():
    """A drop zone that only accepts drops excludes anyone who does not drag."""
    assert 'id="archive-choose"' in ARCHIVES
    assert 'id="archive-path"' in ARCHIVES
    assert 'type="file"' in ARCHIVES


def test_the_drag_veil_arms_on_the_archives_screen_too():
    """It armed the capture screen's panel only, so a drag over Archives
    showed nothing at all."""
    assert "dropTargets" in JS
    assert ".drop-armed" in CSS
    assert "#archive-drop.drop-armed" in CSS


def test_the_three_routes_reach_the_same_place():
    assert JS.count("openDroppedArchive(") >= 4  # drop x2, picker, path field
