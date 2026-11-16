"""The streaming joiner: Chromium's PDFs joined without holding them.

pypdf joining 412 MB of parts peaked at 858 MB and pikepdf at 449 MB -- memory
growing with the PDF. The joiner copies every object byte for byte, stream
data in chunks, rewriting only small dictionaries; it peaked at 55 MB (Python
alone is 53). These tests hold it to pypdf's result."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from connections_export.pdf import CHROMIUM_AVAILABLE
from connections_export.pdf import join as pdf_join

needs_browser = pytest.mark.skipif(not CHROMIUM_AVAILABLE, reason="needs Chromium")

PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def _print(tmp: Path, name: str, html: str) -> Path:
    from playwright.sync_api import sync_playwright

    from connections_export.pdf.browser import launch_browser

    path = tmp / name
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page()
        page.set_content(html)
        page.pdf(path=str(path))
        browser.close()
    return path


def _part(tmp: Path, n: int) -> Path:
    pages = "".join(
        f'<div style="break-after:page"><h1>Part {n} page {i}</h1>'
        f'<p>Text «ü» {n}.{i}</p><img src="data:image/png;base64,{PNG_1PX}" width=80>'
        f'<a href="https://part-link.example.invalid/t-{n}-{i}">link</a></div>'
        for i in range(3)
    )
    return _print(tmp, f"part-{n}.pdf", f"<html><body>{pages}</body></html>")


@needs_browser
def test_a_join_reads_exactly_like_pypdfs(tmp_path):
    from pypdf import PdfReader, PdfWriter

    parts = [_part(tmp_path, n) for n in range(3)]
    ours = tmp_path / "ours.pdf"
    count = pdf_join.join(parts, ours)
    writer = PdfWriter()
    for part in parts:
        writer.append(str(part))
    theirs = io.BytesIO()
    writer.write(theirs)

    a = PdfReader(ours, strict=True)
    b = PdfReader(io.BytesIO(theirs.getvalue()))
    assert count == len(a.pages) == len(b.pages) == 9
    assert [p.extract_text() for p in a.pages] == [p.extract_text() for p in b.pages]

    # Images come through byte for byte (the raw, still-encoded stream).
    def image_bytes(page):
        xobjects = page["/Resources"]["/XObject"]
        return [xobjects[k].get_object()._data for k in sorted(xobjects)]

    assert image_bytes(a.pages[4]) == image_bytes(b.pages[4])
    assert image_bytes(a.pages[4])
    # Every link is still there.
    assert sum(len(p.get("/Annots") or []) for p in a.pages) == 9


@needs_browser
def test_links_are_rewritten_into_jumps_and_bookmarks_added(tmp_path):
    from pypdf import PdfReader

    parts = [_part(tmp_path, n) for n in range(2)]
    out = tmp_path / "out.pdf"

    def rewrite(body, pages):
        uri = pdf_join.link_uri(body)
        if uri and uri.endswith("t-1-2"):
            return pdf_join.link_with(body, b"/Dest [" + pages.ref(5) + b" /Fit]")
        return b""  # every other link goes inert

    pdf_join.join(
        parts,
        out,
        rewrite_annot=rewrite,
        outline=[pdf_join.Bookmark("Part 0", 1, 0), pdf_join.Bookmark("Part 1 é", 1, 3)],
        open_outline=True,
    )
    reader = PdfReader(out, strict=True)
    numbers = {p.indirect_reference.idnum: i for i, p in enumerate(reader.pages)}
    jumps = [
        numbers[a.get_object()["/Dest"][0].idnum]
        for p in reader.pages
        for a in p.get("/Annots") or []
        if "/Dest" in a.get_object()
    ]
    assert jumps == [5]
    assert all("/A" not in a.get_object() for p in reader.pages for a in p.get("/Annots") or [])
    assert [(o.title, reader.get_destination_page_number(o)) for o in reader.outline] == [
        ("Part 0", 0),
        ("Part 1 é", 3),
    ]
    assert reader.page_mode == "/UseOutlines"


@needs_browser
def test_chromiums_named_links_still_land(tmp_path):
    """A one-piece render links within itself by named destinations kept in
    the catalog, which the joiner rebuilds -- so it points them at pages."""
    from pypdf import PdfReader

    html = (
        '<p><a href="#target">to the target</a></p>'
        '<div style="break-before:page"><div style="break-before:page">'
        '<h2 id="target">Target</h2></div></div>'
    )
    src = _print(tmp_path, "named.pdf", html)
    out = tmp_path / "out.pdf"
    pdf_join.join([src], out)
    reader = PdfReader(out, strict=True)
    [annot] = [a.get_object() for a in reader.pages[0].get("/Annots")]
    dest_page = annot["/Dest"][0].idnum
    target = next(i for i, p in enumerate(reader.pages) if p.indirect_reference.idnum == dest_page)
    assert "Target" in reader.pages[target].extract_text()


@needs_browser
def test_attachments_are_embedded_one_at_a_time(tmp_path):
    from pypdf import PdfReader

    loaded = []

    def load(data):
        def go():
            loaded.append(data)
            return data

        return go

    out = tmp_path / "out.pdf"
    pdf_join.join(
        [_part(tmp_path, 0)],
        out,
        attachments=[("b.txt", load(b"bee")), ("a.txt", load(b"ay")), ("gone", lambda: None)],
    )
    reader = PdfReader(out, strict=True)
    assert reader.attachments == {"a.txt": [b"ay"], "b.txt": [b"bee"]}
    assert loaded == [b"bee", b"ay"]


@needs_browser
def test_links_can_be_rewritten_in_place_keeping_everything_else(tmp_path):
    from pypdf import PdfReader

    joined = tmp_path / "joined.pdf"
    pdf_join.join(
        [_part(tmp_path, 0)],
        joined,
        outline=[pdf_join.Bookmark("Start", 1, 0)],
        attachments=[("a.txt", lambda: b"ay")],
    )
    out = tmp_path / "out.pdf"
    changed = pdf_join.rewrite_links(
        joined,
        out,
        lambda body, pages: pdf_join.link_with(
            body, b"/A <</S /GoToR /F (other.pdf) /D [2 /Fit]>>"
        ),
    )
    reader = PdfReader(out, strict=True)
    assert changed == 3
    actions = [a.get_object()["/A"]["/S"] for p in reader.pages for a in p.get("/Annots")]
    assert actions == ["/GoToR"] * 3
    assert [o.title for o in reader.outline] == ["Start"]
    assert reader.attachments == {"a.txt": [b"ay"]}


def test_a_key_is_not_mistaken_for_a_longer_one():
    """A font stream carries /Length, /Length1 and /Length2."""
    body = b"<</Length1 100 /Length2 200 /Length 300 /Filter /FlateDecode>>"
    assert pdf_join._dict_value(body, b"/Length") == b"300"
    replaced = pdf_join._set_key(body, b"/Length", b"42")
    assert b"/Length1 100" in replaced and b"/Length2 200" in replaced
    assert replaced.count(b"/Length ") == 1 and b"/Length 42" in replaced


def test_references_inside_strings_are_left_alone():
    body = b"<</Title (see 3 0 R here) /Parent 3 0 R /Kids [4 0 R 5 0 R]>>"
    out = pdf_join._renumber(body, lambda n: n + 100)
    assert out == b"<</Title (see 3 0 R here) /Parent 103 0 R /Kids [104 0 R 105 0 R]>>"


def test_a_file_it_cannot_copy_says_so(tmp_path):
    """A cross-reference stream (or an object stream, or an update) is not
    Chromium's shape: callers fall back to pypdf for it."""
    path = tmp_path / "xrefstream.pdf"
    path.write_bytes(b"%PDF-1.5\n1 0 obj\n<<>>\nendobj\nstartxref\n9\n%%EOF\n")
    with pytest.raises(pdf_join.NotSimple):
        pdf_join.join([path], tmp_path / "out.pdf")


def test_text_strings_are_written_safely():
    assert pdf_join.pdf_string("a (b) \\ c") == b"(a \\(b\\) \\\\ c)"
    assert pdf_join.pdf_string("é").startswith(b"<FEFF")
