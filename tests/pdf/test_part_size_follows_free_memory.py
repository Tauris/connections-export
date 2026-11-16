"""The part size is worked out from the memory free at each export.

A size in megabytes is a number nobody can choose well, on a machine whose
free memory changes by the hour. Measured: the browser's peak is about
1.15 GB plus 2.2 times the part's weight."""

from __future__ import annotations

from connections_export.pdf import memory

GB = 2**30


def test_a_roomy_machine_gets_the_largest_parts():
    assert memory.automatic_part_mb(16 * GB) == memory.MAX_PART_MB


def test_a_tight_machine_gets_small_parts_but_never_none():
    assert memory.automatic_part_mb(2 * GB) < 100
    assert memory.automatic_part_mb(1 * GB) == memory.MIN_PART_MB


def test_the_size_grows_with_free_memory():
    sizes = [memory.automatic_part_mb(n * GB) for n in (2, 3, 4, 6)]
    assert sizes == sorted(sizes) and len(set(sizes)) > 2


def test_unknown_free_memory_falls_back_to_the_default():
    # When the system will not say, the fallback is used.
    original = memory.available_memory
    try:
        memory.available_memory = lambda: None
        assert memory.automatic_part_mb() == memory.FALLBACK_PART_MB
    finally:
        memory.available_memory = original


def test_this_machine_reports_its_free_memory_and_disk(tmp_path):
    free = memory.available_memory()
    assert free is None or free > 0
    assert memory.free_disk(tmp_path / "not" / "yet" / "made") > 0
