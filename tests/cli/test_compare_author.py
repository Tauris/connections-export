"""`connections-export compare-author --demo`: the search-vs-naive trust check."""

from connections_export.cli import compare_author_main


def test_compare_author_demo_reports_disagreement(capsys):
    rc = compare_author_main(["--author", "A. Okafor", "--demo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Author-filter comparison for: A. Okafor" in out
    assert "naive scan" in out and "search authored" in out
    assert "MISSED by search" in out
    # the demo's Search is author-indexed, so it misses comment-only involvement
    assert "DISAGREES" in out


def test_compare_author_requires_demo():
    assert compare_author_main(["--author", "someone"]) == 2
