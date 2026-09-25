"""`connections-export ingest` with several `--archive`s writes one export.

Repeating `--archive` (or `--package`) combines everything given into one
Hugo site, Jekyll site or Obsidian vault (`derive.combine`); the summary says
how many archives went in, how many duplicates were merged and how many links
between archives now point inside the export. One `--archive` behaves exactly
as it always has.
"""

from __future__ import annotations

from connections_export.cli import ingest_main
from connections_export.gui.demo import run_demo


def _archives(tmp_path):
    wiki = tmp_path / "wiki-archive"
    blog = tmp_path / "blog-archive"
    run_demo(lambda _e: None, archive_dir=wiki, delay=0, app_filter="wiki")
    run_demo(lambda _e: None, archive_dir=blog, delay=0, app_filter="blog")
    return wiki, blog


def test_repeated_archives_are_combined_into_one_export(tmp_path, capsys):
    wiki, blog = _archives(tmp_path)
    out = tmp_path / "site"

    code = ingest_main(
        ["--format", "hugo", "--archive", str(wiki), "--archive", str(blog), "--output", str(out)]
    )

    assert code == 0
    assert (out / "content" / "wikis").is_dir() and (out / "content" / "blogs").is_dir()
    printed = capsys.readouterr().out
    assert "2 archives combined" in printed
    assert "duplicate(s) merged" in printed and "cross-archive link(s) resolved" in printed
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "- wiki-archive" in readme and "- blog-archive" in readme


def test_the_same_archive_twice_reports_every_container_as_a_duplicate(tmp_path, capsys):
    """Nothing disappears silently: each container both held is named with the
    archive whose copy was kept."""
    wiki, _blog = _archives(tmp_path)
    again = tmp_path / "wiki-again"
    run_demo(lambda _e: None, archive_dir=again, delay=0, app_filter="wiki")

    code = ingest_main(
        [
            "--format",
            "obsidian",
            "--archive",
            str(wiki),
            "--archive",
            str(again),
            "--output",
            str(tmp_path / "vault"),
        ]
    )

    assert code == 0
    printed = capsys.readouterr().out
    assert "0 duplicate(s)" not in printed
    assert "kept from" in printed


def test_a_single_archive_is_reported_as_before(tmp_path, capsys):
    wiki, _blog = _archives(tmp_path)

    code = ingest_main(
        ["--format", "jekyll", "--archive", str(wiki), "--output", str(tmp_path / "site")]
    )

    assert code == 0
    printed = capsys.readouterr().out
    assert printed.startswith("connections-export ingest: from the archive, ")
    assert "combined" not in printed
    assert not (tmp_path / "site" / "sources.md").exists()


def test_an_unreadable_archive_among_several_says_which(tmp_path, capsys):
    wiki, _blog = _archives(tmp_path)

    code = ingest_main(
        [
            "--format",
            "hugo",
            "--archive",
            str(wiki),
            "--archive",
            str(tmp_path / "nowhere"),
            "--output",
            str(tmp_path / "site"),
        ]
    )

    assert code == 1
    assert "nowhere" in capsys.readouterr().err
