"""The Overview offers the two things you can do, and gets out of the way.

There were four buttons across two lines: a green "Paste a URL to start", a
demo, "Open the manual" -- which is in the sidebar, on every screen -- and
"Open Archives", exiled below a rule as though it were an afterthought.

There are two actions: make an archive, or read one. Neither outranks the
other, and the green said otherwise: reading is the half that still matters
with no deployment in reach, which is the whole point of the tool.
The demo is worth knowing about and is not a third peer, so it sits beside
them rather than among them.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()
HERO = HTML[HTML.index('class="ov-ctas"') : HTML.index('id="panel-archives"')]


def test_the_two_real_actions_are_offered_together():
    assert 'id="ov-start"' in HERO
    assert 'id="ov-archives"' in HERO


def test_neither_action_is_dressed_as_the_important_one():
    """The green button said capture outranks reading. It does not -- reading
    is what remains when the deployment is gone."""
    assert 'id="ov-start"' in HERO
    start = HERO[HERO.index('id="ov-start"') - 60 : HERO.index('id="ov-start"')]
    assert "primary" not in start


def test_the_manual_button_is_gone_because_the_sidebar_has_it():
    assert 'id="ov-manual"' not in HTML
    assert 'data-section="manual"' in HTML  # still one click away, from anywhere
    assert 'getElementById("ov-manual")' not in JS


def test_archives_is_not_exiled_below_a_rule():
    """It was under its own divider, reading as a footnote to the real
    choice rather than as half of it."""
    assert "ov-recent-wrap" not in HTML


def test_the_demo_sits_beside_the_actions_rather_than_among_them():
    assert 'id="ov-demo"' in HERO
    assert "ov-cta-aside" in HERO


def test_the_actions_still_go_where_they_did():
    assert 'showSection("select")' in JS
    assert 'showSection("archives")' in JS
