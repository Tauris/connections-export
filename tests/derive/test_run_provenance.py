"""`derive` reads run provenance from the
archive with fallback (derive -- read it, fall back
gracefully): explicit kwargs > run metadata > URL recovery. Small,
hand-built archives -- no crawl, no HTTP -- mirroring
`tests/derive/test_assemble.py`'s own style.
"""

import time

from connections_export.adapters.wikis import wikis_feed_url
from connections_export.archive.records import RunMetadata
from connections_export.archive.store import Archive
from connections_export.derive.assemble import derive
from connections_export.derive.model import Interchange
from connections_export.fakeserver import atom as fake_atom
from connections_export.fakeserver.model import WikiSet
from tests.archive.conftest import FakeResponse

BASE_URL = "https://fake"
AUTH_ROOT = "basic"
PAGE_SIZE = 500


def _seed_empty_archive(tmp_path) -> Archive:
    """The minimum an archive needs for `derive` to locate its entry
    point at all (`_discover_root`): one archived
    `.../wikis/feed?ps=..&page=1` response, no wikis inside it."""
    archive = Archive.open(tmp_path / "archive")
    wikis_url = f"{wikis_feed_url(base_url=BASE_URL, auth_root=AUTH_ROOT)}?ps={PAGE_SIZE}&page=1"
    archive.write_response(
        FakeResponse(
            url=wikis_url,
            method="GET",
            status=200,
            content=fake_atom.wikis_feed(
                WikiSet(wikis=[]), auth_root=AUTH_ROOT, base_url=BASE_URL, ps=PAGE_SIZE, page=1
            ),
        ),
        fetched_at="2026-01-01T00:00:00Z",
    )
    return archive


def _run_metadata(**overrides) -> RunMetadata:
    fields = dict(
        run_id="run-1",
        base_url=BASE_URL,
        principal="unknown",
        adapter_version="wikis-1",
        source_system_version="8.0",
        hcl_hosts=["files.example.corp"],
    )
    fields.update(overrides)
    return RunMetadata(**fields)


# --- 3.1: derive recovers base_url/hcl_hosts/source_version from run metadata


def test_derive_recovers_provenance_from_run_metadata(tmp_path):
    archive = _seed_empty_archive(tmp_path)
    archive.write_run_metadata(_run_metadata())

    interchange = derive(archive)

    assert interchange.base_url == BASE_URL
    assert interchange.hcl_hosts == ["files.example.corp"]
    assert interchange.source_version == "8.0"
    assert interchange.run_id == "run-1"


def test_explicit_kwargs_override_run_metadata(tmp_path):
    archive = _seed_empty_archive(tmp_path)
    archive.write_run_metadata(_run_metadata())

    interchange = derive(
        archive,
        base_url="https://override.example.corp",
        hcl_hosts=["override.example.corp"],
        source_version="9.0",
        run_id="explicit-run",
    )

    assert interchange.base_url == "https://override.example.corp"
    assert interchange.hcl_hosts == ["override.example.corp"]
    assert interchange.source_version == "9.0"
    assert interchange.run_id == "explicit-run"


def test_explicit_empty_hcl_hosts_kwarg_overrides_a_nonempty_run_metadata(tmp_path):
    """An explicit `hcl_hosts=[]` must still win over run metadata --
    proves the precedence distinguishes "not passed" from "passed as
    empty", not just truthiness."""
    archive = _seed_empty_archive(tmp_path)
    archive.write_run_metadata(_run_metadata(hcl_hosts=["files.example.corp"]))

    interchange = derive(archive, hcl_hosts=[])

    assert interchange.hcl_hosts == []


# --- 3.1: no run metadata -> URL recovery + empty hcl_hosts, unchanged


def test_no_run_metadata_falls_back_to_url_recovery_and_empty_hcl_hosts(tmp_path):
    archive = _seed_empty_archive(tmp_path)  # no write_run_metadata call at all

    interchange = derive(archive)

    assert interchange.base_url == BASE_URL  # recovered from the wikis-feed URL
    assert interchange.hcl_hosts == []
    assert interchange.run_id is None
    assert interchange.source_version is None


# --- 3.3: most-recent run metadata wins when several are present


def test_most_recent_run_metadata_is_used_when_several_present(tmp_path):
    archive = _seed_empty_archive(tmp_path)
    archive.write_run_metadata(
        _run_metadata(
            run_id="2026-01-01T000000Z", source_system_version="7.0", hcl_hosts=["old.example.corp"]
        )
    )
    time.sleep(0.01)  # ensure a distinguishable mtime, belt-and-suspenders with the name tie-break
    archive.write_run_metadata(
        _run_metadata(
            run_id="2026-06-01T000000Z", source_system_version="8.0", hcl_hosts=["new.example.corp"]
        )
    )

    interchange = derive(archive)

    assert interchange.run_id == "2026-06-01T000000Z"
    assert interchange.source_version == "8.0"
    assert interchange.hcl_hosts == ["new.example.corp"]


# --- 3.2: Interchange provenance fields


def test_interchange_carries_hcl_hosts_default_empty():
    interchange = Interchange(base_url=BASE_URL)
    assert interchange.hcl_hosts == []


def test_interchange_hcl_hosts_round_trips_through_json():
    interchange = Interchange(base_url=BASE_URL, run_id="run-1", hcl_hosts=["files.example.corp"])

    dumped = interchange.model_dump_json()
    reloaded = Interchange.model_validate_json(dumped)

    assert reloaded == interchange
    assert reloaded.hcl_hosts == ["files.example.corp"]
