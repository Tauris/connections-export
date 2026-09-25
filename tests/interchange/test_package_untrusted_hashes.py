"""A package's blob hashes are someone else's text, never a path.

`interchange.json` travels between people, and `ResolvedAsset.blob_hash` is
whatever it says. Writing a package from a model, and reading blobs back out
of one, both turn that string into a filename -- so both must refuse one
that is not a SHA-256 digest, or `sha256:../../x` reads and WRITES files
outside the package.
"""

from __future__ import annotations

import pytest

from connections_export.archive.blobs import write_blob
from connections_export.archive.store import Archive
from connections_export.derive.model import (
    DerivedFile,
    DerivedFileLibrary,
    DerivedPage,
    DerivedWiki,
    Interchange,
    Provenance,
    ResolvedAsset,
)
from connections_export.interchange.package import open_blob, write_package

GENERATED_AT = "2026-07-20T12:00:00Z"


def _asset(blob_hash: str) -> ResolvedAsset:
    return ResolvedAsset(
        original_href="img/x.png",
        resolved_url="https://fake/img/x.png",
        blob_hash=blob_hash,
        present=True,
        scope="same",
    )


def _interchange(blob_hash: str, good_hash: str) -> Interchange:
    page = DerivedPage(
        id="p1",
        label="p1",
        title="Page 1",
        content_html="<p>hi</p>",
        assets=[_asset(blob_hash), _asset(good_hash)],
        provenance=Provenance(hcl_id="p1"),
    )
    library = DerivedFileLibrary(
        id="lib",
        title="Files",
        file_ids=["f1"],
        files={"f1": DerivedFile(id="f1", name="evil.txt", asset=_asset(blob_hash))},
    )
    return Interchange(
        base_url="https://fake",
        source_version="8.0",
        run_id="run-1",
        hcl_hosts=["fake"],
        wikis=[
            DerivedWiki(id="w1", label="w", title="W", root_page_ids=["p1"], pages={"p1": page})
        ],
        file_libraries=[library],
    )


def test_writing_a_package_never_reads_or_writes_outside_it(tmp_path):
    """The archive is at tmp/archive and the package at tmp/out/pkg, so
    `../../secret` resolves to tmp/secret on the read side and tmp/out/secret
    on the write side. Neither may happen; the good blob still lands."""
    (tmp_path / "secret").write_bytes(b"private key")
    archive = Archive.open(tmp_path / "archive")
    good = "sha256:" + write_blob(archive.root, b"pixel")
    dest = tmp_path / "out" / "pkg"

    write_package(
        _interchange("sha256:../../secret", good), archive, dest, generated_at=GENERATED_AT
    )

    assert not (tmp_path / "out" / "secret").exists()
    written = [p.read_bytes() for p in dest.rglob("*") if p.is_file()]
    assert b"private key" not in written
    assert (dest / "blobs" / good.split(":")[1]).read_bytes() == b"pixel"


@pytest.mark.parametrize("value", ["../../secret", "sha256:../../secret", "sha256:"])
def test_open_blob_treats_a_hash_that_is_not_one_as_absent(tmp_path, value):
    """Absent, in the form callers already handle (`FileNotFoundError`), so
    the Obsidian writer shows a gap instead of the file -- or a crash."""
    (tmp_path / "secret").write_bytes(b"private key")
    (tmp_path / "pkg" / "blobs").mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        open_blob(tmp_path / "pkg", value)
