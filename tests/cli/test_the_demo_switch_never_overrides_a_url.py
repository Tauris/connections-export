"""`crawl --demo` and a URL is a contradiction, and says so.

`--demo` on a one-shot command is not a mode: it names what to capture,
the way a URL does. But given both, the URL was skipped without a word
-- the same shape as a flag that decides which deployment gets read
while an address sits in front of it, and the same silence.

There is nothing to infer here. Both were asked for, only one can
happen, and the person asking knows which they meant.
"""

from __future__ import annotations

from connections_export.cli import crawl_main


def test_naming_both_is_refused(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    code = crawl_main(["https://connections.example.corp/wikis/home/wiki/handbook", "--demo"])

    assert code == 2
    message = capsys.readouterr().err
    assert "cannot also capture a URL" in message
    assert "--demo" in message and "URL" in message


def test_the_demo_alone_still_captures_the_demo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    code = crawl_main(["--demo", "--output-dir", str(tmp_path / "out")])

    assert code == 0
    assert any((tmp_path / "out").rglob("manifest.jsonl")), "no archive was written"
