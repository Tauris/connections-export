"""One normaliser for "is this the same person".

Three byte-identical copies of `_norm` lived in three packages, and
`crawler.crawl` imported the PRIVATE one out of `crawler.author_plan` rather
than any of them. Author matching decides what a filtered export keeps.
"""

from __future__ import annotations

from connections_export.identity import norm_identity


def test_case_and_surrounding_space_do_not_change_who_someone_is():
    assert norm_identity("  Jane Doe ") == norm_identity("jane doe")
    assert norm_identity("JANE DOE") == "jane doe"


def test_nothing_normalises_to_nothing():
    """`None` rather than `""`, so two unattributed items never match."""
    assert norm_identity(None) is None
    assert norm_identity("") is None
    assert norm_identity("   ") is None


def test_a_non_string_is_not_an_identity():
    assert norm_identity(123) is None  # type: ignore[arg-type]


def test_the_three_former_copies_are_now_one_object():
    """A rewiring that left a copy behind would still pass every behavioural
    test above while reintroducing the drift this deletes."""
    from connections_export.compare import author_compare
    from connections_export.crawler import author_plan
    from connections_export.derive import author_filter

    assert author_filter._norm is norm_identity
    assert author_compare._norm is norm_identity
    assert author_plan._norm is norm_identity
