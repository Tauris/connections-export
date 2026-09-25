"""Reading an archive, without assuming it is a directory.

An archive is `manifest.jsonl`, `feeds.jsonl`, `run-*.json` and
`blobs/<digest>`. Writing one means writing files; *reading* one means four
operations, and nothing about them requires a filesystem. This module is that
seam, so the same archive can be read from a directory or straight out of a
zip.

Deliberately read-only. Crawling into a zip is not supported, and is refused
where a run is configured rather than discovered halfway through one.
"""

from __future__ import annotations

import json
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from connections_export.archive.blobs import BLOBS_DIRNAME, valid_digest
from connections_export.archive.store import MANIFEST_FILENAME

#: `run-*.json`, the shape `derive/apps/_shared.py` documents.
RUN_METADATA_PREFIX = "run-"
RUN_METADATA_SUFFIX = ".json"

#: The most a zip member is decompressed to. A zip may come from anyone, and a
#: few kilobytes of deflated zeros expand to gigabytes that `ZipFile.read`
#: would hold in memory before anything looked at them. The manifest and
#: sidecars are text and grow with item count; half a gigabyte is far past
#: any capture this tool has made. A blob is one response body -- a community
#: file can be large, so its cap is higher, but still below what would take a
#: desktop down. Over the cap is a corrupt member, not a crash.
MAX_RECORDS_MEMBER_BYTES = 512 * 1024 * 1024
MAX_BLOB_MEMBER_BYTES = 1024 * 1024 * 1024

_CHUNK = 1024 * 1024


class _OverLimit(Exception):
    """A zip member larger than its cap."""


class ArchiveSourceError(Exception):
    """This is not an archive, or not one that can be read."""


@dataclass(frozen=True)
class RunEntry:
    """One `run-*.json`, already read.

    Ordering is the source's job, not the caller's: a directory has mtimes and
    a zip entry has a two-second-granularity timestamp, and no caller should
    have to know which it is looking at.
    """

    name: str
    data: bytes


@runtime_checkable
class ArchiveSource(Protocol):
    #: Whether an archive backed by this source can be written into. Only a
    #: directory can. A zip is refused in words rather than half-supported:
    #: the failure worth guarding is not a traceback but a silent no-op that
    #: leaves someone believing their update landed somewhere.
    writable: bool

    def read_lines(self, name: str) -> Iterator[str]: ...

    def read_blob(self, digest: str) -> bytes | None: ...

    def run_metadata(self) -> list[RunEntry]: ...

    def read_json(self, name: str) -> dict | None: ...

    @property
    def directory(self) -> Path | None: ...

    @property
    def label(self) -> str: ...

    def check(self) -> None: ...


class DirectorySource:
    """An archive as it has always been stored: a directory."""

    writable = True

    def __init__(self, root: Path | str):
        self._root = Path(root)

    @property
    def directory(self) -> Path | None:
        return self._root

    @property
    def label(self) -> str:
        return str(self._root)

    def check(self) -> None:
        if not (self._root / MANIFEST_FILENAME).is_file():
            raise ArchiveSourceError(
                f"{self._root} does not look like an archive: no {MANIFEST_FILENAME}"
            )

    def read_lines(self, name: str) -> Iterator[str]:
        path = self._root / name
        if not path.is_file():
            return
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                yield line.rstrip("\n")

    def read_blob(self, digest: str) -> bytes | None:
        bare = valid_digest(digest)
        if bare is None:
            # A manifest line is the archive author's text; a "digest" of
            # `../../.ssh/id_rsa` is not a blob this archive holds.
            return None
        path = self._root / BLOBS_DIRNAME / bare
        if not path.is_file():
            return None
        return path.read_bytes()

    def read_json(self, name: str) -> dict | None:
        path = self._root / name
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def run_metadata(self) -> list[RunEntry]:
        paths = [
            path
            for path in self._root.glob(f"{RUN_METADATA_PREFIX}*{RUN_METADATA_SUFFIX}")
            if path.is_file()
        ]
        paths.sort(key=lambda path: (path.stat().st_mtime, path.name))
        return [RunEntry(name=path.name, data=path.read_bytes()) for path in paths]


class ZipSource:
    """An archive read out of a `.zip`, without unpacking it.

    Every read is a seek into the central directory, which is what the access
    pattern already is: two metadata streams at open, then blobs by digest.
    Extraction would pay full disk and a full wait on every open to buy
    nothing the zip's own random access does not already give.

    `zipfile.ZipFile` is not thread-safe and the GUI serves blobs
    concurrently, so each thread gets its own handle. The file is immutable
    for this object's lifetime, so the handles are interchangeable. A single
    shared lock was rejected: it would serialise blob serving and stall the
    reader exactly when a page needs the most images.
    """

    writable = False

    def __init__(self, zip_path: Path | str):
        self._path = Path(zip_path)
        self._local = threading.local()
        self._prefix: str | None = None

    @property
    def directory(self) -> Path | None:
        return None

    @property
    def label(self) -> str:
        return str(self._path)

    def _handle(self) -> zipfile.ZipFile:
        handle = getattr(self._local, "handle", None)
        if handle is None:
            try:
                handle = zipfile.ZipFile(self._path)
            except (OSError, zipfile.BadZipFile) as exc:
                raise ArchiveSourceError(
                    f"{self._path} could not be opened as a zip: {exc}"
                ) from exc
            self._local.handle = handle
        return handle

    def _resolve_prefix(self) -> str:
        """Where the archive sits inside the zip: at the root, or under
        exactly one directory. Two candidates is an error, never a guess."""
        if self._prefix is not None:
            return self._prefix
        names = self._handle().namelist()
        candidates = sorted(
            name[: -len(MANIFEST_FILENAME)]
            for name in names
            if name == MANIFEST_FILENAME or name.endswith("/" + MANIFEST_FILENAME)
        )
        if not candidates:
            raise ArchiveSourceError(
                f"{self._path} does not contain an archive: no {MANIFEST_FILENAME} inside it"
            )
        if len(candidates) > 1:
            found = ", ".join(candidate or "<root>" for candidate in candidates)
            raise ArchiveSourceError(
                f"{self._path} contains more than one archive ({found}); "
                "unpack the one you want and open that"
            )
        self._prefix = candidates[0]
        return self._prefix

    def check(self) -> None:
        self._resolve_prefix()

    def _read_member(self, info: zipfile.ZipInfo, limit: int) -> bytes:
        """`info`'s bytes, refusing past `limit`.

        Checked twice: `file_size` is what the zip's own directory claims, and
        whoever built the zip wrote that too, so the decompressed stream is
        counted as it is read as well. A member whose bytes do not match its
        directory entry (`BadZipFile`, a CRC failure) is as corrupt as one
        that is too large.
        """
        if info.file_size > limit:
            raise _OverLimit(info.filename)
        chunks: list[bytes] = []
        total = 0
        try:
            with self._handle().open(info) as member:
                while chunk := member.read(_CHUNK):
                    total += len(chunk)
                    if total > limit:
                        raise _OverLimit(info.filename)
                    chunks.append(chunk)
        except zipfile.BadZipFile as exc:
            raise _OverLimit(info.filename) from exc
        return b"".join(chunks)

    def _read(self, name: str, limit: int | None = None) -> bytes | None:
        """`name`'s bytes; `None` if absent, `_OverLimit` if corrupt. The
        records cap unless told otherwise."""
        try:
            info = self._handle().getinfo(self._resolve_prefix() + name)
        except KeyError:
            return None
        return self._read_member(info, MAX_RECORDS_MEMBER_BYTES if limit is None else limit)

    def read_lines(self, name: str) -> Iterator[str]:
        try:
            payload = self._read(name)
        except _OverLimit as exc:
            # A stream cannot be half-read: derive would build from a
            # truncated manifest and call it the archive. Refused in words.
            raise ArchiveSourceError(
                f"{name} in {self._path} is corrupt or larger than "
                f"{MAX_RECORDS_MEMBER_BYTES // (1024 * 1024)} MB; refusing to read it"
            ) from exc
        if payload is None:
            return
        # `splitlines` rather than a split on "\n": a file ending in a
        # newline must yield the same list a directory yields, or derive
        # counts one blank record more from a zip than from a directory.
        yield from payload.decode("utf-8").splitlines()

    def read_blob(self, digest: str) -> bytes | None:
        bare = valid_digest(digest)
        if bare is None:
            return None
        try:
            return self._read(f"{BLOBS_DIRNAME}/{bare}", MAX_BLOB_MEMBER_BYTES)
        except _OverLimit:
            return None

    def read_json(self, name: str) -> dict | None:
        try:
            payload = self._read(name)
        except _OverLimit:
            return None
        if payload is None:
            return None
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def run_metadata(self) -> list[RunEntry]:
        prefix = self._resolve_prefix()
        infos = [
            info
            for info in self._handle().infolist()
            if info.filename.startswith(prefix + RUN_METADATA_PREFIX)
            and info.filename.endswith(RUN_METADATA_SUFFIX)
            and "/" not in info.filename[len(prefix) :]
        ]
        infos.sort(key=lambda info: (info.date_time, info.filename))
        entries = []
        for info in infos:
            try:
                data = self._read_member(info, MAX_RECORDS_MEMBER_BYTES)
            except _OverLimit:
                continue  # a corrupt run record is skipped like an unparseable one
            entries.append(RunEntry(name=info.filename[len(prefix) :], data=data))
        return entries


def source_for(path: Path | str) -> ArchiveSource:
    """A source for `path` -- a directory or a `.zip` -- WITHOUT checking that
    it really holds an archive.

    For reads that are meaningful on their own: the summary sidecar an archive
    list shows, and the item count beside it. A run that died before writing
    a manifest still has a name and a summary worth showing, and demanding a
    valid archive to read them would hide exactly the runs a person most needs
    to see.
    """
    path = Path(path)
    if path.is_dir():
        return DirectorySource(path)
    if path.is_file() and path.suffix.lower() == ".zip":
        return ZipSource(path)
    if path.exists():
        raise ArchiveSourceError(f"{path} is neither an archive directory nor a .zip")
    raise ArchiveSourceError(f"{path} does not exist")


def open_source(path: Path | str) -> ArchiveSource:
    """A source for `path`, checked: it holds an archive, or this raises.

    Never creates anything. `Archive.open` deliberately does
    (`root.mkdir(parents=True, exist_ok=True)`), which is right for a crawl
    target and wrong for a read: a mistyped path should say so, not leave an
    empty archive behind for someone to find later and wonder about.
    """
    source = source_for(path)
    source.check()
    return source


class ReadableArchive:
    """An archive opened for reading only.

    `Archive` creates directories as it opens them, because it exists to be
    written into. This is the read side: a source, and nothing else. Anything
    that only reads takes one of these and never learns whether it is holding
    a directory or a zip.
    """

    def __init__(self, source: ArchiveSource):
        self.source = source

    @classmethod
    def open(cls, path: Path | str) -> ReadableArchive:
        return cls(open_source(path))

    @property
    def root(self) -> Path | None:
        """The directory backing this archive, or `None` for a zip.

        `package_root` already answers `None` for "nothing that lives on
        disk", so a zip needs no new state downstream.
        """
        return self.source.directory
