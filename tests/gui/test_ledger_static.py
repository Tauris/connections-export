"""The archive-mode screen, over the served console assets.

Everything here is about the two things the user asked to be unmistakable:
what can be done where, and whether this run is capturing fresh or adding to
something. Both are properties of what the screen SAYS, so they are asserted
against the served text rather than a screenshot.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_css, served_console_html, served_console_js

HTML = served_console_html()
JS = served_console_js()
CSS = served_console_css()


def test_the_screen_has_a_ledger_to_render_into():
    assert 'id="archive-ledger"' in HTML
    assert 'id="ledger-rows"' in HTML


def test_a_row_says_what_it_would_cost_before_anything_is_chosen():
    """The per-app asymmetry has to be visible without opening anything -- it
    is the difference between a four-request update and a full rescan."""
    assert "function ledgerCost(" in JS
    assert "date query" in JS and "rescan" in JS and "full re-read" in JS


def test_the_cost_is_always_a_floor_never_a_promise():
    """What actually changed cannot be counted until the run looks. A number
    the run then exceeds would be worse than no number."""
    assert "at least" in JS
    assert "cannot be counted before the run looks" in JS


def test_captured_rows_default_to_update_and_new_ones_to_skip():
    """Someone who opened an archive came to bring it up to date; adding a
    component is a decision that should be taken, not arrived at."""
    view = JS[JS.index("function ledgerAction(") :]
    view = view[: view.index("\n  }")]

    assert 'row.in_archive ? "update" : "skip"' in view


def test_the_re_check_option_carries_its_explanation_where_it_is_chosen():
    """The user asked for this specifically: the gap has to be explained at
    the point of choosing, not only in the manual."""
    assert "RECHECK_TOOLTIP" in JS
    assert "label.title = RECHECK_TOOLTIP" in JS
    assert "may not count as changing it" in JS
    assert "one extra request per item" in JS


def test_every_update_method_has_a_note_including_the_one_that_cannot_ask():
    view = JS[JS.index("UPDATE_METHOD_NOTE") :]
    view = view[: view.index("\n  };")]

    assert "since:" in view and "rescan:" in view and "reread:" in view
    assert "offers no way to ask for changes" in view


def test_the_cutoff_is_explained_rather_than_asserted():
    """A date on screen with no account of where it came from is not something
    a user can judge."""
    assert "one minute before the last capture began" in JS
    assert "clock differences" in JS


def test_an_archive_with_no_finished_capture_says_so():
    assert "no completed capture to date from" in JS


def test_the_date_can_be_overridden_and_the_direction_is_stated():
    assert 'id="field-since"' in HTML
    assert "re-checks more, never less" in HTML


def test_the_run_is_labelled_extending_rather_than_starting():
    """Fresh vs extending must be legible at the point of consequence, not
    stated once at the top."""
    assert "Extend & update" in JS
    assert "function setStartLabel(" in JS


def test_the_estimate_names_which_kind_of_run_this_is():
    assert "Existing archive" in JS


def test_deletions_are_stated_plainly():
    assert "this is an archive, not a mirror" in HTML


def test_the_inherited_author_choice_is_stated_not_re_offered():
    """Mixing filters inside one archive would make it impossible to say what
    the archive is."""
    assert "the choice this archive was made with" in JS


def test_archives_offer_extend_beside_open():
    assert "Extend or update" in JS
    assert "openArchiveForUpdate(a.name)" in JS


def test_the_ledger_replaces_the_fresh_capture_controls():
    """Showing both would offer two contradictory ways to say the same thing."""
    view = JS[JS.index("function openArchiveForUpdate(") :]
    view = view[: view.index("\n  //: The archive this run")]

    assert "components.hidden = true" in view
    assert "modes.hidden = true" in view


def test_the_ledger_is_styled_with_the_consoles_own_tokens():
    assert ".ledger-row" in CSS
    assert "var(--line)" in CSS and "var(--accent-soft)" in CSS


def test_the_demo_can_let_time_pass():
    """Without it, the update story is reachable from the test suite and the
    API and not by clicking -- so the feature that most needs demonstrating is
    the one nobody can watch work."""
    assert 'id="demo-advance"' in HTML
    assert "let a week pass" in HTML
    assert "demo_wave" in JS


def test_the_time_control_is_demo_only():
    """On a real deployment time passes without being asked."""
    assert "runHasStarted && lastStartBody && lastStartBody.demo" in JS


def test_fresh_capture_notice_is_not_labelled_as_an_error():
    assert 'notify("Capturing fresh — this will create a new archive.", "New archive")' in JS


def test_the_run_carries_what_archive_mode_decided():
    """`into` and the re-check choice have to reach the run, or the ledger is
    a form that reports nothing."""
    view = JS[JS.index("function startBody") :]
    view = view[: view.index("\n    }")]

    assert "into: archiveSubject" in view
    assert "recheck_comments" in view
