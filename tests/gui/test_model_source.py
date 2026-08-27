"""`model_source.py` owns "what derived model does this server
expose". Pure/offline over hand-built
`Interchange`s + `Archive`s and written packages -- no crawl/derive
needed to prove the contract itself, mirroring
`tests/interchange/test_package.py`'s style.
"""

from __future__ import annotations

from pathlib import Path

from connections_export.archive.blobs import write_blob
from connections_export.archive.store import Archive
from connections_export.derive.model import (
    DerivedAttachment,
    DerivedPage,
    DerivedWiki,
    Interchange,
    Provenance,
    ResolvedAsset,
)
from connections_export.gui.model_source import ModelSource
from connections_export.interchange.package import write_package

GENERATED_AT = "2026-07-20T12:00:00Z"


def _archive_with_blob(tmp_path: Path, data: bytes) -> tuple[Archive, str]:
    archive = Archive.open(tmp_path / "archive")
    digest = write_blob(archive.root, data)
    return archive, f"sha256:{digest}", digest


def _sample_interchange(blob_hash: str | None, *, present: bool = True) -> Interchange:
    asset = ResolvedAsset(
        original_href="img/diagram.png",
        resolved_url="https://fake/img/diagram.png",
        blob_hash=blob_hash,
        present=present,
        scope="same",
    )
    attachment_asset = ResolvedAsset(
        original_href="files/notes.pdf",
        resolved_url="https://fake/files/notes.pdf",
        blob_hash=blob_hash,
        present=present,
        scope="same",
    )
    attachment = DerivedAttachment(
        id="a1", filename="notes.pdf", content_type="application/pdf", asset=attachment_asset
    )
    page = DerivedPage(
        id="p1",
        label="p1",
        title="Page 1",
        content_html="<p>hi</p>",
        assets=[asset],
        attachments=[attachment],
        provenance=Provenance(hcl_id="p1"),
    )
    wiki = DerivedWiki(
        id="w1", label="wiki0", title="Wiki 0", root_page_ids=["p1"], pages={"p1": page}
    )
    return Interchange(
        base_url="https://fake",
        source_version="8.0",
        run_id="run-1",
        hcl_hosts=["fake"],
        wikis=[wiki],
    )


# --- empty source -----------------------------------------------------


def test_empty_model_source_has_no_model_yet():
    source = ModelSource()

    assert source.get_model() is None


def test_empty_model_source_has_no_blobs():
    source = ModelSource()

    assert source.get_blob("0" * 64) is None


# --- demo-run stash -----------------------------------------------------


def test_stash_makes_the_model_available(tmp_path):
    archive, blob_hash, digest = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    source = ModelSource()

    source.stash(interchange, archive_dir=archive.root)

    assert source.get_model() is interchange


def test_stash_serves_blobs_from_the_archive(tmp_path):
    archive, blob_hash, digest = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    source = ModelSource()

    source.stash(interchange, archive_dir=archive.root)

    result = source.get_blob(digest)
    assert result is not None
    data, content_type = result
    assert data == b"pixel-bytes"


def test_stash_content_type_from_attachment_when_known(tmp_path):
    archive, blob_hash, digest = _archive_with_blob(tmp_path, b"pdf-bytes")
    interchange = _sample_interchange(blob_hash)
    source = ModelSource()

    source.stash(interchange, archive_dir=archive.root)

    _data, content_type = source.get_blob(digest)
    assert content_type == "application/pdf"


def test_stash_content_type_defaults_to_octet_stream_when_unknown(tmp_path):
    # A blob referenced only by an inline-image asset (no attachment
    # entry) carries no known content-type in the model.
    archive = Archive.open(tmp_path / "archive")
    digest = write_blob(archive.root, b"png-bytes")
    blob_hash = f"sha256:{digest}"
    asset = ResolvedAsset(
        original_href="img/x.png",
        resolved_url="https://fake/img/x.png",
        blob_hash=blob_hash,
        present=True,
        scope="same",
    )
    page = DerivedPage(id="p1", title="Page 1", content_html="<p>hi</p>", assets=[asset])
    wiki = DerivedWiki(
        id="w1", label="wiki0", title="Wiki 0", root_page_ids=["p1"], pages={"p1": page}
    )
    interchange = Interchange(wikis=[wiki])
    source = ModelSource()

    source.stash(interchange, archive_dir=archive.root)

    _data, content_type = source.get_blob(digest)
    assert content_type == "application/octet-stream"


def test_stashing_a_new_run_replaces_the_previous_model(tmp_path):
    archive, blob_hash, _digest = _archive_with_blob(tmp_path, b"pixel-bytes")
    first = _sample_interchange(blob_hash)
    second = _sample_interchange(blob_hash)
    second.run_id = "run-2"
    source = ModelSource()

    source.stash(first, archive_dir=archive.root)
    source.stash(second, archive_dir=archive.root)

    assert source.get_model() is second
    assert source.get_model().run_id == "run-2"


# --- absent / malformed blobs -------------------------------------------


def test_get_blob_for_absent_hash_returns_none(tmp_path):
    archive, blob_hash, _digest = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    source = ModelSource()
    source.stash(interchange, archive_dir=archive.root)

    assert source.get_blob("f" * 64) is None


def test_get_blob_rejects_non_hex_hash(tmp_path):
    archive, blob_hash, _digest = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    source = ModelSource()
    source.stash(interchange, archive_dir=archive.root)

    assert source.get_blob("not-hex!") is None
    assert source.get_blob("gg" * 32) is None


def test_get_blob_rejects_path_traversal_attempt(tmp_path):
    archive, blob_hash, _digest = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    source = ModelSource()
    source.stash(interchange, archive_dir=archive.root)

    # A secret file that a naive path join could otherwise reach.
    secret = archive.root.parent / "secret.txt"
    secret.write_text("do not serve me")

    assert source.get_blob("../secret.txt") is None
    assert source.get_blob("..%2Fsecret.txt") is None


# --- package mode ---------------------------------------------------------


def test_from_package_loads_the_written_model(tmp_path):
    archive, blob_hash, digest = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    source = ModelSource.from_package(dest)

    model = source.get_model()
    assert model is not None
    assert model.model_dump() == interchange.model_dump()


def test_from_package_serves_blobs_from_the_package(tmp_path):
    archive, blob_hash, digest = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    source = ModelSource.from_package(dest)

    data, content_type = source.get_blob(digest)
    assert data == b"pixel-bytes"
    assert content_type == "application/pdf"


# --- archive mode (derive once) -------------------------------------------


def test_from_archive_derives_the_model(tmp_path):
    # `derive` needs a real archive (a wikis-feed request to locate
    # the API root from) -- `run_demo` is the project's own way to get
    # one offline, without a real HCL deployment (gui/demo.py).
    from connections_export.gui.demo import run_demo

    result = run_demo(lambda _event: None, seed=1, archive_dir=tmp_path / "archive", delay=0)

    source = ModelSource.from_archive(result.archive_dir)

    assert source.get_model().model_dump() == result.interchange.model_dump()
