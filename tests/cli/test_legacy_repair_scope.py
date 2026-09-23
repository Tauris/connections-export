"""Recovering repair scope from a pre-provenance archive's manifest.

Old archives have run files with only basic fields -- no `completed_at`, no
`components` -- so `--repair` cannot read from run provenance what to re-crawl.
`_legacy_repair_scope` falls back to the one thing every archive has: the
request URLs in `manifest.jsonl`. This proves it recovers the blog, forum and
wiki component IDs and the community from those URLs, and that a missing
manifest is empty rather than an error.
"""

from __future__ import annotations

import json
from pathlib import Path

from connections_export.cli import _legacy_repair_scope

BLOG_UUID = "6fb61bb4-0000-0000-0000-000000000001"
FORUM_UUID = "6fb61bb4-0000-0000-0000-000000000002"
WIKI_UUID = "6fb61bb4-0000-0000-0000-000000000003"
COMMUNITY = "6fb61bb4-45b0-4c73-a299-9a4a898e2d0f"


def _write_manifest(archive_dir: Path, urls: list[str]) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "manifest.jsonl").write_text(
        "\n".join(json.dumps({"url": u}) for u in urls) + "\n", encoding="utf-8"
    )


def test_it_recovers_blog_forum_wiki_and_community_from_manifest_urls(tmp_path):
    archive = tmp_path / "archive"
    _write_manifest(
        archive,
        [
            f"https://host/communities/service/atom/community/blogs?communityUuid={COMMUNITY}",
            f"https://host/blogs/roller-ui/rendering/feed/{BLOG_UUID}/entries/atom?ps=10&page=0",
            "https://host/blogs/some-handle/feed/entrycomments/some-slug/atom?lang=en_us",
            f"https://host/forums/atom/topics?forumUuid={FORUM_UUID}&page=0",
            f"https://host/wikis/basic/api/wiki/{WIKI_UUID}/feed",
        ],
    )

    selected, community = _legacy_repair_scope(archive)

    assert community == COMMUNITY
    assert BLOG_UUID in selected["blog"]
    assert "some-handle" in selected["blog"]  # entrycomments handle also counts
    assert selected["forum"] == [FORUM_UUID]
    assert selected["wiki"] == [WIKI_UUID]


def test_ids_are_deduplicated_across_many_pages(tmp_path):
    archive = tmp_path / "archive"
    _write_manifest(
        archive,
        [
            f"https://host/blogs/roller-ui/rendering/feed/{BLOG_UUID}/entries/atom?ps=10&page={p}"
            for p in range(4)
        ],
    )

    selected, _ = _legacy_repair_scope(archive)

    assert selected["blog"] == [BLOG_UUID]


def test_a_missing_manifest_is_empty_not_an_error(tmp_path):
    selected, community = _legacy_repair_scope(tmp_path / "no-such-archive")

    assert community is None
    assert all(ids == [] for ids in selected.values())
