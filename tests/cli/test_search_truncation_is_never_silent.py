"""A Search answer cut short at our own page limit says so.

The comparison walks Search's pages until one comes back short. It also
stops after forty pages -- 6,000 results -- and those two endings are not
the same thing. One means "that was all of it". The other means "that was
as far as we looked", and everything after it is unknown.

The loop could not tell them apart, so a truncated answer produced a
comparison that read exactly like a complete one. `MISSED by search = 0`
then means only that nothing was missed *among what we asked for*, which
is not the question.

It matters at real sizes. A forum of 2,000 topics sits in a community
holding others, and the person query is scoped to the community: reaching
6,000 is an ordinary outcome there, not a pathological one.
"""

from __future__ import annotations

from connections_export.cli import _search_pagination_note

PAGE = 150
LIMIT = 40


def test_a_short_page_means_the_answer_was_complete():
    note = _search_pagination_note(pages_read=3, last_batch=42, page_size=PAGE, page_limit=LIMIT)

    assert note is None, "a completed walk should not warn about anything"


def test_running_out_of_pages_is_reported():
    note = _search_pagination_note(
        pages_read=LIMIT, last_batch=PAGE, page_size=PAGE, page_limit=LIMIT
    )

    assert note, "a walk that hit its own page limit reported nothing"
    assert "6000" in note or "6,000" in note, "the note does not say how much was seen"
    assert "MISSED" in note, "the note does not say which conclusion is unsafe"


def test_the_exact_boundary_is_still_suspect():
    """A last page that is exactly full, on the last page allowed, is the
    shape of truncation even if the next page would have been empty. It
    cannot be told apart without asking, so it is reported."""
    note = _search_pagination_note(
        pages_read=LIMIT, last_batch=PAGE, page_size=PAGE, page_limit=LIMIT
    )

    assert note is not None


def test_a_full_last_page_below_the_limit_is_fine():
    """Stopping early with a full page cannot happen -- the loop continues --
    so this only guards the note against firing on a normal walk."""
    note = _search_pagination_note(pages_read=5, last_batch=PAGE, page_size=PAGE, page_limit=LIMIT)

    assert note is None
