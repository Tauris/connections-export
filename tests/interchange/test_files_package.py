"""A community's files land in the package as files, not as hex digests.

`blobs/` stays -- it is the integrity anchor and the deduplicated bytes -- but a
library of 65 documents addressed by hash is not an archive anyone can use. The
package therefore also carries `files/<folder>/<name>`, plus a manifest that
makes every compromise undoable: copying the directory out is enough for most
purposes, and a target system that can hold what a filesystem could not needs to
know what was changed and why.
"""

from __future__ import annotations

import json

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import crawl_files
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize, synthesize_files
from connections_export.interchange.package import write_package
from tests.crawler.conftest import make_client

COMMUNITY = "c-1"


def _packaged(tmp_path):
    seed = SynthSeed(seed=0, wiki_count=1, depth=1, pages_per_level=1, human_names=True)
    fileset = synthesize_files(seed, community_uuid=COMMUNITY)
    archive = Archive.open(tmp_path / "archive")
    crawl_files(
        config=Config(base_url="https://fake"),
        client=make_client(make_app(synthesize(seed), fileset=fileset)),
        archive=archive,
        community_uuid=COMMUNITY,
        emit=lambda _e: None,
    )
    interchange = derive(archive, base_url="https://fake")
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at="2026-01-01T00:00:00Z")
    manifest = json.loads((dest / "files.json").read_text(encoding="utf-8"))
    return dest, manifest, fileset.libraries[0]


def test_every_file_is_written_under_a_readable_name(tmp_path):
    dest, manifest, source = _packaged(tmp_path)

    assert len(manifest["files"]) == len(source.files)
    unwritten = [r["original"] for r in manifest["files"] if not r["written"]]
    assert unwritten == [], f"files listed but not written: {unwritten}"
    for record in manifest["files"]:
        assert (dest / record["path"]).is_file()


def test_the_bytes_on_disk_are_the_bytes_captured(tmp_path):
    """A named copy that is not the file would be worse than no copy."""
    dest, manifest, source = _packaged(tmp_path)
    # Keyed by ID, not name: two documents share a name here, which is exactly
    # the case the package has to keep apart.
    by_id = {f.uuid: f.body for f in source.files}

    for record in manifest["files"]:
        written = (dest / record["path"]).read_bytes()
        assert written == by_id[record["file_id"]]


def test_files_live_in_their_folders(tmp_path):
    dest, manifest, _source = _packaged(tmp_path)

    foldered = [r for r in manifest["files"] if r["folders"]]
    assert foldered, "no file was filed in a folder"
    for record in foldered:
        assert record["path"].startswith(f"files/{record['folders'][0]}/")


def test_an_unchanged_name_records_no_reason(tmp_path):
    """Silence means "this is exactly what Connections holds", so a consumer
    never has to compare strings to find out."""
    _dest, manifest, _source = _packaged(tmp_path)

    clean = [r for r in manifest["files"] if not r["reasons"]]
    assert clean
    assert all(r["name"] == r["original"] for r in clean)


def test_every_compromise_is_named_and_reversible(tmp_path):
    """The original is always kept, whatever had to be done to write it."""
    _dest, manifest, _source = _packaged(tmp_path)

    by_original = {r["original"]: r for r in manifest["files"]}
    assert "forbidden-characters" in by_original["Q1 Budget: draft?.xlsx"]["reasons"]
    assert "reserved-device-name" in by_original["CON.txt"]["reasons"]
    assert "trailing-dot-or-space" in by_original["Runbook."]["reasons"]
    assert all(r["original"] for r in manifest["files"])


def test_names_that_collide_are_all_suffixed_and_all_survive(tmp_path):
    """Three files here share a name once case is folded -- two exactly, one by
    case alone, which is one file on Windows. All three must exist separately."""
    dest, manifest, _source = _packaged(tmp_path)

    colliding = [r for r in manifest["files"] if "collision" in r["reasons"]]
    assert len(colliding) == 3
    written = {(dest / r["path"]).read_bytes() for r in colliding}
    assert len(written) == 3, "colliding files overwrote each other"


def test_paths_are_unique_on_a_case_insensitive_filesystem(tmp_path):
    """The package may be unzipped on Windows or macOS, where two paths
    differing only in case are one file."""
    _dest, manifest, _source = _packaged(tmp_path)

    keys = {r["path"].casefold() for r in manifest["files"]}
    assert len(keys) == len(manifest["files"])


def test_each_file_names_the_blob_it_came_from(tmp_path):
    """`files/` is a convenience copy; the blob remains the source of truth."""
    _dest, manifest, _source = _packaged(tmp_path)

    assert all(r["blob"] for r in manifest["files"])


def test_a_package_without_files_gets_no_files_manifest(tmp_path):
    """Most exports have no community library, and should not carry an empty
    one."""
    from connections_export.derive.model import Interchange

    dest = tmp_path / "empty-package"
    archive = Archive.open(tmp_path / "archive")
    write_package(Interchange(), archive, dest, generated_at="2026-01-01T00:00:00Z")

    assert not (dest / "files.json").exists()
