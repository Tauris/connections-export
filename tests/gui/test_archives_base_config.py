"""#23: run archives land in a configurable location instead of always
under the system temp dir. `_resolve_archives_base` is the pure resolver
behind the module-level `ARCHIVES_BASE` -- tested directly (not by
reloading `connections_export.gui.app`, which many other tests already
monkeypatch `ARCHIVES_BASE` on and would be disturbed by a reload) so the
env-var precedence and the `"."` shorthand are covered without any
import-order fragility.
"""

from __future__ import annotations

from pathlib import Path

from connections_export.gui.app import ARCHIVES_DIR_ENV, _resolve_archives_base


def test_defaults_to_cwd_slash_connections_export_archives(tmp_path):
    assert _resolve_archives_base({}, tmp_path) == tmp_path / "connections-export-archives"


def test_env_var_dot_resolves_to_cwd_itself(tmp_path):
    env = {ARCHIVES_DIR_ENV: "."}
    assert _resolve_archives_base(env, tmp_path) == tmp_path


def test_env_var_explicit_path_is_used_verbatim(tmp_path):
    target = tmp_path / "somewhere-else"
    env = {ARCHIVES_DIR_ENV: str(target)}
    assert _resolve_archives_base(env, tmp_path) == target


def test_env_var_empty_string_falls_back_to_default(tmp_path):
    # An empty env value (e.g. an unset-but-exported var) is not a
    # deliberate override -- same fallback as not setting it at all.
    env = {ARCHIVES_DIR_ENV: ""}
    assert _resolve_archives_base(env, tmp_path) == tmp_path / "connections-export-archives"


def test_module_level_archives_base_is_a_path():
    # Resolved once at import time from the real environment/cwd -- just
    # confirm the type and shape, since the value depends on the process's own
    # environment.
    #
    # It lives in `gui.support`, and deliberately is NOT re-exported from
    # `gui.app`: `PUT /api/archives-dir` reassigns it at run time, so a
    # by-value re-export would be a snapshot that silently stopped tracking
    # the real value. Read it -- and patch it -- where it is defined.
    from connections_export.gui import support as gui_support

    assert isinstance(gui_support.ARCHIVES_BASE, Path)


def test_archives_base_is_not_re_exported_from_app():
    """A by-value copy of a value that changes at run time is the shape of bug
    this codebase has now hit four times (the page cap forked between the
    crawler and derive; the crawl registry captured at import; this). Keeping
    one definition is the fix; this stops the convenience alias coming back."""
    import connections_export.gui.app as app_module

    assert not hasattr(app_module, "ARCHIVES_BASE")
