"""File names that survive a filesystem, and disambiguation that survives a
re-export.

The cases here are not hypothetical: a Connections library is a nearly
ruleless namespace, and every one of these is something it can hold and a
filesystem cannot.
"""

from __future__ import annotations

import unicodedata

from connections_export.interchange.filenames import (
    REASON_COLLISION,
    REASON_FORBIDDEN,
    REASON_TRAILING,
    REASON_TRUNCATED,
    allocate,
    collision_key,
    plan,
    sanitize,
)


def test_an_ordinary_name_is_left_alone():
    assert sanitize("Quarterly Report.pdf") == "Quarterly Report.pdf"


def test_characters_a_filesystem_forbids_are_replaced():
    assert sanitize('a/b:c?d*e"f<g>h|i.txt') == "a_b_c_d_e_f_g_h_i.txt"


def test_windows_device_names_stay_openable():
    """`CON.txt` is as unopenable as `CON` -- the reservation ignores the
    extension, which is the part people forget."""
    assert sanitize("CON.txt") == "CON_.txt"
    assert sanitize("lpt9") == "lpt9_"
    assert sanitize("console.txt") == "console.txt"  # not reserved


def test_trailing_dots_and_spaces_go():
    """Windows strips them silently, which would fabricate a collision between
    `foo.` and `foo` that Connections does not have."""
    assert sanitize("report. ") == "report"
    assert sanitize("report ...") == "report"


def test_a_dotfile_keeps_its_name():
    assert sanitize(".gitignore") == ".gitignore"


def test_a_name_that_sanitises_away_still_gets_one():
    """Forbidden characters are replaced, not dropped, so `///` keeps its shape
    as `___` -- the original is in the manifest either way. Only a genuinely
    empty stem needs inventing a name for."""
    assert sanitize("///") == "___"
    assert sanitize("") == "unnamed"
    assert sanitize("   ") == "unnamed"


def test_long_names_are_truncated_but_keep_their_extension():
    result = sanitize("x" * 400 + ".pdf")

    assert result.endswith(".pdf")
    assert len(result) < 200


def test_case_and_unicode_form_count_as_the_same_name():
    """Two files in Connections, one on Windows and on macOS -- which stores
    decomposed. Detecting the collision is the whole point."""
    assert collision_key("Report.pdf") == collision_key("report.PDF")
    composed = unicodedata.normalize("NFC", "café.txt")
    decomposed = unicodedata.normalize("NFD", "café.txt")
    assert composed != decomposed
    assert collision_key(composed) == collision_key(decomposed)


def test_unique_names_are_not_decorated():
    names = allocate([("id-1", "a.pdf"), ("id-2", "b.pdf")])

    assert names == {"id-1": "a.pdf", "id-2": "b.pdf"}


def test_every_member_of_a_collision_is_suffixed():
    """Including the first. If one kept the plain name, adding another colliding
    file later would rename a file that had not changed."""
    names = allocate([("id-1", "Report.pdf"), ("id-2", "Report.pdf")])

    assert names["id-1"] != names["id-2"]
    assert "Report.pdf" not in names.values()
    assert all(n.startswith("Report (") and n.endswith(".pdf") for n in names.values())


def test_case_only_differences_are_disambiguated_too():
    names = allocate([("id-1", "Report.pdf"), ("id-2", "report.pdf")])

    assert collision_key(names["id-1"]) != collision_key(names["id-2"])


def test_the_same_file_gets_the_same_name_whatever_the_order():
    """The reason a counter is wrong. Order changes between exports; the names
    must not, or a diff of two packages shows changes that never happened."""
    forward = allocate([("id-1", "Report.pdf"), ("id-2", "Report.pdf")])
    reversed_ = allocate([("id-2", "Report.pdf"), ("id-1", "Report.pdf")])

    assert forward == reversed_


def test_adding_a_file_does_not_rename_an_unrelated_one():
    before = allocate([("id-1", "Report.pdf"), ("id-2", "Notes.txt")])
    after = allocate([("id-1", "Report.pdf"), ("id-2", "Notes.txt"), ("id-3", "Other.txt")])

    assert after["id-2"] == before["id-2"]


def test_every_allocated_name_is_unique_on_a_case_insensitive_filesystem():
    entries = [(f"id-{i}", "Report.pdf") for i in range(50)]

    names = allocate(entries)

    keys = {collision_key(n) for n in names.values()}
    assert len(keys) == len(entries)


# --- reconstruction: the manifest has to carry enough to undo our compromises


def test_a_name_written_unchanged_records_no_reason():
    """Silence means "this is exactly what Connections holds" -- a consumer
    should not have to compare strings to find that out."""
    (entry,) = plan([("id-1", "Report.pdf")])

    assert entry.name == "Report.pdf"
    assert entry.original == "Report.pdf"
    assert entry.reasons == ()


def test_the_original_name_is_always_kept_verbatim():
    """Including the characters that could not be written. This is the
    authority for any reconstruction; without it the original is unrecoverable
    from the package."""
    (entry,) = plan([("id-1", "budget/2026:draft?.xlsx")])

    assert entry.original == "budget/2026:draft?.xlsx"
    assert entry.name == "budget_2026_draft_.xlsx"
    assert REASON_FORBIDDEN in entry.reasons


def test_a_collision_is_reported_as_one():
    """So a target system that can hold both -- a case-sensitive store, a
    database -- knows to restore the original names rather than inheriting a
    filesystem's limitation permanently."""
    entries = plan([("id-1", "Report.pdf"), ("id-2", "report.pdf")])

    assert all(REASON_COLLISION in e.reasons for e in entries)
    assert {e.original for e in entries} == {"Report.pdf", "report.pdf"}


def test_several_reasons_can_apply_at_once():
    (entry,) = plan([("id-1", "CON:x. ")])

    assert REASON_FORBIDDEN in entry.reasons
    assert REASON_TRAILING in entry.reasons


def test_truncation_is_reported_because_it_loses_information():
    (entry,) = plan([("id-1", "x" * 400 + ".pdf")])

    assert REASON_TRUNCATED in entry.reasons
    assert entry.original == "x" * 400 + ".pdf"


def test_plan_and_allocate_agree():
    entries = [("id-1", "Report.pdf"), ("id-2", "Report.pdf"), ("id-3", "Notes.txt")]

    assert {e.file_id: e.name for e in plan(entries)} == allocate(entries)


def test_plan_preserves_input_order():
    """The manifest lists files in the order the library reported them; the
    disambiguator does not depend on that order, but the listing should keep
    it."""
    entries = [("id-3", "c.txt"), ("id-1", "a.txt"), ("id-2", "b.txt")]

    assert [e.file_id for e in plan(entries)] == ["id-3", "id-1", "id-2"]
