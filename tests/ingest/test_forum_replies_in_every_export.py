"""A forum thread keeps its replies, nested, in every export.

The demo's "How to migrate off the old importer?" topic has replies four
deep. Jekyll left replies out altogether -- a thread without its answers --
and Obsidian marked nesting only with deeper headings, which a rendered note
shows at one indent. Both now write every reply with its body, one
blockquote deeper per level, as the Hugo export does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from connections_export.cli import ingest_main
from connections_export.gui.demo import run_demo

TOPIC = "How to migrate off the old importer"


@pytest.fixture(scope="module")
def archive(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("demo") / "archive"
    run_demo(lambda _event: None, archive_dir=path, delay=0)
    return path


def _export(archive: Path, fmt: str, out: Path) -> str:
    assert ingest_main(["--format", fmt, "--archive", str(archive), "--output", str(out)]) == 0
    for note in out.rglob("*.md"):
        text = note.read_text(encoding="utf-8")
        front = text.split("\n---\n", 1)[0] if text.startswith("---") else ""
        if f'title: "{TOPIC}' in front or f"\n# {TOPIC}" in text:
            return text
    raise AssertionError(f"no exported file for {TOPIC!r}")


@pytest.mark.parametrize("fmt", ["jekyll", "obsidian"])
def test_every_reply_is_exported_and_nested_replies_are_indented(archive, tmp_path, fmt):
    text = _export(archive, fmt, tmp_path / fmt)

    assert "## Replies" in text
    assert "S. Nakamura" in text  # a top-level reply
    assert "\n> ### M. Lindqvist" in text  # its reply, one level in
    assert "\n>> ### J. Weber" in text  # two levels in
    assert "\n>>> ### T. Bianchi" in text  # three levels in
    assert "####" not in text  # the quote depth carries the nesting
    assert "Worth the extra step." in text  # bodies come along
