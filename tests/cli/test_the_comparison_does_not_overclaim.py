"""The comparison says which of its numbers the narrowing constrained.

Search is scoped to a community, and a community holds more than one
forum, so its results are narrowed to the forum being compared. The only
identifier available for that is the topic id, and the set of topic ids
for the forum comes from the naive crawl -- so the narrowing intersects
Search's answer with the very thing it is being compared against.

`MISSED by search` survives that: it is `naive - search`, and removing
items from Search cannot remove a miss. It is the number the question
turns on, and it is honest.

Three others do not survive it:

  * `search returned` becomes the size of the intersection, not of what
    Search returned -- and the unnarrowed total is the one thing that
    shows whether pagination hit a ceiling, which is how "returned
    everything" and "returned the first N" are told apart;
  * `extra from search` is zero by construction;
  * `superset noise` is confined to items the crawl already found.

Reporting those beside the real finding invites a reader to take them as
evidence. So the total is reported before narrowing, and the constrained
rows say that they are.
"""

from __future__ import annotations

from connections_export.cli import _print_comparison
from connections_export.compare.author_compare import Comparison


def _comparison(**over):
    base = dict(
        author="someone",
        naive_count=202,
        search_returned=202,
        search_authored=202,
        agreed=["t"] * 202,
        missed_by_search=[],
        extra_from_search=[],
        superset_noise=[],
    )
    base.update(over)
    return Comparison(**base)


def test_it_reports_what_search_returned_before_narrowing(capsys):
    _print_comparison(_comparison(), search_returned_unfiltered=987)

    out = capsys.readouterr().out
    assert "987" in out, "the unnarrowed total is not shown, so a page ceiling is invisible"


def test_the_constrained_rows_say_so(capsys):
    _print_comparison(_comparison(), search_returned_unfiltered=987)

    out = capsys.readouterr().out
    lower = out.lower()
    assert "narrow" in lower or "constrained" in lower or "this forum" in lower, (
        "nothing tells the reader which numbers the forum narrowing shaped"
    )


def test_without_narrowing_nothing_is_qualified(capsys):
    """A blog comparison does not narrow, so there is nothing to disclaim
    and no qualifier should appear."""
    _print_comparison(_comparison())

    out = capsys.readouterr().out
    assert "before narrowing" not in out.lower()
