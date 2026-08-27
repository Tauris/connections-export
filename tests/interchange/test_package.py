"""Tasks 2 and 3: `write_package`/`load_package`/`open_blob`.

Pure over a hand-built `Interchange` + an `Archive` holding the blobs
it references -- no crawl/derive needed to prove the package-writing
contract itself.
`generated_at` is always caller-supplied here, never the wall clock.
"""

import json
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
from connections_export.interchange.package import load_package, open_blob, write_package

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPEC_DOC = REPO_ROOT / "docs" / "reference" / "interchange-format.md"

GENERATED_AT = "2026-07-20T12:00:00Z"


def _archive_with_blob(tmp_path: Path, data: bytes) -> tuple[Archive, str]:
    archive = Archive.open(tmp_path / "archive")
    digest = write_blob(archive.root, data)
    return archive, f"sha256:{digest}"


def _sample_interchange(blob_hash: str | None, *, present: bool = True) -> Interchange:
    asset = ResolvedAsset(
        original_href="img/diagram.png",
        resolved_url="https://fake/img/diagram.png",
        blob_hash=blob_hash,
        present=present,
        scope="same",
    )
    page = DerivedPage(
        id="p1",
        label="p1",
        title="Page 1",
        content_html="<p>hi</p>",
        assets=[asset],
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


# --- 2.1: write_package emits the full file set ----------------------------


def test_spec_doc_path_resolves_and_ships_in_the_wheel():
    # SPEC_DOC_PATH must resolve to a real file both in a dev checkout (repo
    # doc) and in an installed wheel (force-included copy) -- write_package and
    # the Manual's spec view both read it, and both broke when only the
    # unpackaged docs/ copy existed. Guard the resolution + the packaging rule.
    from connections_export.interchange import package as pkg

    assert pkg.SPEC_DOC_PATH.is_file(), "interchange-format spec not resolvable"
    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert "connections_export/interchange/interchange-format.md" in pyproject, (
        "the spec must be force-included into the wheel (docs/ isn't packaged)"
    )


def test_write_package_creates_the_documented_layout(tmp_path):
    archive, blob_hash = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    dest = tmp_path / "package"

    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    assert (dest / "interchange.json").is_file()
    assert (dest / "manifest.json").is_file()
    assert (dest / "provenance.json").is_file()
    assert (dest / "INTERCHANGE.md").is_file()
    filename = blob_hash.split(":", 1)[-1]
    assert (dest / "blobs" / filename).read_bytes() == b"pixel-bytes"


def test_write_package_interchange_json_is_the_full_model_dump(tmp_path):
    archive, blob_hash = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    dest = tmp_path / "package"

    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    dumped = json.loads((dest / "interchange.json").read_text(encoding="utf-8"))
    assert dumped == json.loads(interchange.model_dump_json())


def test_write_package_skips_copying_not_present_assets_but_keeps_them_declared(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    interchange = _sample_interchange(None, present=False)
    dest = tmp_path / "package"

    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    blobs_dir = dest / "blobs"
    assert not blobs_dir.exists() or list(blobs_dir.iterdir()) == []
    # ...but the not-present asset is still declared in the model.
    dumped = json.loads((dest / "interchange.json").read_text(encoding="utf-8"))
    asset = dumped["wikis"][0]["pages"]["p1"]["assets"][0]
    assert asset["present"] is False


def test_write_package_dedupes_a_blob_referenced_by_multiple_assets(tmp_path):
    archive, blob_hash = _archive_with_blob(tmp_path, b"shared-bytes")
    asset1 = ResolvedAsset(
        original_href="a.png",
        resolved_url="https://fake/a.png",
        blob_hash=blob_hash,
        present=True,
        scope="same",
    )
    asset2 = ResolvedAsset(
        original_href="b.png",
        resolved_url="https://fake/b.png",
        blob_hash=blob_hash,
        present=True,
        scope="same",
    )
    page = DerivedPage(id="p1", assets=[asset1, asset2])
    wiki = DerivedWiki(id="w1", label="wiki0", title="Wiki 0", pages={"p1": page})
    interchange = Interchange(wikis=[wiki])
    dest = tmp_path / "package"

    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    assert list((dest / "blobs").iterdir()) == [dest / "blobs" / blob_hash.split(":", 1)[-1]]


def test_write_package_copies_attachment_blobs_too(tmp_path):
    archive, blob_hash = _archive_with_blob(tmp_path, b"attachment-bytes")
    attachment = DerivedAttachment(
        id="a1",
        filename="doc.bin",
        content_type="application/octet-stream",
        asset=ResolvedAsset(
            original_href="doc.bin",
            resolved_url="https://fake/doc.bin",
            blob_hash=blob_hash,
            present=True,
            scope="same",
        ),
    )
    page = DerivedPage(id="p1", attachments=[attachment])
    wiki = DerivedWiki(id="w1", label="wiki0", title="Wiki 0", pages={"p1": page})
    interchange = Interchange(wikis=[wiki])
    dest = tmp_path / "package"

    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    filename = blob_hash.split(":", 1)[-1]
    assert (dest / "blobs" / filename).read_bytes() == b"attachment-bytes"


def test_write_package_manifest_json_matches_build_manifest(tmp_path):
    from connections_export.interchange.manifest import build_manifest

    archive, blob_hash = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    dest = tmp_path / "package"

    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    manifest_on_disk = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_on_disk == json.loads(build_manifest(interchange).model_dump_json())


# --- 2.2: provenance.json ---------------------------------------------


def test_provenance_json_carries_injected_generated_at_and_model_source_fields(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    interchange = Interchange(
        base_url="https://fake",
        source_version="8.0",
        run_id="run-1",
        hcl_hosts=["fake"],
        wikis=[],
    )
    dest = tmp_path / "package"

    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    provenance = json.loads((dest / "provenance.json").read_text(encoding="utf-8"))
    # Compared whole, so a field added here has to be added here too: this
    # file travels with the package and is read by strangers.
    generator = {
        key: provenance.pop(key)
        for key in ("generator", "generator_version", "generator_url")
        if key in provenance
    }
    assert provenance == {
        "base_url": "https://fake",
        "source_version": "8.0",
        "run_id": "run-1",
        "hcl_hosts": ["fake"],
        "generated_at": GENERATED_AT,
    }
    assert set(generator) == {"generator", "generator_version", "generator_url"}


def test_write_package_never_reads_the_wall_clock(tmp_path, monkeypatch):
    """`generated_at` is always the caller-injected string -- nothing
    in `write_package` may consult the real clock."""
    import datetime

    def _boom(*args, **kwargs):
        raise AssertionError("write_package must not read the wall clock")

    monkeypatch.setattr(datetime, "datetime", type("Blocked", (), {"now": _boom}))

    archive = Archive.open(tmp_path / "archive")
    interchange = Interchange(wikis=[])
    dest = tmp_path / "package"

    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    provenance = json.loads((dest / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["generated_at"] == GENERATED_AT


# --- 2.3: INTERCHANGE.md is a byte-identical copy --------------------------


def test_interchange_md_is_byte_identical_to_the_source_doc(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    interchange = Interchange(wikis=[])
    dest = tmp_path / "package"

    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    source_bytes = SPEC_DOC.read_bytes()
    bundled_bytes = (dest / "INTERCHANGE.md").read_bytes()
    assert bundled_bytes == source_bytes


# --- 3.1: load_package / open_blob -----------------------------------------


def test_load_package_round_trips_the_written_model(tmp_path):
    archive, blob_hash = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    loaded = load_package(dest)

    assert loaded == interchange


def test_load_package_accepts_a_str_path(tmp_path):
    archive, blob_hash = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    loaded = load_package(str(dest))

    assert loaded == interchange


def test_open_blob_reads_back_the_bytes(tmp_path):
    archive, blob_hash = _archive_with_blob(tmp_path, b"pixel-bytes")
    interchange = _sample_interchange(blob_hash)
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    assert open_blob(dest, blob_hash) == b"pixel-bytes"
