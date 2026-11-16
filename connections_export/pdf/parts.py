"""A large PDF, laid out in parts and stitched into one document.

paged.js lays out the whole document in one browser page, and the browser's
memory grows with it: 1.5 GB for 200 pages with photos, 2.5 GB for 800. An
archive of thousands of pages runs out of memory -- on the machine it was tested
on, and sooner on the smaller ones other people use. So a large export is cut
into parts, each laid out and printed on its own in a fresh page, and the part
PDFs are joined.

What makes the parts read as one document:

- **Cut only where a page break already is.** Every container and every item
  in it starts a new sheet, as does the table of contents and each of its
  page-sized blocks. A part boundary at one of those changes no page break.
- **Page numbers run on.** Each part's footers are stamped with its offset in
  the whole and the whole's page count, which a measuring pass (layout only)
  learns first when a footer shows it.
- **Links reach across parts.** Every internal link is written as a
  placeholder address (`LINK_PREFIX` + target id), which the browser keeps as a
  URI link; each part reports where its link targets landed; after joining,
  every placeholder becomes a jump to the target's page and position.
- **Numbers that pointed at other pages** (the contents, the external-images
  table) are filled in from those positions rather than by `target-counter`,
  which only sees its own document. Their boxes have a fixed width, so the
  contents come out the same length before and after the numbers are known.
- **The running footer continues**: each part starts with the container and
  item names in effect where the previous one ended.
- **Author styles apply everywhere**, as they did in one document: every part
  carries every `<style>` from the body.
"""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from html import escape as _escape
from pathlib import Path
from urllib.parse import quote, unquote

import lxml.html

from connections_export.pdf.html import (
    REMOTE_TARGET,
    ServedImages,
    _intrinsic_size,
    served_bytes,
)
from connections_export.pdf.progress import (
    PdfOutOfMemoryError,
    PdfProgress,
    ProgressCallback,
    ignore,
)

#: The address an internal link is written as inside a part. `.invalid` never
#: resolves; nothing loads it -- it only has to survive into the PDF.
LINK_PREFIX = "https://part-link.example.invalid/"


_CONTAINER_CLASSES = ("hcl-wiki", "hcl-blog", "hcl-forum", "hcl-rc", "hcl-files")


#: Fills in each cross-part page number before paged.js lays the part out.
_FILL_PAGES_JS = """
(pages) => {
  document.querySelectorAll('[data-hcl-target]').forEach((el) => {
    const page = pages[el.getAttribute('data-hcl-target')];
    if (page !== undefined) el.setAttribute('data-hcl-page', String(page));
  });
}
"""

#: Where each wanted id landed: page index within the part and the offset
#: from the page box's top-left, in CSS px. paged.js may repeat an element
#: that straddles pages; the first in document order is the original.
_POSITIONS_JS = """
(ids) => {
  const want = new Set(ids);
  const pages = Array.from(document.querySelectorAll('.pagedjs_page'));
  const out = {};
  document.querySelectorAll('.pagedjs_page [id]').forEach((el) => {
    if (!want.has(el.id) || Object.prototype.hasOwnProperty.call(out, el.id)) return;
    const pageEl = el.closest('.pagedjs_page');
    const box = pageEl.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    out[el.id] = [pages.indexOf(pageEl), r.left - box.left, r.top - box.top];
  });
  return out;
}
"""

_LENGTH_CHANGED = (
    "Part {n} laid out to a different number of pages than when it was counted;"
    " the page numbers would be wrong, so the export stops here."
)

#: CSS px to PDF points (96 dpi to 72).
_PX_TO_PT = 0.75


# --- planning ------------------------------------------------------------


@dataclass
class Unit:
    """One piece of the document (`html.Piece`), kept on disk: its HTML is
    in the plan's spool file at `offset`, `length` bytes long."""

    offset: int
    length: int
    weight: int
    label: str
    section: str
    container: int | None = None
    open_tag: str = ""
    close_tag: str = ""
    glue_next: bool = False
    spool: Path | None = None

    @property
    def html(self) -> str:
        with open(self.spool, "rb") as handle:
            handle.seek(self.offset)
            return handle.read(self.length).decode("utf-8")


@dataclass
class Part:
    units: list[Unit]
    front: bool = False
    #: Pages, once measured or rendered.
    pages: int | None = None

    @property
    def weight(self) -> int:
        return sum(u.weight for u in self.units)

    @property
    def label(self) -> str:
        if len(self.units) == 1:
            return self.units[0].label
        return f"{self.units[0].label} … {self.units[-1].label}"


@dataclass
class Plan:
    head: str
    #: Every author sheet in the document, deduplicated keeping the last
    #: occurrence (later sheets win the cascade, so among identical copies
    #: only the last one's position matters).
    styles: str
    #: Every id some internal link points at.
    targets: set[str]
    units: dict[str, list[Unit]]
    front: list[Part] = field(default_factory=list)
    body: list[Part] = field(default_factory=list)
    #: The external-content list: it points into every other part, so it is
    #: a part of its own, rendered once their page numbers are known.
    external: list[Part] = field(default_factory=list)
    #: Ids whose position is wanted although nothing here links to them --
    #: the items a set of PDFs links to from its other files.
    extra_targets: set[str] = field(default_factory=set)

    @property
    def order(self) -> list[Part]:
        """The parts in document order."""
        return self.front + self.body + self.external

    @property
    def weight(self) -> int:
        return sum(u.weight for name in ("front", "body", "external") for u in self.units[name])

    def pack(self, budget: int) -> Plan:
        """Cut the document into parts of at most `budget` weight each -- or
        one unit, if a unit alone is heavier."""
        self.front = [Part(p.units, front=True) for p in _pack(self.units["front"], budget)]
        self.body = _pack(self.units["body"], budget)
        self.external = [Part([u]) for u in self.units["external"]]
        return self

    def whole_document(self) -> str:
        """The document in one piece -- exactly what `render_html` returns."""
        from connections_export.pdf.html import PIECE_SECTIONS, join_pieces  # noqa: PLC0415

        return (
            '<!DOCTYPE html><html lang="en"><head>'
            + self.head
            + "</head><body>"
            + "".join(
                join_pieces(_AsPiece(u, u.html) for u in self.units[name])
                for name in PIECE_SECTIONS
            )
            + "</body></html>"
        )


@dataclass
class _AsPiece:
    """A unit seen as a piece, for `join_pieces`."""

    unit: Unit
    html: str

    @property
    def container(self):
        return self.unit.container

    @property
    def open_tag(self):
        return self.unit.open_tag

    @property
    def close_tag(self):
        return self.unit.close_tag


def _classes(el) -> set[str]:
    return set((el.get("class") or "").split())


def _image_weight(src: str, served: ServedImages | None) -> int:
    """Decoded size of a served image: what the browser holds once it is drawn."""
    if not served or src not in served:
        return 0
    data = served_bytes(served[src])
    size = _intrinsic_size(data)
    if size is None:
        return len(data) * 8
    return size[0] * size[1] * 4


def _fragment(html: str):
    return lxml.html.fragment_fromstring(html, create_parent="div")


def _serialize_children(parent) -> str:
    return _escape(parent.text or "", quote=False) + "".join(
        lxml.html.tostring(child, encoding="unicode") for child in parent
    )


def _prepare(html: str) -> str:
    """A unit's HTML as it goes into a part: internal links point at their
    placeholder address, and author sheets are emptied (they travel in the
    part's head). Emptied, not removed: a selector like `p:first-child` must
    match exactly what it matched in the one document."""
    root = _fragment(html)
    for el in root.iter("a", "area"):
        href = el.get("href") or ""
        if href.startswith("#") and len(href) > 1:
            target = href[1:]
            el.set("href", LINK_PREFIX + quote(target, safe=""))
            el.set("data-hcl-target", target)
    for style in root.iter("style"):
        for child in list(style):
            style.remove(child)
        style.text = None
    return _serialize_children(root)


def _pack(units: list[Unit], budget: int) -> list[Part]:
    parts: list[Part] = []
    current: list[Unit] = []
    weight = 0
    for unit in units:
        glued = bool(current) and current[-1].glue_next
        if current and not glued and weight + unit.weight > budget:
            parts.append(Part(current))
            current, weight = [], 0
        current.append(unit)
        weight += unit.weight
    if current:
        parts.append(Part(current))
    return parts


def plan_document(head: str, pieces, *, served: ServedImages | None, spool: Path) -> Plan:
    """Take the document's `pieces` (`html.document_pieces`) one at a time:
    write each one's HTML to `spool` and keep only what planning needs --
    its weight (HTML length plus the decoded size of its images), its link
    targets and its author sheets. The whole document is never in memory.

    Call `Plan.pack` before rendering."""
    from connections_export.pdf.html import PIECE_SECTIONS  # noqa: PLC0415

    units: dict[str, list[Unit]] = {name: [] for name in PIECE_SECTIONS}
    targets: set[str] = set()
    sheets: list[str] = []
    offset = 0
    with open(spool, "wb") as out:
        for piece in pieces:
            root = _fragment(piece.html)
            for el in root.iter("a", "area"):
                href = el.get("href") or ""
                if href.startswith("#") and len(href) > 1:
                    targets.add(href[1:])
            sheets.extend(
                lxml.html.tostring(style, encoding="unicode", with_tail=False)
                for style in root.iter("style")
            )
            seen: set[str] = set()
            weight = len(piece.html)
            for img in root.iter("img"):
                src = img.get("src") or ""
                if src not in seen:
                    seen.add(src)
                    weight += _image_weight(src, served)
            data = piece.html.encode("utf-8")
            out.write(data)
            units[piece.section].append(
                Unit(
                    offset=offset,
                    length=len(data),
                    weight=weight,
                    label=piece.label,
                    section=piece.section,
                    container=piece.container,
                    open_tag=piece.open_tag,
                    close_tag=piece.close_tag,
                    glue_next=piece.glue_next,
                    spool=spool,
                )
            )
            offset += len(data)
    last = {sheet: i for i, sheet in enumerate(sheets)}
    styles = "".join(sheet for i, sheet in enumerate(sheets) if last[sheet] == i)
    return Plan(head=head, styles=styles, targets=targets, units=units)


def part_document(plan: Plan, part: Part) -> str:
    """One part as a complete HTML document."""
    from connections_export.pdf.html import join_pieces  # noqa: PLC0415
    from connections_export.pdf.paged import AFTER_PAGED_STYLE  # noqa: PLC0415

    body = join_pieces(_AsPiece(u, _prepare(u.html)) for u in part.units)
    # Every author sheet goes in the head, after the paged-media sheet: that
    # lays out exactly as the one document does (measured -- the same sheets
    # at the END of the body lay out differently, because paged.js meets them
    # only after the content). Not first in the body either: paged.js breaks
    # before an element only when something precedes it, and a `<style>`
    # counts -- every part began with a blank page.
    return (
        '<!DOCTYPE html><html lang="en"><head>'
        + plan.head
        + AFTER_PAGED_STYLE
        + plan.styles
        + "</head><body>"
        + body
        + "</body></html>"
    )


def split_part(part: Part) -> list[Part]:
    """Two halves of `part`, by units, never between glued units. A part
    that cannot be cut comes back whole."""
    cuts = [i for i in range(1, len(part.units)) if not part.units[i - 1].glue_next]
    if not cuts:
        return [part]
    half = min(cuts, key=lambda i: abs(i - len(part.units) / 2))
    return [Part(part.units[:half], front=part.front), Part(part.units[half:], front=part.front)]


def needs_total(marks) -> bool:
    """Whether any header or footer shows the whole document's page count."""
    from connections_export.pdf.marks import Marks  # noqa: PLC0415

    marks = marks or Marks()
    fields = (
        marks.header_left,
        marks.header_center,
        marks.header_right,
        marks.footer_left,
        marks.footer_center,
        marks.footer_right,
        marks.header_html,
        marks.footer_html,
    )
    return any("{pages}" in (value or "") for value in fields)


# --- rendering -----------------------------------------------------------


@dataclass
class _Rendered:
    path: Path
    pages: int
    #: id -> (page index within the part, x px, y px)
    positions: dict[str, list]
    outline: list[dict]
    end_container: str
    end_item: str


class _Session:
    """One browser for the whole run, relaunched when it dies."""

    def __init__(self, playwright, *, served, pdf_timeout: float) -> None:
        self.playwright = playwright
        self.served = served
        self.pdf_timeout = pdf_timeout
        self.browser = None

    def page(self):
        from connections_export.pdf import paged  # noqa: PLC0415

        if self.browser is None or not self.browser.is_connected():
            self.relaunch()
        return paged.PrintPage(self.browser, served=self.served, pdf_timeout=self.pdf_timeout)

    def relaunch(self) -> None:
        from connections_export.pdf import paged  # noqa: PLC0415

        self.close()
        self.browser = paged._launch(self.playwright)

    def close(self) -> None:
        if self.browser is not None:
            with contextlib.suppress(Exception):
                self.browser.close()
            self.browser = None


class _Run:
    def __init__(
        self,
        plan: Plan,
        *,
        session: _Session,
        marks,
        generated_at: str,
        pdf_timeout: float,
        report: ProgressCallback,
        workdir: Path,
    ) -> None:
        self.plan = plan
        self.session = session
        self.marks = marks
        self.generated_at = generated_at
        self.pdf_timeout = pdf_timeout
        self.report = report
        self.workdir = workdir
        self.total: int | None = None
        self.done = 0
        self.counter = 0

    @property
    def parts(self) -> int:
        return len(self.plan.order)

    def number(self, part: Part) -> int:
        """`part`'s place in the document, 1-based."""
        order = self.plan.order
        return next(i for i, p in enumerate(order, 1) if p is part)

    # -- one part, with out-of-memory recovery ----------------------------

    def _attempt(self, part: Part, action):
        guard = self.session.page()
        try:
            return action(guard)
        except PdfOutOfMemoryError:
            raise
        except Exception as exc:
            error = guard.error(exc, what=part.label)
            if isinstance(error, PdfOutOfMemoryError):
                # The renderer died; the browser may have gone with it.
                self.session.relaunch()
            raise error from exc
        finally:
            guard.close()

    def _split_or_raise(self, parts: list[Part], part: Part, exc: PdfOutOfMemoryError) -> None:
        halves = split_part(part)
        if len(halves) < 2:
            raise PdfOutOfMemoryError(
                f"{part.label[:1].upper()}{part.label[1:]} is too large to lay out with the"
                " memory available, even on its own. " + _advice()
            ) from exc
        index = next(i for i, p in enumerate(parts) if p is part)
        parts[index : index + 1] = halves
        self.report(
            PdfProgress(
                stage="retrying",
                part=index + 1,
                parts=self.parts,
                pages_done=self.done,
                pages_total=self.total,
                message=(
                    "The browser ran out of memory; trying again in smaller parts"
                    f" ({self.parts} now)."
                ),
            )
        )

    # -- measuring --------------------------------------------------------

    def measure_all(self, parts: list[Part]) -> int:
        """Lay out each part once, layout only, and return the page count."""
        total = 0
        i = 0
        while i < len(parts):
            part = parts[i]
            try:
                part.pages = self._attempt(part, lambda g, p=part, t=total: self._measure(g, p, t))
            except PdfOutOfMemoryError as exc:
                self._split_or_raise(parts, part, exc)
                continue
            total += part.pages
            i += 1
        return total

    def _measure(self, guard, part: Part, so_far: int) -> int:
        from connections_export.pdf import paged  # noqa: PLC0415

        n = self.number(part)
        self.report(
            PdfProgress(
                stage="measuring",
                part=n,
                parts=self.parts,
                message=f"Counting pages: part {n} of {self.parts} ({so_far:,} pages so far)…",
            )
        )
        return paged.lay_out(
            guard.page,
            part_document(self.plan, part),
            pdf_timeout=self.pdf_timeout,
            numbers_from_attribute=True,
        )

    # -- rendering --------------------------------------------------------

    def render_all(
        self,
        parts: list[Part],
        *,
        offset: int,
        pages_of: dict[str, int],
        start: tuple[str, str] = ("", ""),
    ) -> list[_Rendered]:
        """Render `parts` in order from page `offset` (0-based), each part
        starting with the running names the previous one ended with.

        A measured part must come out the length it was measured at, or every
        page number after it would be wrong; a part split after running out
        of memory is checked by the sum of all of them."""
        measured = all(p.pages is not None for p in parts)
        expected_sum = sum(p.pages or 0 for p in parts)
        rendered: list[_Rendered] = []
        container, item = start
        i = 0
        while i < len(parts):
            part = parts[i]
            expected = part.pages
            try:
                result = self._attempt(
                    part,
                    lambda g, p=part, o=offset, c=container, t=item: self._render(
                        g, p, offset=o, start=(c, t), pages_of=pages_of
                    ),
                )
            except PdfOutOfMemoryError as exc:
                self._split_or_raise(parts, part, exc)
                parts[i].pages = parts[i + 1].pages = None
                continue
            if expected is not None and result.pages != expected:
                raise RuntimeError(_LENGTH_CHANGED.format(n=self.number(part)))
            part.pages = result.pages
            rendered.append(result)
            offset += result.pages
            container, item = result.end_container, result.end_item
            i += 1
        if measured and sum(r.pages for r in rendered) != expected_sum:
            raise RuntimeError(_LENGTH_CHANGED.format(n="split after running out of memory,"))
        return rendered

    def _render(self, guard, part: Part, *, offset: int, start, pages_of) -> _Rendered:
        from connections_export.pdf import paged  # noqa: PLC0415

        n = self.number(part)
        page = guard.page
        done_before = self.done

        def on_page(count: int) -> None:
            self.report(
                PdfProgress(
                    stage="laying_out",
                    part=n,
                    parts=self.parts,
                    pages_done=done_before + count,
                    pages_total=self.total,
                    message=f"Laying out part {n} of {self.parts}: page {offset + count:,}"
                    + (f" of {self.total:,}" if self.total else "")
                    + "…",
                )
            )

        on_page(0)
        pages = paged.lay_out(
            page,
            part_document(self.plan, part),
            pdf_timeout=self.pdf_timeout,
            numbers_from_attribute=True,
            on_page=on_page,
            before_layout=(_FILL_PAGES_JS, pages_of),
        )
        stamp = page.evaluate(
            paged._STAMP_JS,
            paged._stamp_args(
                self.marks,
                self.generated_at,
                page_offset=offset,
                pages_total=self.total,
                start_container=start[0],
                start_item=start[1],
            ),
        )
        outline = page.evaluate(paged._OUTLINE_JS)
        positions = page.evaluate(
            _POSITIONS_JS, sorted(self.plan.targets | self.plan.extra_targets)
        )
        self.report(
            PdfProgress(
                stage="printing",
                part=n,
                parts=self.parts,
                pages_done=done_before + pages,
                pages_total=self.total,
                message=f"Writing part {n} of {self.parts} to PDF"
                f" (pages {offset + 1:,}–{offset + pages:,})…",
            )
        )
        self.counter += 1
        path = self.workdir / f"part-{self.counter:05d}.pdf"
        paged.print_pdf(page, pdf_timeout=self.pdf_timeout, pages=pages, path=str(path))
        self.done = done_before + pages
        return _Rendered(
            path=path,
            pages=pages,
            positions=positions,
            outline=[{**e, "page": e["page"] + offset} for e in outline],
            end_container=stamp.get("container", ""),
            end_item=stamp.get("item", ""),
        )


def _advice() -> str:
    from connections_export.pdf.paged import OUT_OF_MEMORY_ADVICE  # noqa: PLC0415

    return OUT_OF_MEMORY_ADVICE


# --- joining -------------------------------------------------------------


def _offsets(rendered: list[_Rendered], start: int) -> list[int]:
    offsets = []
    for part in rendered:
        offsets.append(start)
        start += part.pages
    return offsets


def _page_numbers(positions: dict[str, tuple]) -> dict[str, int]:
    """Target id -> its 1-based page number in the whole document."""
    return {target: index + 1 for target, (index, _x, _y) in positions.items()}


def _absolute_positions(rendered: list[_Rendered], offsets: list[int]) -> dict[str, tuple]:
    positions: dict[str, tuple] = {}
    for part, offset in zip(rendered, offsets, strict=True):
        for target, (page, x, y) in part.positions.items():
            if page >= 0:
                positions.setdefault(target, (offset + page, x, y))
    return positions


def placeholder_links(positions: dict[str, tuple]):
    """The annotation rewriter for joined parts: each placeholder becomes a
    jump to where its target landed; one into another file of a set is left
    for `pdf.sets`; one whose target is not in the document goes inert."""
    from connections_export.pdf.join import link_uri, link_with  # noqa: PLC0415

    def rewrite(body: bytes, pages) -> bytes | None:
        uri = link_uri(body)
        if uri is None or not uri.startswith(LINK_PREFIX):
            return None
        name = unquote(uri[len(LINK_PREFIX) :])
        if name.startswith(REMOTE_TARGET):
            return None
        target = positions.get(name)
        if target is None:
            return b""
        index, x, y = target
        top = pages.height(index) - y * _PX_TO_PT
        return link_with(
            body,
            b"/Dest [" + pages.ref(index) + b" /XYZ %.2f %.2f 0]" % (x * _PX_TO_PT, top),
        )

    return rewrite


def join_parts(
    rendered: list[_Rendered],
    *,
    positions: dict[str, tuple],
    outline: list[dict],
    open_panel: bool,
    output: Path,
    attachments=(),
) -> None:
    """One PDF from the parts, written to `output`: every placeholder link a
    jump to its target, the bookmarks of the whole, the attachments.
    Streaming (`pdf.join`); pypdf only for a part the joiner cannot copy."""
    from connections_export.pdf import join  # noqa: PLC0415

    try:
        join.join(
            [part.path for part in rendered],
            output,
            rewrite_annot=placeholder_links(positions),
            outline=[join.Bookmark(e["title"], e["level"], e["page"]) for e in outline],
            open_outline=open_panel,
            attachments=attachments,
        )
    except join.NotSimple:
        _join_with_pypdf(rendered, positions, outline, open_panel, output, attachments)


def _join_with_pypdf(rendered, positions, outline, open_panel, output, attachments) -> None:
    """The general (memory-hungry) way, for parts in a shape the streaming
    joiner does not copy."""
    from pypdf import PdfWriter  # noqa: PLC0415
    from pypdf.generic import ArrayObject, FloatObject, NameObject, NumberObject  # noqa: PLC0415

    from connections_export.pdf.paged import add_outline  # noqa: PLC0415

    writer = PdfWriter()
    for part in rendered:
        writer.append(str(part.path), import_outline=False)
    pages = writer.pages
    for page in pages:
        annots = page.get("/Annots")
        if annots is None:
            continue
        keep = ArrayObject()
        for ref in annots.get_object():
            annot = ref.get_object()
            action = annot.get("/A")
            action = action.get_object() if action is not None else None
            uri = action.get("/URI") if action is not None else None
            if not (isinstance(uri, str) and uri.startswith(LINK_PREFIX)):
                keep.append(ref)
                continue
            name = unquote(uri[len(LINK_PREFIX) :])
            if name.startswith(REMOTE_TARGET):
                keep.append(ref)
                continue
            target = positions.get(name)
            if target is None:
                continue
            index, x, y = target
            dest_page = pages[index]
            height = float(dest_page.mediabox.height)
            del annot["/A"]
            annot[NameObject("/Dest")] = ArrayObject(
                [
                    dest_page.indirect_reference,
                    NameObject("/XYZ"),
                    FloatObject(round(x * _PX_TO_PT, 2)),
                    FloatObject(round(height - y * _PX_TO_PT, 2)),
                    NumberObject(0),
                ]
            )
            keep.append(ref)
        page[NameObject("/Annots")] = keep
    add_outline(writer, outline, open_panel=open_panel)
    for name, load in attachments:
        data = load()
        if data is not None:
            writer.add_attachment(name, data)
    with output.open("wb") as out:
        writer.write(out)


# --- the whole run -------------------------------------------------------


@contextlib.contextmanager
def _workdir() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="connections-export-pdf-") as name:
        yield Path(name)


def render_in_parts(
    plan: Plan,
    *,
    served: ServedImages | None,
    marks=None,
    generated_at: str = "",
    pdf_timeout: float = 120.0,
    open_bookmarks: bool = False,
    progress: ProgressCallback | None = None,
    positions_out: dict | None = None,
    output: Path | None = None,
    attachments=(),
) -> bytes | None:
    """Render `plan` part by part and join the parts into one PDF, written to
    `output` (then None is returned) or returned as bytes. `positions_out`
    receives where each target landed: id -> (page index, x px, y px from the
    page's top-left). `attachments` are `(name, load)` pairs attached to it."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    report = progress or ignore
    with sync_playwright() as playwright, _workdir() as workdir:
        session = _Session(playwright, served=served, pdf_timeout=pdf_timeout)
        try:
            run = _Run(
                plan,
                session=session,
                marks=marks,
                generated_at=generated_at,
                pdf_timeout=pdf_timeout,
                report=report,
                workdir=workdir,
            )
            # The contents come first in the document but are printed last,
            # once every page number is known; their length is needed now.
            front_pages = run.measure_all(plan.front)
            if needs_total(marks):
                run.total = (
                    front_pages + run.measure_all(plan.body) + run.measure_all(plan.external)
                )
            run.done = 0
            # The external-content list points into every other part, so it is
            # rendered after them, once their page numbers are known.
            body = run.render_all(plan.body, offset=front_pages, pages_of={})
            body_offsets = _offsets(body, front_pages)
            pages_of = _page_numbers(_absolute_positions(body, body_offsets))
            if plan.external:
                at = body_offsets[-1] + body[-1].pages if body else front_pages
                start = (body[-1].end_container, body[-1].end_item) if body else ("", "")
                body += run.render_all(plan.external, offset=at, pages_of=pages_of, start=start)
                body_offsets = _offsets(body, front_pages)
                pages_of = _page_numbers(_absolute_positions(body, body_offsets))
            expected_front = [p.pages for p in plan.front]
            front = run.render_all(plan.front, offset=0, pages_of=pages_of)
            if [p.pages for p in front] != expected_front:
                raise RuntimeError(
                    "The table of contents laid out to a different length once its page"
                    " numbers were filled in; the export stops rather than number pages wrongly."
                )
            rendered = front + body
            offsets = _offsets(rendered, 0)
            at = sum(part.pages for part in rendered)
            positions = _absolute_positions(rendered, offsets)
            if positions_out is not None:
                positions_out.update(positions)
            outline = [e for part in rendered for e in part.outline]
            report(
                PdfProgress(
                    stage="assembling",
                    part=run.parts,
                    parts=run.parts,
                    pages_done=at,
                    pages_total=at,
                    message=f"Joining {run.parts} parts into one PDF of {at:,} pages…",
                )
            )
            target = output if output is not None else workdir / "joined.pdf"
            join_parts(
                rendered,
                positions=positions,
                outline=outline,
                open_panel=open_bookmarks,
                output=target,
                attachments=attachments,
            )
            report(
                PdfProgress(
                    stage="done",
                    part=run.parts,
                    parts=run.parts,
                    pages_done=at,
                    pages_total=at,
                    message=f"Done: {at:,} pages in {run.parts} parts.",
                )
            )
            return None if output is not None else target.read_bytes()
        finally:
            session.close()
