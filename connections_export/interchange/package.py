"""`write_package`/`load_package`/`open_blob` (the design, "write_package
(interchange, archive, dest, *, generated_at)" / "load_package(dest) ->
Interchange"): serialize a derived `Interchange` to a portable,
self-documenting package directory, and read one back.

Nothing here reads the wall clock -- `generated_at` is always a
caller-supplied string, so packages (and the tests around them) are
deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

from connections_export.archive.blobs import read_blob, valid_digest
from connections_export.archive.store import Archive
from connections_export.derive.model import Interchange
from connections_export.derive.traverse import iter_all_assets
from connections_export.interchange.filenames import plan, sanitize
from connections_export.interchange.manifest import build_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
# The interchange-format spec must be readable both from a dev checkout AND
# from a pip/uv-installed wheel. In the wheel it is force-included next to this
# module (see pyproject `[tool.hatch.build.targets.wheel.force-include]`), where
# `docs/` does not exist; from source the repo copy under `docs/reference/` is
# the canonical one. Prefer whichever is present -- without this, the Manual's
# spec view AND `write_package` (which copies the spec into every package as
# INTERCHANGE.md) both break once installed.
_PACKAGED_SPEC = Path(__file__).with_name("interchange-format.md")
_REPO_SPEC = REPO_ROOT / "docs" / "reference" / "interchange-format.md"
SPEC_DOC_PATH = _PACKAGED_SPEC if _PACKAGED_SPEC.is_file() else _REPO_SPEC

INTERCHANGE_FILENAME = "interchange.json"
MANIFEST_FILENAME = "manifest.json"
PROVENANCE_FILENAME = "provenance.json"
SPEC_COPY_FILENAME = "INTERCHANGE.md"
BLOBS_DIRNAME = "blobs"


def _blob_filename(blob_hash: str) -> str | None:
    """`ResolvedAsset.blob_hash` carries the archive's own
    `"sha256:<hex>"` form; the package stores blobs under the bare hex
    digest, mirroring the archive's own `blobs/<sha256hex>` layout.

    `None` for a hash that is not a digest. A model is loaded from someone
    else's `interchange.json` as readily as derived here, and a hash of
    `sha256:../../x` must never become a path to read or write."""
    return valid_digest(blob_hash)


def _copy_blobs(interchange: Interchange, archive: Archive, dest: Path) -> None:
    """Copy every blob the model references into `blobs/`.

    The same walk the manifest counts with (`derive.traverse.iter_all_assets`),
    so `manifest.blobs` and the contents of this directory cannot disagree.
    They did: this side covered wikis, blogs and forum *bodies*, so
    rich-content images and a community's file bytes were declared in
    `manifest.json` and never written, and forum attachments were missed by
    both walks at once.

    `present=False` refs are skipped -- nothing to copy, and they stay declared
    in the model as not-present.
    """
    hashes = {
        asset.blob_hash
        for asset in iter_all_assets(interchange)
        if asset.present and asset.blob_hash
    }
    if not hashes:
        return
    blobs_dir = dest / BLOBS_DIRNAME
    blobs_dir.mkdir(parents=True, exist_ok=True)
    for blob_hash in sorted(hashes):
        filename = _blob_filename(blob_hash)
        if filename is None:
            continue  # not a digest: nothing the archive can hold, nowhere to write it
        path = blobs_dir / filename
        if path.exists():  # dedup: a hash written once
            continue
        try:
            data = read_blob(archive.root, filename)
        except (OSError, ValueError):
            # A hash the archive cannot produce is a gap in the ARCHIVE, which
            # the manifest already records as not-present. Skipping keeps
            # packaging total rather than aborting a whole export over one
            # missing body.
            continue
        path.write_bytes(data)


#: Named copies of a community's files, alongside the content-addressed blobs.
FILES_DIRNAME = "files"
#: Where the mapping from written name back to the original lives.
FILES_MANIFEST_FILENAME = "files.json"


def _write_files(interchange: Interchange, archive: Archive, dest: Path) -> dict | None:
    """Write each captured file under its own name, in its folder.

    Blobs stay: they are the integrity anchor and the deduplicated bytes. But a
    library of 65 documents as 65 hex digests is not an archive anyone can use,
    so the package also carries `files/<folder>/<name>` -- the copy a person
    opens.

    Everything a filesystem could not represent is recorded rather than lost.
    `plan` supplies the safe name and the reason it differs; this adds where
    the bytes went, which blob they are, and every folder the file belongs to
    (the tree can hold it in one place; a file can be in several).
    """
    libraries = list(interchange.file_libraries)
    if not libraries:
        return None

    records: list[dict] = []
    for library in libraries:
        entries = [
            (file_id, library.files[file_id].name or file_id)
            for file_id in library.file_ids
            if file_id in library.files
        ]
        # Disambiguated per LIBRARY, not per directory -- so two files sharing
        # a name are both suffixed even when they land in different folders.
        # Deliberate: folder membership is an association that can change, and a
        # file can belong to several, so a per-directory scheme would make a
        # file's name depend on which folder we happened to file it under. The
        # cost is a suffix nobody strictly needed; the alternative is names that
        # move when a folder does.
        planned = {p.file_id: p for p in plan(entries)}
        folder_names = {folder.id: folder.name or folder.id for folder in library.folders}

        for file_id, _ in entries:
            derived = library.files[file_id]
            name = planned[file_id]
            # One place on disk, every membership in the manifest. Sorted so a
            # file in several folders lands somewhere stable between runs.
            folders = sorted(folder_names.get(fid, fid) for fid in derived.folder_ids)
            relative = Path(FILES_DIRNAME)
            if folders:
                relative = relative / sanitize(folders[0])
            relative = relative / name.name

            record = {
                "file_id": file_id,
                "library_id": library.id,
                "original": name.original,
                "name": name.name,
                "reasons": list(name.reasons),
                "path": relative.as_posix(),
                "folders": folders,
                "version_label": derived.version_label,
                "size": derived.size,
                "content_type": derived.content_type,
                "blob": None,
                "written": False,
            }

            asset = derived.asset
            digest = _blob_filename(asset.blob_hash) if asset is not None else None
            if asset is not None and asset.present and digest is not None:
                record["blob"] = asset.blob_hash
                try:
                    data = read_blob(archive.root, digest)
                except (OSError, ValueError):
                    data = None
                if data is not None:
                    target = dest / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    record["written"] = True
            records.append(record)

    return {
        "files": records,
        # Said plainly, because a directory listing cannot say it: what is in
        # `files/` is a convenience copy, and anything the filesystem could not
        # carry is recoverable from here.
        "note": (
            "`files/` is the readable copy; `blobs/` holds the bytes once, addressed "
            "by hash. Where `reasons` is empty the written name is exactly what "
            "Connections holds. Where it is not, `original` is, and a target system "
            "able to represent it should restore it rather than inherit this "
            "compromise."
        ),
    }


def _write_provenance(interchange: Interchange, dest: Path, *, generated_at: str) -> None:
    from connections_export.interchange.manifest import GENERATOR  # noqa: PLC0415
    from connections_export.sbom import OWN_PACKAGE_URL, own_version  # noqa: PLC0415

    provenance = {
        "base_url": interchange.base_url,
        "source_version": interchange.source_version,
        "run_id": interchange.run_id,
        "hcl_hosts": interchange.hcl_hosts,
        "generated_at": generated_at,
        # Which tool, which release, and where to get it. This is the file
        # about where the package came from, and the program that wrote it is
        # part of that answer.
        "generator": GENERATOR,
        "generator_version": own_version(),
        "generator_url": OWN_PACKAGE_URL,
    }
    (dest / PROVENANCE_FILENAME).write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def write_package(
    interchange: Interchange, archive: Archive, dest: Path | str, *, generated_at: str
) -> None:
    """Write `interchange` (+ every present blob it references, read
    from `archive`) to `dest` as a portable package.

    `generated_at` is a caller-supplied string -- never a wall-clock
    read -- so the package (and any test around it) is deterministic.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    (dest / INTERCHANGE_FILENAME).write_text(
        interchange.model_dump_json(indent=2), encoding="utf-8"
    )

    _copy_blobs(interchange, archive, dest)

    files_manifest = _write_files(interchange, archive, dest)
    if files_manifest is not None:
        (dest / FILES_MANIFEST_FILENAME).write_text(
            json.dumps(files_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    manifest = build_manifest(interchange)
    (dest / MANIFEST_FILENAME).write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    _write_provenance(interchange, dest, generated_at=generated_at)

    (dest / SPEC_COPY_FILENAME).write_bytes(SPEC_DOC_PATH.read_bytes())


def load_package(dest: Path | str) -> Interchange:
    """Reload the `Interchange` written by `write_package(dest,...)`
    -- round-trip identity with the object that was written."""
    dest = Path(dest)
    return Interchange.model_validate_json(
        (dest / INTERCHANGE_FILENAME).read_text(encoding="utf-8")
    )


def open_blob(dest: Path | str, blob_hash: str) -> bytes:
    """Read a blob's bytes out of a written package by the same
    `ResolvedAsset.blob_hash` value the model carries (`"sha256:<hex>"`
    or the bare hex digest -- either form resolves).

    A hash that is not a digest raises `FileNotFoundError`, exactly as an
    absent blob does: the package is someone else's, and its hashes are
    names inside `blobs/`, never paths out of it."""
    dest = Path(dest)
    filename = _blob_filename(blob_hash)
    if filename is None:
        raise FileNotFoundError(f"not a blob digest: {blob_hash!r}")
    return (dest / BLOBS_DIRNAME / filename).read_bytes()
