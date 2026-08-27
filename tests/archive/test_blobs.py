"""Tests for connections_export.archive.blobs: content-addressed blob storage."""

import hashlib

from connections_export.archive import blobs


def test_write_blob_returns_sha256_id(tmp_path):
    data = b"hello world"
    expected_digest = hashlib.sha256(data).hexdigest()

    digest = blobs.write_blob(tmp_path, data)

    assert digest == expected_digest


def test_read_blob_returns_identical_bytes(tmp_path):
    data = b"some response body\x00with a null byte"

    digest = blobs.write_blob(tmp_path, data)
    read_back = blobs.read_blob(tmp_path, digest)

    assert read_back == data


def test_identical_bodies_dedupe_to_one_file(tmp_path):
    data = b"same content twice"

    digest_a = blobs.write_blob(tmp_path, data)
    digest_b = blobs.write_blob(tmp_path, data)

    assert digest_a == digest_b
    stored_files = list((tmp_path / "blobs").glob("*"))
    assert len(stored_files) == 1


def test_distinct_bodies_do_not_collide(tmp_path):
    digest_a = blobs.write_blob(tmp_path, b"content A")
    digest_b = blobs.write_blob(tmp_path, b"content B")

    assert digest_a != digest_b
    stored_files = list((tmp_path / "blobs").glob("*"))
    assert len(stored_files) == 2


def test_binary_bytes_survive_round_trip_unchanged(tmp_path):
    # Stand-in for a fake binary attachment: non-UTF8 bytes, embedded nulls.
    data = bytes(range(256)) * 4

    digest = blobs.write_blob(tmp_path, data)
    read_back = blobs.read_blob(tmp_path, digest)

    assert read_back == data


def test_blob_is_stored_under_blobs_dir_named_by_hex_digest(tmp_path):
    data = b"content addressed"
    digest = blobs.write_blob(tmp_path, data)

    assert (tmp_path / "blobs" / digest).is_file()
    assert (tmp_path / "blobs" / digest).read_bytes() == data
