"""Content-addressed blob storage: `blobs/<sha256hex>`.

Bodies are stored exactly as received — no decoding, no re-encoding. The
filename is the SHA-256 hex digest of the bytes, so identical bodies
naturally dedupe and corruption is detectable by recomputing the hash.
"""

import hashlib
import re
from pathlib import Path

BLOBS_DIRNAME = "blobs"

#: The prefix the manifest and the model carry in front of a digest.
DIGEST_PREFIX = "sha256:"

#: A blob's name is exactly what `write_blob` produces: 64 lowercase hex
#: characters. Nothing else may become a path -- a digest read back from a
#: manifest or a package is whatever whoever wrote that file put there, and
#: archives are handed from person to person.
_HEX64 = re.compile(r"[0-9a-f]{64}")


def valid_digest(value: str | None) -> str | None:
    """The bare hex digest `value` names, or `None` if it names none.

    Accepts the bare digest and the `sha256:<hex>` form the manifest and the
    model use. Everything else -- `../../.ssh/id_rsa`, an absolute path, an
    upper-case or truncated digest, another algorithm -- is `None`, which every
    caller treats as "no such blob". One rule for every reader, so a digest
    cannot be safe in one place and a path in another.
    """
    if not value:
        return None
    if value.startswith(DIGEST_PREFIX):
        value = value[len(DIGEST_PREFIX) :]
    return value if _HEX64.fullmatch(value) else None


def blobs_dir(root: Path) -> Path:
    return root / BLOBS_DIRNAME


def blob_path(root: Path, digest: str) -> Path:
    """Where `digest` lives under `root`. Raises `ValueError` for anything that
    is not a digest, so a caller that forgot `valid_digest` still cannot be
    walked out of `blobs/`."""
    bare = valid_digest(digest)
    if bare is None:
        raise ValueError(f"not a blob digest: {digest!r}")
    return blobs_dir(root) / bare


def write_blob(root: Path, data: bytes) -> str:
    """Write `data` under its content address, returning the hex digest.

    A no-op if a blob with the same digest already exists (dedup).
    """
    digest = hashlib.sha256(data).hexdigest()
    directory = blobs_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / digest
    if not path.exists():
        # Write to a temp file then rename, so a crash mid-write can never
        # leave a partial blob visible under its final content-addressed name.
        tmp_path = path.with_name(f"{digest}.tmp-{id(data)}")
        tmp_path.write_bytes(data)
        tmp_path.replace(path)
    return digest


def read_blob(root: Path, digest: str) -> bytes:
    """Read back the bytes previously stored under `digest`."""
    return blob_path(root, digest).read_bytes()
