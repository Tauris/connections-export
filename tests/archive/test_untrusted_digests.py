"""A blob digest is data from someone else's archive, never a path.

Archives are handed from person to person, so a manifest's `body_hash` and a
package's `blob_hash` are whatever the author of that file wrote. Every
place that turns one into a filename has to refuse anything that is not a
bare SHA-256 hex digest -- otherwise `sha256:../../.ssh/id_rsa` reads a file
off the machine opening the archive and carries it into pages, PDFs and
exports.
"""

from __future__ import annotations

import zipfile

import pytest

from connections_export.archive import blobs
from connections_export.archive.source import DirectorySource, ZipSource

GOOD = "ab" * 32
TRAVERSALS = [
    "../../secret",
    "sha256:../../secret",
    "/etc/passwd",
    "..",
    "",
    GOOD.upper(),
    GOOD + "\n",
    GOOD[:-1],
    "md5:" + GOOD,
]


def test_a_digest_is_accepted_bare_or_with_its_sha256_prefix():
    """The model carries `sha256:<hex>`, the blob store the bare hex; both
    name the same blob."""
    assert blobs.valid_digest(GOOD) == GOOD
    assert blobs.valid_digest("sha256:" + GOOD) == GOOD


@pytest.mark.parametrize("value", [*TRAVERSALS, None])
def test_anything_that_is_not_a_sha256_hex_digest_is_refused(value):
    assert blobs.valid_digest(value) is None


@pytest.mark.parametrize("value", TRAVERSALS)
def test_blob_path_refuses_a_digest_that_is_not_one(tmp_path, value):
    """The archive's own reader is the last line: a caller that forgot to
    validate still cannot be walked out of `blobs/`."""
    with pytest.raises(ValueError):
        blobs.blob_path(tmp_path, value)


def _directory_with_secret(tmp_path):
    root = tmp_path / "archive"
    (root / "blobs").mkdir(parents=True)
    (tmp_path / "secret").write_bytes(b"private key")
    return root


@pytest.mark.parametrize("value", ["../../secret", "sha256:../../secret"])
def test_a_directory_source_reads_nothing_outside_its_blobs(tmp_path, value):
    """Treated as absent, not raised: one poisoned record must not stop an
    otherwise good archive from opening."""
    source = DirectorySource(_directory_with_secret(tmp_path))

    assert source.read_blob(value) is None


def test_a_directory_source_still_reads_a_prefixed_digest(tmp_path):
    root = _directory_with_secret(tmp_path)
    (root / "blobs" / GOOD).write_bytes(b"body")

    assert DirectorySource(root).read_blob("sha256:" + GOOD) == b"body"


def test_a_zip_source_refuses_a_digest_that_is_not_one_but_reads_real_ones(tmp_path):
    """A zip cannot be walked out of, but the same rule everywhere means a
    digest means the same thing whichever form the archive arrived in."""
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.jsonl", "")
        archive.writestr(f"blobs/{GOOD}", b"body")
        archive.writestr("blobs/../secret", b"private key")
    source = ZipSource(path)

    assert source.read_blob(GOOD) == b"body"
    assert source.read_blob("../secret") is None
