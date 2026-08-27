"""Files in the demo: the fourth app, on the same footing as the other three.

The fakeserver has served a community library since the Files crawler was
written, but `run_demo` never asked it for one -- so a demo ingest showed
wikis, blogs and forums, and a user evaluating whether this tool captures
their community files had no way to see that it does.
"""

from __future__ import annotations

from connections_export.gui.demo import run_demo


def _run(**kwargs):
    events = []
    return run_demo(events.append, delay=0, **kwargs), events


def test_the_demo_captures_a_community_file_library(tmp_path):
    result, _ = _run(archive_dir=tmp_path)

    assert result.interchange.file_libraries
    library = result.interchange.file_libraries[0]
    assert library.file_ids, "a library with no files demonstrates nothing"


def test_the_demo_files_have_names_folders_and_bytes(tmp_path):
    """What makes the demo worth looking at is that it shows the real shape:
    named documents, filed in folders, with their content actually captured."""
    result, _ = _run(archive_dir=tmp_path)
    library = result.interchange.file_libraries[0]
    files = [library.files[fid] for fid in library.file_ids]

    assert all(f.name for f in files)
    assert any(f.folder_ids for f in files), "no file is filed in a folder"
    assert any(f.asset and f.asset.blob_hash for f in files), "no file content captured"


def test_the_files_app_can_be_selected_alone(tmp_path):
    """The filter has to work the same way it does for the other three: pick
    Files only, and the run captures files and nothing else."""
    result, _ = _run(archive_dir=tmp_path, app_filter="files")

    assert result.interchange.file_libraries
    assert not result.interchange.wikis
    assert not result.interchange.blogs
    assert not result.interchange.forums


def test_excluding_files_leaves_the_other_apps_untouched(tmp_path):
    result, _ = _run(archive_dir=tmp_path, app_filter=("wiki", "blog"))

    assert not result.interchange.file_libraries
    assert result.interchange.wikis
    assert result.interchange.blogs


def _with_bytes(result):
    library = result.interchange.file_libraries[0]
    return [fid for fid in library.file_ids if (a := library.files[fid].asset) and a.blob_hash]


def test_the_preview_limit_caps_the_documents_downloaded(tmp_path):
    """`max_entries` is the Preview stepper. For files it bounds DOWNLOADS,
    not the listing: the listing is read back from the archived library feed,
    which describes the whole library.

    That asymmetry is deliberate. If the listing were narrowed to whatever was
    downloaded, a file whose download FAILED would quietly disappear from the
    archive instead of showing up as listed-but-missing -- and losing a
    document without saying so is the one outcome this tool must never
    produce. So the library is always described in full, and the cap shows in
    which documents have bytes."""
    small, _ = _run(archive_dir=tmp_path / "small", max_entries=1)
    full, _ = _run(archive_dir=tmp_path / "full")

    assert len(_with_bytes(small)) == 1
    assert len(_with_bytes(full)) > 1
    # The library itself is described whole either way.
    assert len(small.interchange.file_libraries[0].file_ids) == len(
        full.interchange.file_libraries[0].file_ids
    )


def test_a_demo_run_is_deterministic(tmp_path):
    """Determinism is load-bearing across the demo: two runs of the same seed
    must yield the same library, or every downstream demo assertion is a
    coin toss."""
    first, _ = _run(archive_dir=tmp_path / "a")
    second, _ = _run(archive_dir=tmp_path / "b")

    def shape(result):
        library = result.interchange.file_libraries[0]
        return [(library.files[fid].name, library.files[fid].size) for fid in library.file_ids]

    assert shape(first) == shape(second)
