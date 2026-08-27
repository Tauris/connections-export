"""Content-addressed blob storage: `blobs/<sha256hex>`.

Bodies are stored exactly as received — no decoding, no re-encoding. The
filename is the SHA-256 hex digest of the bytes, so identical bodies
naturally dedupe and corruption is detectable by recomputing the hash.
"""

import hashlib
from pathlib import Path

BLOBS_DIRNAME = "blobs"


def blobs_dir(root: Path) -> Path:
    return root / BLOBS_DIRNAME


def blob_path(root: Path, digest: str) -> Path:
    return blobs_dir(root) / digest


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
