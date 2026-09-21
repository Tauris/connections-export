"""`get_blob` accepts the model's own hash form, not only a bare digest.

Every asset in the derived model carries `blob_hash = "sha256:<hex>"`
(`archive/store.py` writes it, `derive` keeps it). The ingesters hand that
verbatim to `ModelSource.get_blob`, which required a bare 64-hex string --
so `ingest --archive` read every image as absent and wrote none, while the
Reader (its JS strips the prefix) and `ingest --package` (via `open_blob`,
which strips it) both worked. No test or demo ever exercised a present image
blob through an exporter, which is how it shipped.
"""

from __future__ import annotations

import hashlib

from connections_export.gui.model_source import ModelSource

_DATA = b"\x89PNG\r\n\x1a\n" + b"pretend image bytes" * 4
_DIGEST = hashlib.sha256(_DATA).hexdigest()


class _BlobSource:
    def read_blob(self, digest: str) -> bytes | None:
        return _DATA if digest == _DIGEST else None


def _source() -> ModelSource:
    src = ModelSource()
    src._blob_source = _BlobSource()
    src._content_types = {_DIGEST: "image/png"}
    return src


def test_the_models_sha256_prefixed_hash_resolves():
    """The form every ResolvedAsset carries -- and the one that regressed."""
    result = _source().get_blob(f"sha256:{_DIGEST}")
    assert result is not None, "the model's own hash form read as absent"
    assert result[0] == _DATA
    assert result[1] == "image/png"


def test_a_bare_hex_digest_still_resolves():
    """What `/api/blob/` sends after the JS strips the prefix -- unchanged."""
    result = _source().get_blob(_DIGEST)
    assert result is not None and result[0] == _DATA


def test_a_malformed_hash_is_still_rejected():
    assert _source().get_blob("sha256:" + "z" * 64) is None
    assert _source().get_blob("../../etc/passwd") is None
    assert _source().get_blob("sha256:deadbeef") is None
