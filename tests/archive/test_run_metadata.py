"""Tests for run metadata: run-<run_id>.json write/read."""

from connections_export.archive.records import RunMetadata
from connections_export.archive.store import Archive


def test_write_run_metadata_records_required_fields_and_is_retrievable(tmp_path):
    archive = Archive.open(tmp_path)
    run = RunMetadata(
        run_id="2026-07-20T180000Z",
        base_url="https://host/wikis/basic",
        principal="demo-user",
        adapter_version="wikis-adapter-0.1.0",
        source_system_version="HCL Connections 8.0",
    )

    path = archive.write_run_metadata(run)

    assert path == tmp_path / "run-2026-07-20T180000Z.json"
    assert path.is_file()

    retrieved = archive.read_run_metadata("2026-07-20T180000Z")
    assert retrieved == run
    assert retrieved.base_url == "https://host/wikis/basic"
    assert retrieved.principal == "demo-user"
    assert retrieved.adapter_version == "wikis-adapter-0.1.0"
    assert retrieved.source_system_version == "HCL Connections 8.0"


def test_run_metadata_retrievable_alongside_manifest(tmp_path):
    archive = Archive.open(tmp_path)
    run = RunMetadata(
        run_id="run-1",
        base_url="https://host",
        principal="alice",
        adapter_version="v1",
        source_system_version="8.0",
    )
    archive.write_run_metadata(run)

    from tests.archive.conftest import FakeResponse

    archive.write_response(
        FakeResponse(url="https://host/x", method="GET", status=200, content=b"x"),
        fetched_at="2026-07-20T18:00:00Z",
    )

    # Both the manifest and the run metadata coexist under the same root.
    assert (tmp_path / "manifest.jsonl").is_file()
    assert archive.read_run_metadata("run-1") == run
