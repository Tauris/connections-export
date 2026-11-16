"""`connections-export pdf` says what the paged render is doing, and names
an out-of-memory failure without a traceback."""

from __future__ import annotations

from connections_export import cli
from connections_export.pdf.progress import PdfOutOfMemoryError, PdfProgress


def test_laying_out_lines_are_throttled_but_stage_changes_always_print(capsys):
    now = [0.0]
    report = cli._stderr_progress(interval=2.0, clock=lambda: now[0])
    report(PdfProgress(stage="preparing", message="Preparing"))
    report(PdfProgress(stage="laying_out", message="Laying out 1"))
    now[0] = 0.5
    report(PdfProgress(stage="laying_out", message="Laying out 2"))  # too soon
    now[0] = 3.0
    report(PdfProgress(stage="laying_out", message="Laying out 3"))
    report(PdfProgress(stage="printing", message="Printing"))
    err = capsys.readouterr().err.splitlines()
    assert [line.split(": ", 1)[1] for line in err] == [
        "Preparing",
        "Laying out 1",
        "Laying out 3",
        "Printing",
    ]


def test_the_command_hands_the_renderer_progress_and_part_size(tmp_path, capsys):
    seen = {}

    def render(model, blob_bytes, progress=None, part_size_mb=None, **_):
        seen["part"] = part_size_mb
        progress(PdfProgress(stage="printing", message="Printing part 1 of 2"))
        return b"%PDF"

    out = tmp_path / "o.pdf"
    code = cli.pdf_main(["--demo", "--output", str(out), "--part-size-mb", "32"], render=render)
    assert code == 0 and seen["part"] == 32.0
    assert "Printing part 1 of 2" in capsys.readouterr().err


def test_out_of_memory_prints_its_message_and_exits_nonzero(tmp_path, capsys):
    def render(model, blob_bytes, **_):
        raise PdfOutOfMemoryError("Out of memory. Try --part-size-mb 64.")

    code = cli.pdf_main(["--demo", "--output", str(tmp_path / "o.pdf")], render=render)
    err = capsys.readouterr().err
    assert code != 0
    assert "Out of memory. Try --part-size-mb 64." in err and "Traceback" not in err
