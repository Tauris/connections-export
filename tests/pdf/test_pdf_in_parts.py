"""A large PDF rendered in parts reads exactly like the one-piece render.

The paged renderer used to lay out the whole export in one browser page, whose
memory grows with the export (1.5 GB at 200 pages, 5 GB at 3,300) until a large
archive runs out. Rendered in parts and joined, the document must still be one
document: the same pages, the same text on every page (footers with the
whole-document total, contents with whole-document numbers), every internal
link landing on the same page -- including links from one part into another --
and the same bookmarks. These tests render one document both ways and compare.
"""

from __future__ import annotations

import io
import struct
import zlib

import pytest

from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedForum,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    ResolvedAsset,
)
from connections_export.pdf import CHROMIUM_AVAILABLE, PAGED_AVAILABLE
from connections_export.pdf import parts as pdf_parts
from connections_export.pdf.html import render_html, serving_images

LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua. "
) * 5


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(
        b"\x00" + bytes((x * 7 + y) % 256 for x in range(width * 3)) for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


PHOTO = _png(400, 300)
PHOTO_DIGEST = "b" * 64
EXTERNAL_URL = "https://images.example.org/diagram.png"


def _link(href: str, target: str) -> LinkRef:
    return LinkRef(
        original_href=href,
        resolved_url=f"https://hcl.example.invalid/{href}",
        scope="in_export",
        target_page_id=target,
    )


def _model() -> Interchange:
    """Two wikis, a blog and a forum that link to each other across the whole
    document, author styles with structural selectors, a photo and an
    external image."""
    wikis = []
    for w in range(2):
        pages = {}
        for i in range(4):
            pid = f"w{w}p{i}"
            body = (
                # A sheet, then a paragraph that is NOT its first child: the
                # sheet must stay where it is for `:first-child` to agree.
                "<style>.page-body p:first-child { margin-top: 40px; }"
                " .note { padding: 12px; border: 1px solid #999; }</style>"
                + "".join(f"<p>{LOREM}</p>" for _ in range(4 + i))
                + '<p class="note">Related: <a href="x-post">the post</a>,'
                ' <a href="x-topic">the topic</a>, <a href="x-far">far page</a>.</p>'
            )
            links = [
                _link("x-post", "post1"),
                _link("x-topic", "topic1"),
                _link("x-far", f"w{1 - w}p{3 - i}"),
            ]
            assets = []
            if i == 1:
                body += '<p><img src="cid:photo.png" alt="photo"></p>'
                assets.append(
                    ResolvedAsset(
                        original_href="cid:photo.png",
                        resolved_url="https://hcl.example.invalid/photo.png",
                        blob_hash=f"sha256:{PHOTO_DIGEST}",
                        present=True,
                        scope="same",
                    )
                )
            if w == 1 and i == 2:
                body += f'<p><img src="{EXTERNAL_URL}" alt="external"></p>'
                assets.append(
                    ResolvedAsset(
                        original_href=EXTERNAL_URL,
                        resolved_url=EXTERNAL_URL,
                        present=False,
                        scope="external",
                    )
                )
            pages[pid] = DerivedPage(
                id=pid,
                label=pid,
                title=f"Page {w}.{i}",
                content_html=body,
                links=links,
                assets=assets,
                ordinal=i,
            )
        wikis.append(
            DerivedWiki(
                id=f"w{w}", label=f"w{w}", title=f"Wiki {w}", root_page_ids=list(pages), pages=pages
            )
        )
    post = DerivedBlogPost(
        id="post1",
        title="A post",
        content_html=f"<p>{LOREM}</p><p>Back to <a href='x-first'>the first page</a>.</p>",
        links=[_link("x-first", "w0p0")],
    )
    topic = DerivedForumTopic(
        id="topic1",
        title="A topic",
        content_html=f"<p>{LOREM}</p><p>See <a href='x-last'>the last page</a>.</p>",
        links=[_link("x-last", "w1p3")],
    )
    return Interchange(
        wikis=wikis,
        blogs=[DerivedBlog(id="b", title="Blog", post_ids=["post1"], posts={"post1": post})],
        forums=[
            DerivedForum(id="f", title="Forum", topic_ids=["topic1"], topics={"topic1": topic})
        ],
    )


def _blobs(digest: str) -> bytes | None:
    return PHOTO if digest.endswith(PHOTO_DIGEST) else None


EXTERNAL = {EXTERNAL_URL: _png(60, 40)}


# --- planning (no browser) -----------------------------------------------


@pytest.fixture
def make_plan(tmp_path):
    from connections_export.pdf.html import document_head, document_pieces

    def make(budget: int = 1):
        with serving_images() as served:
            plan = pdf_parts.plan_document(
                document_head(_model()),
                document_pieces(_model(), blob_bytes=_blobs, external_images=EXTERNAL),
                served=served,
                spool=tmp_path / "pieces.html",
            )
        return plan.pack(budget)

    return make


def test_the_plan_reassembles_to_exactly_the_one_piece_document(make_plan):
    with serving_images():
        expected = render_html(_model(), blob_bytes=_blobs, external_images=EXTERNAL)
    # Served addresses are content hashes, so the two renders agree.
    assert make_plan().whole_document() == expected


def test_every_item_can_be_its_own_part_and_the_cover_keeps_the_contents(make_plan):
    plan = make_plan(budget=1)
    # Cover + contents' first block are glued; nothing else is.
    assert [u.label for u in plan.front[0].units][:2] == ["the cover", "the table of contents"]
    labels = [p.label for p in plan.body]
    assert labels[0] == "“Page 0.0” in “Wiki 0”"
    assert "“A post” in “Blog”" in labels and "“A topic” in “Forum”" in labels
    # The external-content list points into every other part: last, alone.
    assert plan.external and plan.external[0].label == "the list of external content"


def test_a_generous_budget_keeps_the_body_in_one_part(make_plan):
    plan = make_plan(budget=10**12)
    assert len(plan.body) == 1 and len(plan.front) == 1


def test_internal_links_become_placeholders_naming_their_target(make_plan):
    plan = make_plan()
    html = "".join(pdf_parts.part_document(plan, p) for p in plan.body)
    assert 'href="#' not in html
    assert f'href="{pdf_parts.LINK_PREFIX}b-post1"' in html
    assert {"b-post1", "t-topic1", "p-w0p0", "p-w1p3"} <= plan.targets


def test_author_sheets_travel_in_every_part_and_stay_as_empty_placeholders(make_plan):
    plan = make_plan()
    assert ".note { padding: 12px" in plan.styles
    # One copy: identical sheets are kept once (the last occurrence).
    assert plan.styles.count(".note { padding") == 1
    doc = pdf_parts.part_document(plan, plan.body[3])
    head, body = doc.split("<body>", 1)
    assert ".note { padding" in head and ".note { padding" not in body
    assert "<style></style>" in body


def test_a_photo_weighs_what_the_browser_holds_once_it_is_drawn(make_plan):
    plan = make_plan()
    with_photo = next(u for p in plan.body for u in p.units if "Page 0.1" in u.label)
    without = next(u for p in plan.body for u in p.units if "Page 0.0" in u.label)
    assert with_photo.weight - len(with_photo.html) == 400 * 300 * 4
    assert without.weight == len(without.html)


def test_a_part_splits_in_half_but_never_between_glued_units(make_plan):
    plan = make_plan(budget=10**12)
    halves = pdf_parts.split_part(plan.body[0])
    assert len(halves) == 2
    assert halves[0].units + halves[1].units == plan.body[0].units
    front = pdf_parts.Part(plan.front[0].units[:2], front=True)  # cover + first block
    assert pdf_parts.split_part(front) == [front]


def test_only_a_footer_showing_the_total_needs_the_counting_pass():
    from connections_export.pdf.marks import Marks

    assert pdf_parts.needs_total(None)  # the default footer is "{page} / {pages}"
    assert not pdf_parts.needs_total(Marks(footer_right="{page}"))


# --- in a browser ---------------------------------------------------------

needs_browser = pytest.mark.skipif(
    not (CHROMIUM_AVAILABLE and PAGED_AVAILABLE), reason="needs Chromium and paged.js"
)


def _links(reader) -> list[tuple]:
    """(page, kind, target) for every distinct link: internal ones by target
    page. Distinct, because one link can be drawn as several rectangles, and
    how many depends on how its text was split into runs (the one-piece
    render writes a computed page number as two runs, a part as one)."""
    out = []
    for i, page in enumerate(reader.pages):
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if "/Dest" in annot:
                dest = annot["/Dest"]
                if isinstance(dest, str):
                    dest = reader.named_destinations[dest]
                    out.append((i, "page", reader.get_destination_page_number(dest)))
                else:
                    number = next(
                        n
                        for n, p in enumerate(reader.pages)
                        if p.indirect_reference.idnum == dest[0].idnum
                    )
                    out.append((i, "page", number))
            elif "/A" in annot:
                out.append((i, "uri", annot["/A"].get_object().get("/URI")))
    return sorted(set(out))


def _outline(reader) -> list[tuple]:
    out = []

    def walk(items, depth):
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
            else:
                out.append((depth, item.title, reader.get_destination_page_number(item)))

    walk(reader.outline, 0)
    return out


@pytest.fixture(scope="module")
def both():
    from pypdf import PdfReader

    from connections_export.pdf.paged import render_pdf_paged

    one = render_pdf_paged(_model(), _blobs, external_images=EXTERNAL, part_size_mb=0)
    reports = []
    many = render_pdf_paged(
        _model(),
        _blobs,
        external_images=EXTERNAL,
        part_size_mb=1e-6,  # every item its own part
        progress=reports.append,
    )
    return PdfReader(io.BytesIO(one)), PdfReader(io.BytesIO(many)), reports


@needs_browser
def test_it_really_was_rendered_in_parts(both):
    _one, _many, reports = both
    assert reports[-1].stage == "done" and reports[-1].parts > 10


@needs_browser
def test_the_same_pages_with_the_same_text(both):
    one, many, _ = both
    assert len(many.pages) == len(one.pages)
    differing = [
        i
        for i, (a, b) in enumerate(zip(one.pages, many.pages, strict=True))
        if a.extract_text() != b.extract_text()
    ]
    assert differing == []


@needs_browser
def test_every_link_lands_on_the_same_page(both):
    one, many, _ = both
    assert _links(many) == _links(one)
    assert sum(1 for link in _links(many) if link[1] == "page") > 20


@needs_browser
def test_the_same_bookmarks(both):
    one, many, _ = both
    assert _outline(many) == _outline(one)


@needs_browser
def test_progress_counts_pages_of_the_whole(both):
    _one, many, reports = both
    stages = [r.stage for r in reports]
    assert stages[0] == "preparing" and "measuring" in stages and "assembling" in stages
    final = reports[-1]
    assert final.pages_total == final.pages_done == len(many.pages)
    laying_out = [r for r in reports if r.stage == "laying_out" and r.pages_total]
    assert laying_out and all(r.pages_total == len(many.pages) for r in laying_out)


# --- running out of memory --------------------------------------------------


def _crash_when(monkeypatch, too_big):
    """Make the browser 'run out of memory' on every document `too_big` says
    is too big, the way Chromium reports it: the target crashed."""
    from connections_export.pdf import paged

    real = paged.lay_out

    def lay_out(page, html, **kwargs):
        if too_big(html):
            raise RuntimeError("Page.set_content: Target crashed")
        return real(page, html, **kwargs)

    monkeypatch.setattr(paged, "lay_out", lay_out)


@needs_browser
def test_a_part_that_runs_out_of_memory_is_split_and_the_result_is_the_same(both, monkeypatch):
    from pypdf import PdfReader

    from connections_export.pdf.paged import render_pdf_paged

    one, _many, _ = both
    _crash_when(monkeypatch, lambda html: html.count('class="hcl-page') > 1)
    reports = []
    pdf = render_pdf_paged(
        _model(), _blobs, external_images=EXTERNAL, part_size_mb=10, progress=reports.append
    )
    reader = PdfReader(io.BytesIO(pdf))
    assert any(r.stage == "retrying" for r in reports)
    assert len(reader.pages) == len(one.pages)
    assert [p.extract_text() for p in reader.pages] == [p.extract_text() for p in one.pages]
    assert _links(reader) == _links(one)


@needs_browser
def test_an_item_too_large_on_its_own_is_named(monkeypatch):
    from connections_export.pdf.paged import render_pdf_paged
    from connections_export.pdf.progress import PdfOutOfMemoryError

    _crash_when(monkeypatch, lambda html: ">Page 1.2</h1>" in html)
    with pytest.raises(PdfOutOfMemoryError) as caught:
        render_pdf_paged(_model(), _blobs, external_images=EXTERNAL, part_size_mb=10)
    message = str(caught.value)
    assert "“Page 1.2” in “Wiki 1”" in message
    assert "too large to lay out" in message and "Close other programs" in message
