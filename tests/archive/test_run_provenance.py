"""What a run records about itself, so a later run can extend it.

An archive that cannot say *when* it was captured and *what* it was asked for
cannot be updated: the cutoff has nowhere to come from, and "add the component
we skipped" has nothing to compare against.

Nothing is released, so these fields are simply added -- there is no migration
and no fallback dance. What matters is that they are written by every run and
that the meanings below hold exactly.
"""

from __future__ import annotations

from connections_export.archive.records import ComponentSelection, RunMetadata


def _meta(**kwargs) -> RunMetadata:
    base = dict(
        run_id="r1",
        base_url="https://fake",
        principal="j.doe",
        adapter_version="wikis-1",
        source_system_version="8.0",
    )
    base.update(kwargs)
    return RunMetadata(**base)


def test_a_run_records_when_it_started():
    """The anchor for the next update is the START of the last good run, not
    its end: an item edited mid-crawl, after its feed page was read, would
    otherwise fall in the gap between end-time and the next cutoff and never be
    seen again."""
    meta = _meta(started_at="2026-08-10T21:14:00Z")

    assert meta.started_at == "2026-08-10T21:14:00Z"


def test_a_run_in_flight_has_no_completion_time():
    """`completed_at` absent is what distinguishes a run that finished from one
    that was killed. Only a finished run may anchor the next update."""
    assert _meta().completed_at is None


def test_a_finished_run_records_completion():
    meta = _meta(completed_at="2026-08-10T22:02:00Z")

    assert meta.completed_at == "2026-08-10T22:02:00Z"


def test_a_run_records_what_it_was_asked_for_not_what_it_managed():
    """Selection, not outcome. A component that was asked for and yielded
    nothing is still part of this archive's question -- and a component that
    was never asked for is what "extend" later offers."""
    meta = _meta(
        components=[
            ComponentSelection(kind="wiki", id="eng-handbook", action="capture"),
            ComponentSelection(kind="files", id="c-1", action="skip"),
        ]
    )

    assert [c.kind for c in meta.components] == ["wiki", "files"]
    assert meta.components[1].action == "skip"


def test_a_run_records_its_kind():
    assert _meta().run_kind == "initial"
    assert _meta(run_kind="extend_update").run_kind == "extend_update"


def test_an_update_records_the_cutoff_it_actually_used():
    """Not the cutoff it computed or displayed -- the one it sent. Without it,
    nobody can tell afterwards which window an archive covers."""
    meta = _meta(run_kind="update", since_cutoff="2026-08-10T21:13:00Z")

    assert meta.since_cutoff == "2026-08-10T21:13:00Z"


def test_a_person_archive_records_whose_content_it_answers_for():
    meta = _meta(person_subject="ataylor", search_scopes=["blogs", "forums"])

    assert meta.person_subject == "ataylor"
    assert meta.search_scopes == ["blogs", "forums"]


def test_the_author_filter_is_part_of_the_question_the_archive_answers():
    """An archive captured with "only me" cannot later be updated as if it were
    an everyone archive without changing what it means."""
    assert _meta(author_filter="a.taylor").author_filter == "a.taylor"


def test_every_new_field_is_optional_so_a_run_can_be_recorded_before_it_ends():
    """The metadata file is written when the run starts; most of these are
    filled in as it goes."""
    meta = _meta()

    assert meta.completed_at is None
    assert meta.components == []
    assert meta.since_cutoff is None
