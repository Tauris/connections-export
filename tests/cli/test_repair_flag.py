"""`crawl --repair` re-reads a captured archive to recover missed content.

It is a targeted recovery, not a fresh capture: it needs `--into` (the
archive to repair) and refuses URLs or `--component`, because what to read is
taken from the archive's own recorded provenance, not from the command line.
"""

from __future__ import annotations

from connections_export import cli


def test_repair_requires_into(capsys):
    code = cli.crawl_main(["--repair"])
    assert code == 2
    assert "--repair requires --into" in capsys.readouterr().err


def test_repair_refuses_a_url(tmp_path, capsys):
    code = cli.crawl_main(["--repair", "--into", str(tmp_path / "a"), "https://fake/wikis/y"])
    assert code == 2
    err = capsys.readouterr().err
    assert "--repair" in err and "cannot be combined" in err
