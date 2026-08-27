"""`ModelSource`: owns "what derived model does
this server expose" so `/api/model`/`/api/blob/{hash}` (app.py) have a
single place to ask.

Three ways a model gets in:

- **Demo mode**: `stash(interchange, archive_dir=...)` after a demo run
  completes -- the demo already calls `derive`, this just remembers the
  result (when a demo run completes, stash its derived
  Interchange).
- **Package mode**: `ModelSource.from_package(dir)` reads a package
  written by `interchange.write_package` (`interchange.json` +
  `blobs/`) via `interchange.load_package`.
- **Archive mode**: `ModelSource.from_archive(dir)` derives once, at
  construction, from a raw archive.

Blobs are served the same way regardless of source: both an archive
root and a written package directory lay blobs out identically --
`<root>/blobs/<sha256hex>` (`connections_export.archive.blobs`) -- so
`get_blob` only needs to remember which directory to look under.
`hash` is validated as a bare 64-char lowercase hex digest *before* it
ever reaches the filesystem, so a malformed or path-traversal hash
(``"../secret"``, non-hex) can never resolve outside the blobs
directory -- it is simply rejected (`None`), no different from an
absent blob.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Literal

from connections_export.archive.source import ArchiveSource, DirectorySource, ReadableArchive
from connections_export.archive.store import MANIFEST_FILENAME, Archive
from connections_export.derive import DeriveError, Interchange, derive
from connections_export.derive.author_filter import filter_by_author
from connections_export.interchange import load_package

ModelState = Literal["pending", "partial", "complete"]

#: A blob's on-disk name is the bare hex SHA-256 digest of its bytes
#: (`connections_export.archive.blobs.write_blob`) -- 64 lowercase hex chars,
#: never anything a path-join could turn into traversal.
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_DEFAULT_CONTENT_TYPE = "application/octet-stream"


def _sniff_content_type(data: bytes, fallback: str) -> str:
    """Best-effort image content-type from magic bytes, for a blob that
    carries no declared type (an inline **body** asset -- `ResolvedAsset`
    holds no content-type, unlike an attachment). Lets the reader render a
    body image (e.g. a page's diagram) instead of downloading octet-stream."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    head = data[:64].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:512].lower()):
        return "image/svg+xml"
    return fallback


def _digest_of(blob_hash: str) -> str:
    """`ResolvedAsset.blob_hash`/`DerivedAttachment.asset.blob_hash`
    carry the archive's `"sha256:<hex>"` form; blobs are stored on disk
    under the bare hex digest."""
    return blob_hash.split(":", 1)[-1]


def _content_types(model: Interchange) -> dict[str, str]:
    """Best-effort hash -> content-type map, derived from the model
    itself: only `DerivedAttachment.content_type` carries this
    (Content-type from the manifest/attachment where
    known, else application/octet-stream). Inline body assets
    (`ResolvedAsset` on its own) carry no content-type info."""
    mapping: dict[str, str] = {}
    for wiki in model.wikis:
        for page in wiki.pages.values():
            for attachment in page.attachments:
                asset = attachment.asset
                if asset.present and asset.blob_hash and attachment.content_type:
                    mapping[_digest_of(asset.blob_hash)] = attachment.content_type
    return mapping


class ModelSource:
    """Thread-safe holder for the one derived model this server
    exposes (the app holds the derived Interchange).
    Safe to construct empty -- `get_model` is `None` until a demo run
    stashes one, or the app was pointed at a package/archive."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: Interchange | None = None
        self._blob_root: Path | None = None
        #: Where blobs come from. A directory for a package, a stashed run or
        #: a live archive; a zip when the reader was pointed at one. Set
        #: wherever `_blob_root` is, and the only thing `get_blob` consults --
        #: `_blob_root` remains for `package_root`, which answers "what
        #: lives on disk" and is honestly `None` for a zip.
        self._blob_source: ArchiveSource | None = None
        self._content_types: dict[str, str] = {}
        #: A completed model is present (stash / package / archive mode).
        #: Distinguishes a final "complete" model from a live "partial"
        #: snapshot, which are both `Interchange` instances.
        self._stashed = False
        #: Live mode: the in-progress run's archive dir.
        #: `get_model` derives it on demand while `_stashed` is False.
        self._live_dir: Path | None = None
        self._live_model: Interchange | None = None
        #: Cache key for the live derive -- the manifest's byte size, which
        #: only grows (append-only). `-1` means "not derived yet".
        self._live_key: int = -1
        #: When set, every model this source exposes (live snapshot, stashed
        #: run, loaded package/archive) is narrowed to this user's involvement
        #: via `filter_by_author` -- the "capture everything I authored" sweep.
        #: The on-disk archive stays complete; only the served model is filtered.
        self._author_filter: str | None = None

    def _filtered(self, model: Interchange) -> Interchange:
        if self._author_filter:
            return filter_by_author(model, author=self._author_filter)
        return model

    def attach_live(self, archive_dir: Path | str, *, author: str | None = None) -> None:
        """Point the source at an **in-progress** run's archive. From now until `stash`,
        `get_model`
        derives that archive on demand into a growing snapshot, and
        blobs resolve from it immediately -- so the reader can show a
        page's real content the moment it is imported. A later
        `attach_live`/`stash` replaces this (most-recent-run
        semantics)."""
        with self._lock:
            self._live_dir = Path(archive_dir)
            self._blob_root = Path(archive_dir)
            self._blob_source = DirectorySource(archive_dir)
            self._live_model = None
            self._live_key = -1
            self._stashed = False
            self._model = None
            self._content_types = {}
            self._author_filter = author

    def stash(
        self,
        interchange: Interchange,
        *,
        archive_dir: Path | str,
        author: str | None = None,
    ) -> None:
        """Record a freshly completed run's derived model + where its
        blobs live. A later call (a subsequent demo run) replaces the
        previous model -- "the most recent completed demo run's
        derived model". Supersedes any live
        snapshot: the run is done, this is final."""
        with self._lock:
            self._author_filter = author
            model = self._filtered(interchange)
            self._model = model
            self._blob_root = Path(archive_dir)
            self._blob_source = DirectorySource(archive_dir)
            self._content_types = _content_types(model)
            self._stashed = True
            self._live_dir = None
            self._live_model = None

    def _manifest_size(self, archive_dir: Path) -> int:
        try:
            return (archive_dir / MANIFEST_FILENAME).stat().st_size
        except OSError:
            return -1

    def _live_snapshot_locked(self) -> Interchange | None:
        """Derive the live archive on demand, cached on the manifest's
        byte size so repeated reads don't re-derive until the archive
        has grown. `DeriveError` (raised until the first feed is
        archived) is "not ready yet" -> `None`. Caller holds `_lock`."""
        assert self._live_dir is not None
        size = self._manifest_size(self._live_dir)
        if size == self._live_key:
            return self._live_model
        self._live_key = size
        try:
            model = derive(Archive.open(self._live_dir))
        except DeriveError:
            self._live_model = None
            return None
        model = self._filtered(model)
        self._live_model = model
        self._content_types = _content_types(model)
        return model

    def get_model(self) -> Interchange | None:
        with self._lock:
            if self._stashed:
                return self._model
            if self._live_dir is not None:
                return self._live_snapshot_locked()
            return self._model

    def model_state(self) -> ModelState:
        """`"complete"` once a finished model is stashed (or loaded from
        a package/archive), `"partial"` while a live run derives into a
        growing snapshot, `"pending"` before anything is derivable. The `/api/model`
        `X-HCL-Model-State` header."""
        with self._lock:
            if self._stashed and self._model is not None:
                return "complete"
            if self._live_dir is not None:
                return "partial" if self._live_snapshot_locked() is not None else "pending"
            return "complete" if self._model is not None else "pending"

    def get_blob(self, blob_hash: str) -> tuple[bytes, str] | None:
        """`(bytes, content_type)` for a present, validly-shaped blob
        hash; `None` for anything else -- malformed/traversal hashes
        and genuinely absent blobs are indistinguishable to a caller,
        both "not found" (an absent or malformed hash is
        rejected without serving arbitrary files)."""
        if not _HEX64.fullmatch(blob_hash):
            return None
        with self._lock:
            source = self._blob_source
            content_type = self._content_types.get(blob_hash, _DEFAULT_CONTENT_TYPE)
        if source is None:
            return None
        data = source.read_blob(blob_hash)
        if data is None:
            return None
        if content_type == _DEFAULT_CONTENT_TYPE:
            content_type = _sniff_content_type(data, content_type)
        return data, content_type

    def package_root(self) -> Path | None:
        """The directory backing the current model, if any: a written
        package directory (`from_package`), a raw archive directory
        (`from_archive`/a stashed run), or `None` before anything is
        loaded. Used by the Manual's interchange-spec endpoint to
        prefer whatever package is currently open's own bundled
        `INTERCHANGE.md` copy (every package written by
        `interchange.write_package` ships one) over the repo doc."""
        with self._lock:
            return self._blob_root

    @classmethod
    def from_package(cls, package_dir: Path | str, *, author: str | None = None) -> ModelSource:
        """Load a package written by `interchange.write_package` --
        `interchange.json` for the model, `blobs/` for its assets.
        `author` narrows the served model to that user's involvement."""
        package_dir = Path(package_dir)
        source = cls()
        source._author_filter = author
        model = source._filtered(load_package(package_dir))
        with source._lock:
            source._model = model
            source._blob_root = package_dir
            source._blob_source = DirectorySource(package_dir)
            source._content_types = _content_types(model)
        return source

    @classmethod
    def from_archive(cls, archive_dir: Path | str, *, author: str | None = None) -> ModelSource:
        """Derive once, at construction, from a raw archive -- a directory or
        a `.zip` (Archive/package mode... derives once at
        startup). `author` narrows the served model to that user's
        involvement.

        Read-only, so this goes through `ReadableArchive` rather than
        `Archive.open`, which would create the directory it was handed. A
        mistyped path raises `ArchiveSourceError` instead of leaving an empty
        archive behind.
        """
        archive = ReadableArchive.open(archive_dir)
        source = cls()
        source._author_filter = author
        model = source._filtered(derive(archive))
        with source._lock:
            source._model = model
            source._blob_root = archive.root
            source._blob_source = archive.source
            source._content_types = _content_types(model)
        return source
