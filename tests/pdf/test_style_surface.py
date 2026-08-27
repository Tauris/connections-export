"""Readers can change how a PDF looks without depending on our rule structure.

The supported surface is a set of `--pdf-*` design tokens; below them the rules
are ours to rearrange. Three things have to hold for that promise to mean
anything, and each is easy to break silently:

  * the settable names are DERIVED from the stylesheet, so adding a token makes
    it settable with no second list to update
  * a name nobody recognises is an error, because a silently ignored setting is
    indistinguishable from one that did not work
  * reader CSS is emitted after the captured pages' own CSS -- author styles
    live in the body and outrank <head> at equal specificity, which is exactly
    how a sample page's `body { font: 16px }` once set the size of every export
"""

from __future__ import annotations

import pytest

from connections_export.config import Config
from connections_export.pdf.html import _STYLE, style_overrides_css, style_token_names


def test_token_names_come_from_the_stylesheet_itself():
    names = style_token_names()

    assert "body-size" in names and "toc-title-size" in names
    for name in names:
        assert f"--pdf-{name}:" in _STYLE


def test_every_token_is_actually_used_by_a_rule():
    """A token nothing reads is a promise we are not keeping."""
    unused = [n for n in style_token_names() if f"var(--pdf-{n})" not in _STYLE]

    assert unused == [], f"tokens defined but never used: {unused}"


def test_overrides_render_as_a_root_block():
    css = style_overrides_css({"body_size": "11pt", "font": "Georgia, serif"})

    assert css.startswith(":root {")
    assert "--pdf-body-size: 11pt;" in css
    assert "--pdf-font: Georgia, serif;" in css


def test_underscores_and_hyphens_both_work():
    assert style_overrides_css({"toc_title_size": "18pt"}) == style_overrides_css(
        {"toc-title-size": "18pt"}
    )


def test_an_unknown_token_is_reported_not_ignored():
    with pytest.raises(ValueError, match="unknown PDF style token"):
        style_overrides_css({"bodysize": "11pt"})


def test_no_overrides_adds_no_css():
    assert style_overrides_css({}) == ""


def test_config_carries_the_style_settings():
    config = Config(pdf_style={"body_size": "12pt"}, pdf_css=None)

    assert config.pdf_style == {"body_size": "12pt"}
