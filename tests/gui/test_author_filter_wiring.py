"""The author filter is wired through the real seams: the served model
(`ModelSource`) and the Obsidian ingester both narrow to one user's
involvement when an author is given. Uses a real demo run for authored data.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.gui.demo import run_demo
from connections_export.gui.model_source import ModelSource
from connections_export.interchange.package import write_package


def _demo(tmp_path: Path):
    archive_dir = tmp_path / "archive"
    result = run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    return result.interchange, archive_dir


def _page_count(model) -> int:
    return sum(len(w.pages) for w in model.wikis)


def test_model_source_stash_filters_by_author(tmp_path):
    interchange, archive_dir = _demo(tmp_path)
    full = _page_count(interchange)

    # An author who authored some prototype pages (see fakeserver/prototype _AUTHORS).
    src = ModelSource()
    src.stash(interchange, archive_dir=archive_dir, author="A. Okafor")
    mine = src.get_model()
    kept = _page_count(mine)
    assert 0 < kept < full, "filtered model is a non-empty strict subset"
    # every kept page either involves the author or is a context ancestor
    for wiki in mine.wikis:
        for page in wiki.pages.values():
            involved = (
                page.author == "A. Okafor"
                or "A. Okafor" in page.contributors
                or any(c.author == "A. Okafor" for c in page.comments)
            )
            assert involved or page.is_context


def test_model_source_unknown_author_yields_empty(tmp_path):
    interchange, archive_dir = _demo(tmp_path)
    src = ModelSource()
    src.stash(interchange, archive_dir=archive_dir, author="Nobody At All")
    model = src.get_model()
    assert model.wikis == [] and model.blogs == [] and model.forums == []


def test_no_author_keeps_everything(tmp_path):
    interchange, archive_dir = _demo(tmp_path)
    src = ModelSource()
    src.stash(interchange, archive_dir=archive_dir, author=None)
    assert _page_count(src.get_model()) == _page_count(interchange)


def test_obsidian_ingest_author_narrows_the_vault(tmp_path):
    interchange, archive_dir = _demo(tmp_path)
    package = tmp_path / "pkg"
    write_package(
        interchange, Archive.open(archive_dir), package, generated_at="2026-07-28T00:00:00Z"
    )

    from connections_export.ingest import from_package

    all_stats = from_package(package, tmp_path / "vault-all")
    mine_stats = from_package(package, tmp_path / "vault-mine", author="A. Okafor")
    assert 0 < mine_stats.pages < all_stats.pages
