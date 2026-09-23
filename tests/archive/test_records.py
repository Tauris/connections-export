"""Tests for connections_export.archive.records: ManifestRecord, RunMetadata, Outcome."""

import json

from connections_export.archive.records import ManifestRecord, Outcome, RunMetadata


def test_manifest_record_round_trips_through_json():
    record = ManifestRecord(
        url="https://host/wikis/basic/api/wikis/feed",
        method="GET",
        status=200,
        request_headers={"Accept": "application/atom+xml"},
        response_headers={"Content-Type": "application/atom+xml; charset=UTF-8"},
        fetched_at="2026-07-20T18:00:00Z",
        body_hash="sha256:" + "a" * 64,
        content_type="application/atom+xml",
        discovered_from="https://host/wikis/basic/api/wikis/feed",
        outcome=Outcome.ok,
    )

    line = record.model_dump_json()
    restored = ManifestRecord.model_validate_json(line)

    assert restored == record


def test_manifest_record_has_stable_field_order():
    record = ManifestRecord(
        url="https://host/x",
        method="GET",
        status=200,
        fetched_at="2026-07-20T18:00:00Z",
        outcome=Outcome.ok,
    )

    keys = list(json.loads(record.model_dump_json()).keys())

    assert keys == [
        "schema_version",
        "url",
        "method",
        "status",
        "request_headers",
        "response_headers",
        "fetched_at",
        "body_hash",
        "content_type",
        "discovered_from",
        "outcome",
        "error",
        "item_count",
        "page_size",
        "has_next",
        #: an append-only manifest shared by several runs
        # cannot attribute a line without `run_id`, and an update compares an
        # item's own date against the archived one rather than trusting either
        # machine's clock.
        "run_id",
        "item_date",
    ]


def test_manifest_record_carries_schema_version():
    record = ManifestRecord(
        url="https://host/x",
        method="GET",
        status=200,
        fetched_at="2026-07-20T18:00:00Z",
        outcome=Outcome.ok,
    )

    assert record.schema_version == 1


def test_run_metadata_round_trips_through_json():
    run = RunMetadata(
        run_id="2026-07-20T180000Z",
        base_url="https://host/wikis/basic",
        principal="demo-user",
        adapter_version="wikis-adapter-0.1.0",
        source_system_version="HCL Connections 8.0",
    )

    line = run.model_dump_json()
    restored = RunMetadata.model_validate_json(line)

    assert restored == run


def test_run_metadata_has_stable_field_order_and_schema_version():
    run = RunMetadata(
        run_id="2026-07-20T180000Z",
        base_url="https://host/wikis/basic",
        principal="demo-user",
        adapter_version="wikis-adapter-0.1.0",
        source_system_version="HCL Connections 8.0",
    )

    keys = list(json.loads(run.model_dump_json()).keys())

    #: `hcl_hosts` is a new field appended to the
    # on-disk shape -- this assertion is updated (not left broken)
    # because it is a legitimate structural assertion about field
    # order, and the new field genuinely changes that order.
    assert keys == [
        "schema_version",
        "run_id",
        "base_url",
        "principal",
        "adapter_version",
        "source_system_version",
        "hcl_hosts",
        "max_items",
        #: what a later run needs in order to act on this
        # archive rather than remake it. Same reasoning as the note above --
        # a real structural change, so the assertion moves with it.
        "started_at",
        "completed_at",
        "run_kind",
        "components",
        # Which community this run captured for. A scoped wiki crawl skips the
        # only feed carrying `snx:communityUuid`, so the run records what it
        # was asked to do instead of the archive losing the attribution --
        # same reasoning as the notes above, a real structural change and the
        # assertion moves with it.
        "community_uuid",
        "community_title",
        "author_filter",
        "person_subject",
        "search_scopes",
        "since_cutoff",
        # Which program wrote this run and where to get it. Appended, so the
        # shape everything before it already had is unchanged -- and the same
        # reasoning as the notes above: a real structural change, and the
        # assertion moves with it.
        "generator",
        "generator_version",
        "generator_url",
        "generator_repo_url",
    ]
    assert run.schema_version == 1


def test_run_metadata_hcl_hosts_round_trips_through_json():
    run = RunMetadata(
        run_id="2026-07-20T180000Z",
        base_url="https://host/wikis/basic",
        principal="demo-user",
        adapter_version="wikis-adapter-0.1.0",
        source_system_version="HCL Connections 8.0",
        hcl_hosts=["files.example.corp"],
    )

    line = run.model_dump_json()
    restored = RunMetadata.model_validate_json(line)

    assert restored == run
    assert restored.hcl_hosts == ["files.example.corp"]


def test_run_metadata_defaults_hcl_hosts_to_empty_list():
    run = RunMetadata(
        run_id="2026-07-20T180000Z",
        base_url="https://host/wikis/basic",
        principal="demo-user",
        adapter_version="wikis-adapter-0.1.0",
        source_system_version="HCL Connections 8.0",
    )

    assert run.hcl_hosts == []


def test_older_run_metadata_record_without_hcl_hosts_still_validates():
    # Simulates a `run-*.json` written before this field existed: the
    # JSON on disk simply has no `hcl_hosts` key at all.
    older_record_json = {
        "schema_version": 1,
        "run_id": "2026-01-01T000000Z",
        "base_url": "https://host/wikis/basic",
        "principal": "demo-user",
        "adapter_version": "wikis-adapter-0.1.0",
        "source_system_version": "HCL Connections 8.0",
    }

    restored = RunMetadata.model_validate(older_record_json)

    assert restored.hcl_hosts == []


def test_outcome_enum_has_exactly_the_four_required_values():
    assert {o.value for o in Outcome} == {
        "ok",
        "http_error",
        "transport_error",
        "truncated",
    }


def test_manifest_record_fetched_at_is_caller_supplied_not_computed():
    # The archive layer must never read the wall clock itself; the model just
    # stores whatever string the caller injects, verbatim.
    record = ManifestRecord(
        url="https://host/x",
        method="GET",
        status=200,
        fetched_at="1999-01-01T00:00:00Z",
        outcome=Outcome.ok,
    )

    assert record.fetched_at == "1999-01-01T00:00:00Z"
