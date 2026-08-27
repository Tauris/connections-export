"""Archive directory names say WHAT was captured, not just where and when.

A name carried the deployment host and a timestamp, so a directory full of
runs against one system was indistinguishable without opening each one --
which of a hundred forums, which community, which wiki.
"""

from __future__ import annotations

import re

from connections_export.gui import app as app_module
from connections_export.gui import support as gui_support
from connections_export.gui.archives import SLUG_MAX, _display_name, slugify

NEW_NAME = re.compile(r"^export-(\d{8})-(\d{6})-(.+)-([0-9a-f]{4})$")


def test_slugify_is_filesystem_safe_and_short():
    assert slugify("Platform Engineering") == "platform-engineering"
    assert slugify("Help & Support") == "help-support"
    assert slugify("  spaced / out  ") == "spaced-out"
    assert slugify(None) == ""
    assert slugify("") == ""


def test_a_long_name_is_cut_at_a_word_boundary():
    """A slug cut mid-word reads as a typo rather than a shortening."""
    slug = slugify("A very long community name that keeps going and going")

    assert len(slug) <= SLUG_MAX
    assert not slug.endswith("-")
    assert slug in "a-very-long-community-name-that-keeps-going-and-going"


def test_a_single_long_word_is_still_truncated():
    """The word-boundary preference must not defeat the limit."""
    slug = slugify("Supercalifragilisticexpialidocious")

    assert 0 < len(slug) <= SLUG_MAX


def test_the_name_carries_the_captured_thing_the_date_and_a_unique_suffix(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)

    path = app_module._new_run_archive_dir(demo=False, label="Platform Engineering")
    match = NEW_NAME.match(path.name)

    assert match, path.name
    date_part, time_part, slug, unique = match.groups()
    assert slug == "platform-engineering"
    assert len(date_part) == 8 and len(time_part) == 6
    assert len(unique) == 4
    assert path.is_dir()


def test_two_runs_of_the_same_thing_get_different_directories(tmp_path, monkeypatch):
    """Same target, same second: the unique suffix is what keeps them apart."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)

    names = {
        app_module._new_run_archive_dir(demo=True, label="Help & Support").name for _ in range(12)
    }

    assert len(names) == 12
    assert all("help-support" in n for n in names)


def test_a_run_without_a_label_still_gets_a_valid_name(tmp_path, monkeypatch):
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)

    path = app_module._new_run_archive_dir(demo=False, label=None)

    assert path.is_dir()
    assert _display_name(path.name) != path.name, "it must still be humanised"


def test_display_puts_the_captured_thing_first():
    shown = _display_name("export-20260820-143000-platform-eng-a1b2")

    assert shown.startswith("platform-eng"), shown
    assert "2026-08-20 14:30" in shown


def test_the_deployment_host_is_not_in_the_name(tmp_path, monkeypatch):
    """Everyone saves from one system, so repeating it in every directory
    spent length the captured thing's name needs. It stays recorded in the
    run's manifest and summary."""
    monkeypatch.setattr(gui_support, "ARCHIVES_BASE", tmp_path)

    name = app_module._new_run_archive_dir(demo=False, label="Platform Engineering").name

    assert "example" not in name and "connections." not in name
    assert name.startswith("export-2")  # straight from `export-` to the date


def test_names_written_when_the_host_was_included_still_read(tmp_path):
    """Both shapes coexist in one directory: an existing archive keeps its
    host in the name and must still be shown with its slug and date."""
    shown = _display_name("export-connections.example.corp-20260820-143000-platform-eng-a1b2")

    assert shown == "platform-eng · connections.example.corp · 2026-08-20 14:30"


def test_archives_named_before_this_still_read_correctly():
    """Older directories end in a bare, dash-free unique suffix that names
    nothing -- they must not have it shown as though it were a title."""
    shown = _display_name("export-connections.example.corp-20260819-084442-jax2oa2")

    assert shown == "connections.example.corp · 2026-08-19 08:44"
    assert "jax2oa2" not in shown


def test_the_console_sends_what_the_run_is_called():
    """The name has to come from the client: the server knows the community's
    uuid and the component ids, not what a person calls them."""
    from tests.gui._served_assets import served_console_js

    js = served_console_js()
    assert "target_label: identifiedTargetLabel || target || null" in js
    assert "if (data && data.community) identifiedTargetLabel = data.community;" in js


def test_start_falls_back_when_no_label_was_sent():
    """A run started by anything other than the console -- a script, a
    replayed request -- still names something."""
    import inspect

    # `start` moved out of `make_app` into `gui/routes/run.py`.
    from connections_export.gui.routes import run as routes_run

    source = inspect.getsource(routes_run)
    assert "body.target_label" in source
    assert "or body.wiki_label" in source
    assert "or body.blog_handle" in source
