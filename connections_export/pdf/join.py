"""Joining PDFs without holding them: a streaming joiner for Chromium's output.

General PDF libraries copy every object into memory to write it out again:
joining 412 MB of parts took 858 MB with pypdf and 449 MB with pikepdf/qpdf --
memory that grows with the PDF, which is exactly what a large archive cannot
afford. This joiner does not have to be general. It joins files from one
producer, Chromium (Skia), which writes plain PDF 1.4: objects one after
another, a classic cross-reference table, no compressed object streams, no
incremental updates. So every object can be copied byte for byte -- stream
data (images, page content) in fixed-size chunks, from the part file straight
to the output -- and only the small dictionaries are rewritten: references
renumbered, the page tree, catalog and bookmarks made new, link annotations
turned into jumps. Memory stays at a few megabytes per thousand pages whatever
the size of the result.

A part that is not in that shape (an object stream, a cross-reference stream,
an incremental update, encryption) raises `NotSimple`; callers fall back to
pypdf for it.

The output is checked against pypdf's join page for page -- text, link
targets, bookmarks -- in tests/pdf/test_streaming_join.py.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

CHUNK = 1 << 20

#: Page attributes a page inherits from its ancestors in the page tree.
_INHERITABLE = (b"/Resources", b"/MediaBox", b"/CropBox", b"/Rotate")


class NotSimple(Exception):
    """The file uses PDF features this joiner does not copy."""


# --- reading one part --------------------------------------------------------


@dataclass
class _Part:
    path: Path
    #: object number -> byte offset of "N G obj"
    offsets: dict[int, int]
    root: int
    info: int | None
    size: int


_STARTXREF = re.compile(rb"startxref\s+(\d+)\s+%%EOF\s*$")


def _read_part(path: Path) -> _Part:
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 2048))
        tail = f.read()
        match = _STARTXREF.search(tail)
        if not match:
            raise NotSimple(f"{path.name}: no startxref")
        f.seek(int(match.group(1)))
        if f.read(4) != b"xref":
            raise NotSimple(f"{path.name}: cross-reference stream")
        offsets: dict[int, int] = {}
        line = f.readline()  # rest of "xref" line
        while True:
            line = f.readline()
            if not line:
                raise NotSimple(f"{path.name}: truncated xref")
            line = line.strip()
            if line.startswith(b"trailer"):
                break
            if not line:
                continue
            start, count = (int(x) for x in line.split())
            for n in range(start, start + count):
                entry = f.read(20)
                if entry[17:18] == b"n":
                    offsets[n] = int(entry[:10])
        trailer = line[len(b"trailer") :] + f.read(4096)
        trailer = trailer[: trailer.find(b">>", trailer.rfind(b"<<")) + 2]
    if b"/Prev" in trailer or b"/Encrypt" in trailer or b"/XRefStm" in trailer:
        raise NotSimple(f"{path.name}: incremental update or encryption")
    root = re.search(rb"/Root\s+(\d+)\s+\d+\s+R", trailer)
    info = re.search(rb"/Info\s+(\d+)\s+\d+\s+R", trailer)
    if not root:
        raise NotSimple(f"{path.name}: no /Root")
    return _Part(
        path,
        offsets,
        int(root.group(1)),
        int(info.group(1)) if info else None,
        size,
    )


@dataclass
class _Obj:
    """One object: its dictionary/value text, and where its stream data is."""

    body: bytes
    stream_at: int | None = None
    stream_len: int = 0


_OBJ_HEAD = re.compile(rb"\s*(\d+)\s+(\d+)\s+obj")


def _read_obj(f, part: _Part, num: int, cache: dict | None = None) -> _Obj:
    offset = part.offsets[num]
    f.seek(offset)
    head = f.read(64)
    match = _OBJ_HEAD.match(head)
    if not match or int(match.group(1)) != num:
        raise NotSimple(f"{part.path.name}: object {num} not at its offset")
    start = offset + match.end()
    f.seek(start)
    # The value: read until "stream" or "endobj" at the top level.
    buf = b""
    while True:
        chunk = f.read(65536)
        if not chunk:
            raise NotSimple(f"{part.path.name}: object {num} unterminated")
        buf += chunk
        end = _value_end(buf)
        if end is not None:
            break
        if len(buf) > 64 * CHUNK:
            raise NotSimple(f"{part.path.name}: object {num} too large a dictionary")
    value_end, kind = end
    body = buf[:value_end].strip()
    if kind == b"endobj":
        if b"/Type /ObjStm" in body or b"/Type/ObjStm" in body:
            raise NotSimple(f"{part.path.name}: object stream")
        return _Obj(body)
    # stream: data starts after "stream" + EOL
    after = value_end + len(b"stream")
    if buf[after : after + 2] == b"\r\n":
        after += 2
    elif buf[after : after + 1] in (b"\n", b"\r"):
        after += 1
    length = _stream_length(body, f, part, cache)
    if b"/Type /ObjStm" in body or b"/Type/ObjStm" in body or b"/Type /XRef" in body:
        raise NotSimple(f"{part.path.name}: object or xref stream")
    return _Obj(body, stream_at=start + after, stream_len=length)


def _value_end(buf: bytes):
    """Index where the object's value ends (before 'stream' or 'endobj')."""
    i = 0
    n = len(buf)
    depth = 0
    while i < n:
        c = buf[i : i + 1]
        if c == b"(":
            j = _skip_string(buf, i)
            if j is None:
                return None
            i = j
            continue
        if c == b"%":
            j = buf.find(b"\n", i)
            if j < 0:
                return None
            i = j + 1
            continue
        if buf.startswith(b"<<", i) or c == b"[":
            depth += 1
            i += 2 if c == b"<" else 1
            continue
        if buf.startswith(b">>", i) or c == b"]":
            depth -= 1
            i += 2 if c == b">" else 1
            continue
        if c == b"<":  # hex string
            j = buf.find(b">", i)
            if j < 0:
                return None
            i = j + 1
            continue
        if depth == 0:
            if buf.startswith(b"stream", i) and not buf.startswith(b"streamx", i):
                return i, b"stream"
            if buf.startswith(b"endobj", i):
                return i, b"endobj"
        i += 1
    return None


def _skip_string(buf: bytes, i: int) -> int | None:
    """Index after the literal string starting at `buf[i] == '('`."""
    depth = 0
    n = len(buf)
    while i < n:
        c = buf[i]
        if c == 0x5C:  # backslash
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


_LENGTH = re.compile(rb"/Length\s+(\d+)(\s+(\d+)\s+R)?")


def _stream_length(body: bytes, f, part: _Part, cache) -> int:
    match = _LENGTH.search(body)
    if not match:
        raise NotSimple(f"{part.path.name}: stream without /Length")
    if not match.group(2):
        return int(match.group(1))
    ref = int(match.group(1))
    here = f.tell()
    value = _read_obj(f, part, ref, cache).body
    f.seek(here)
    return int(value.strip())


# --- rewriting references ---------------------------------------------------

_REF = re.compile(rb"(\d+)\s+(\d+)\s+R(?![A-Za-z0-9])")


def _renumber(body: bytes, mapping: Callable[[int], int]) -> bytes:
    """`body` with every indirect reference outside strings renumbered."""
    out = []
    i = 0
    n = len(body)
    last = 0
    while i < n:
        c = body[i : i + 1]
        if c == b"(":
            j = _skip_string(body, i) or n
            out.append(_REF.sub(lambda m: _sub(m, mapping), body[last:i]))
            out.append(body[i:j])
            last = i = j
            continue
        if c == b"<" and not body.startswith(b"<<", i):
            j = body.find(b">", i)
            j = n if j < 0 else j + 1
            out.append(_REF.sub(lambda m: _sub(m, mapping), body[last:i]))
            out.append(body[i:j])
            last = i = j
            continue
        if c == b"<":
            i += 2
            continue
        i += 1
    out.append(_REF.sub(lambda m: _sub(m, mapping), body[last:]))
    return b"".join(out)


def _sub(match, mapping) -> bytes:
    return b"%d 0 R" % mapping(int(match.group(1)))


def _refs(body: bytes) -> list[int]:
    found = []
    _renumber(body, lambda n: found.append(n) or n)
    return found


#: A name token ends at whitespace or a delimiter -- so `/Length` is not
#: `/Length1` (a font stream carries both).
_NAME_END = rb"(?=[\s/<>\[\]()%]|$)"


def _dict_value(body: bytes, key: bytes) -> bytes | None:
    """The raw value of `key` in the top-level dictionary `body`, roughly."""
    match = re.search(re.escape(key) + _NAME_END + rb"\s*", body)
    if not match:
        return None
    i = match.end()
    if body.startswith(b"<<", i) or body[i : i + 1] == b"[":
        depth = 0
        j = i
        while j < len(body):
            if body[j : j + 1] == b"(":
                j = _skip_string(body, j) or len(body)
                continue
            if body.startswith(b"<<", j) or body[j : j + 1] == b"[":
                depth += 1
                j += 2 if body[j : j + 1] == b"<" else 1
                continue
            if body.startswith(b">>", j) or body[j : j + 1] == b"]":
                depth -= 1
                j += 2 if body[j : j + 1] == b">" else 1
                if depth == 0:
                    return body[i:j]
                continue
            j += 1
        return body[i:]
    ref = re.match(rb"(\d+)\s+(\d+)\s+R", body[i:])
    if ref:
        return ref.group(0)
    if body[i : i + 1] == b"(":
        j = _skip_string(body, i) or len(body)
        return body[i:j]
    token = re.match(rb"[^\s/<>\[\]()]+|/[^\s/<>\[\]()]+", body[i:])
    return token.group(0) if token else None


def _drop_key(body: bytes, key: bytes) -> bytes:
    value = _dict_value(body, key)
    if value is None:
        return body
    match = re.search(re.escape(key) + _NAME_END + rb"\s*" + re.escape(value), body)
    return body[: match.start()] + body[match.end() :] if match else body


def _set_key(body: bytes, key: bytes, value: bytes) -> bytes:
    body = _drop_key(body, key)
    end = body.rfind(b">>")
    return body[:end] + key + b" " + value + b"\n" + body[end:]


# --- writing ---------------------------------------------------------------


def pdf_string(text: str) -> bytes:
    """A PDF text string: PDFDocEncoding-safe ASCII, else UTF-16BE with BOM."""
    try:
        raw = text.encode("ascii")
        return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"
    except UnicodeEncodeError:
        return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode() + b">"


@dataclass
class _Writer:
    out: object
    offsets: dict[int, int] = field(default_factory=dict)
    next_num: int = 1

    def reserve(self) -> int:
        num = self.next_num
        self.next_num += 1
        return num

    def write_obj(self, num: int, body: bytes) -> None:
        self.offsets[num] = self.out.tell()
        self.out.write(b"%d 0 obj\n" % num + body + b"\nendobj\n")

    def write_stream(self, num: int, body: bytes, src, at: int, length: int) -> None:
        self.offsets[num] = self.out.tell()
        body = _set_key(body, b"/Length", b"%d" % length)
        self.out.write(b"%d 0 obj\n" % num + body + b"\nstream\n")
        src.seek(at)
        left = length
        while left:
            chunk = src.read(min(CHUNK, left))
            if not chunk:
                raise NotSimple("stream shorter than its /Length")
            self.out.write(chunk)
            left -= len(chunk)
        self.out.write(b"\nendstream\nendobj\n")

    def write_data_stream(self, num: int, body: bytes, data: bytes) -> None:
        self.offsets[num] = self.out.tell()
        body = _set_key(body, b"/Length", b"%d" % len(data))
        self.out.write(b"%d 0 obj\n" % num + body + b"\nstream\n" + data + b"\nendstream\nendobj\n")

    def finish(self, root: int, info: int | None) -> None:
        start = self.out.tell()
        size = self.next_num
        self.out.write(b"xref\n0 %d\n0000000000 65535 f \n" % size)
        for num in range(1, size):
            off = self.offsets.get(num)
            if off is None:
                self.out.write(b"0000000000 65535 f \n")
            else:
                self.out.write(b"%010d 00000 n \n" % off)
        trailer = b"trailer\n<</Size %d /Root %d 0 R" % (size, root)
        if info:
            trailer += b" /Info %d 0 R" % info
        self.out.write(trailer + b">>\nstartxref\n%d\n%%%%EOF\n" % start)


@dataclass(frozen=True)
class Pages:
    """What an annotation rewriter needs to point at a page of the output."""

    refs: list[int]
    heights: list[float]

    def ref(self, index: int) -> bytes:
        return b"%d 0 R" % self.refs[index]

    def height(self, index: int) -> float:
        return self.heights[index]


#: An annotation rewriter: given a link annotation's dictionary text and the
#: output's pages, return the new dictionary text, None to leave it as it is,
#: or b"" to make it inert (a link to nowhere is worse than plain text).
AnnotRewrite = Callable[[bytes, Pages], bytes | None]


_URI = re.compile(rb"/URI\s*\(((?:\\.|[^\\)])*)\)")


def link_uri(body: bytes) -> str | None:
    """The URI a link annotation opens, if it is a plain ASCII one."""
    match = _URI.search(body)
    if not match:
        return None
    raw = re.sub(rb"\\(.)", rb"\1", match.group(1))
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return None


def link_with(body: bytes, action: bytes) -> bytes:
    """A link annotation with the same rectangle, doing `action` (the text of
    `/Dest [...]` or `/A <<...>>`)."""
    rect = _dict_value(body, b"/Rect") or b"[0 0 0 0]"
    return b"<</Type /Annot /Subtype /Link /Rect " + rect + b" /Border [0 0 0] " + action + b">>"


@dataclass
class Bookmark:
    title: str
    level: int
    page: int


def join(
    parts: Iterable[Path],
    out_path: Path,
    *,
    rewrite_annot: AnnotRewrite | None = None,
    outline: list[Bookmark] | None = None,
    open_outline: bool = False,
    attachments: Iterable[tuple[str, Callable[[], bytes | None]]] = (),
) -> int:
    """Join `parts` (in order) into `out_path`, streaming. Returns the page
    count. Raises `NotSimple` if a part needs a general library."""
    parts = [_read_part(Path(p)) for p in parts]
    with out_path.open("wb") as out:
        out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        w = _Writer(out)
        pages_root = w.reserve()
        catalog = w.reserve()
        # First pass per part: the pages in order, with their inherited keys,
        # and the part's named destinations (Chromium's same-document links).
        part_pages: list[list[tuple[int, dict[bytes, bytes]]]] = []
        part_dests: list[dict[bytes, tuple[int, bytes]]] = []
        heights: list[float] = []
        for part in parts:
            with part.path.open("rb") as f:
                cat = _read_obj(f, part, part.root).body
                ref = _dict_value(cat, b"/Pages")
                if not ref:
                    raise NotSimple(f"{part.path.name}: no /Pages")
                if _dict_value(cat, b"/Names") is not None:
                    raise NotSimple(f"{part.path.name}: a /Names tree")
                leaves: list[tuple[int, dict[bytes, bytes]]] = []
                _walk_pages(f, part, int(ref.split()[0]), {}, leaves)
                part_pages.append(leaves)
                for num, inherited in leaves:
                    box = _dict_value(_read_obj(f, part, num).body, b"/MediaBox")
                    box = box or inherited.get(b"/MediaBox") or b"[0 0 612 792]"
                    heights.append(float(box.strip(b"[]").split()[3]))
                part_dests.append(_named_dests(f, part, cat))
        # Numbers for every output page, in order (needed by link rewriting).
        page_refs: list[int] = []
        maps: list[dict[int, int]] = []
        for leaves in part_pages:
            mapping: dict[int, int] = {}
            for num, _inh in leaves:
                mapping[num] = w.reserve()
                page_refs.append(mapping[num])
            maps.append(mapping)

        pages = Pages(page_refs, heights)
        first_page = []
        at = 0
        for leaves in part_pages:
            first_page.append(at)
            at += len(leaves)

        info_num = None
        for p_index, part in enumerate(parts):
            mapping = maps[p_index]
            leaves = dict(part_pages[p_index])
            # Objects the part's own page tree and catalog are made of are
            # not copied: the output has its own.
            skip = {part.root}
            with part.path.open("rb") as f:
                _collect_tree_nodes(f, part, part.root, skip, set(leaves))

                def number(n: int, mapping=mapping) -> int:
                    if n not in mapping:
                        mapping[n] = w.reserve()
                    return mapping[n]

                for num in sorted(part.offsets):
                    if num in skip:
                        continue
                    if p_index > 0 and num == part.info:
                        continue
                    obj = _read_obj(f, part, num)
                    body = obj.body
                    if num in leaves:
                        for key, value in leaves[num].items():
                            if _dict_value(body, key) is None:
                                body = _set_key(body, key, value)
                        # The part's references first, then the output's own.
                        body = _renumber(body, number)
                        body = _set_key(body, b"/Parent", b"%d 0 R" % pages_root)
                    elif obj.stream_at is None and b"/Annot" in body and b"/Link" in body:
                        # A rewritten link is built from scratch with OUTPUT
                        # references (and a rectangle, which holds none), so it
                        # is not renumbered again; an untouched one is.
                        new = _resolve_named(
                            body,
                            part_dests[p_index],
                            part_pages[p_index],
                            first_page[p_index],
                            pages,
                        )
                        if new is body and rewrite_annot is not None:
                            new = rewrite_annot(body, pages)
                            if new == b"":
                                new = link_with(body, b"")
                        body = _renumber(body, number) if new is None or new is body else new
                    else:
                        body = _renumber(body, number)
                    target = number(num)
                    if p_index == 0 and num == part.info:
                        info_num = target
                    if obj.stream_at is None:
                        w.write_obj(target, body)
                    else:
                        w.write_stream(target, body, f, obj.stream_at, obj.stream_len)
        kids = b" ".join(b"%d 0 R" % n for n in page_refs)
        w.write_obj(pages_root, b"<</Type /Pages /Kids [%s] /Count %d>>" % (kids, len(page_refs)))
        extras = b""
        if outline:
            extras += b" /Outlines %d 0 R" % _write_outline(w, outline, page_refs)
            if open_outline:
                extras += b" /PageMode /UseOutlines"
        names = _write_attachments(w, attachments)
        if names:
            extras += b" /Names <</EmbeddedFiles %d 0 R>>" % names
        w.write_obj(catalog, b"<</Type /Catalog /Pages %d 0 R%s>>" % (pages_root, extras))
        w.finish(catalog, info_num)
    return len(page_refs)


def _named_dests(f, part: _Part, catalog: bytes) -> dict[bytes, tuple[int, bytes]]:
    """The catalog's `/Dests` dictionary (PDF 1.1 style, as Chromium writes
    it): name -> (the part's page object number, the rest of the array)."""
    value = _dict_value(catalog, b"/Dests")
    if value is None:
        return {}
    if value.endswith(b"R"):
        value = _read_obj(f, part, int(value.split()[0])).body
    dests: dict[bytes, tuple[int, bytes]] = {}
    for match in re.finditer(rb"/([^\s/<>\[\]()]+)\s*\[\s*(\d+)\s+\d+\s+R([^\]]*)\]", value):
        dests[match.group(1)] = (int(match.group(2)), match.group(3))
    return dests


def _resolve_named(body: bytes, dests, leaves, first: int, pages: Pages) -> bytes:
    """A link to a named destination, pointed at its page directly -- the
    names live in the part's catalog, which the output does not copy."""
    match = re.search(rb"/Dest\s*/([^\s/<>\[\]()]+)", body)
    if not match:
        return body
    target = dests.get(match.group(1))
    if target is None:
        return link_with(body, b"")
    page_num, rest = target
    index = next((i for i, (num, _inh) in enumerate(leaves) if num == page_num), None)
    if index is None:
        return link_with(body, b"")
    return link_with(body, b"/Dest [" + pages.ref(first + index) + rest + b"]")


def _walk_pages(f, part: _Part, num: int, inherited: dict, leaves: list) -> None:
    body = _read_obj(f, part, num).body
    if b"/Type /Pages" in body or b"/Type/Pages" in body:
        here = dict(inherited)
        for key in _INHERITABLE:
            value = _dict_value(body, key)
            if value is not None:
                here[key] = value
        kids = _dict_value(body, b"/Kids") or b"[]"
        for kid in _refs(kids):
            _walk_pages(f, part, kid, here, leaves)
    else:
        leaves.append((num, inherited))


def _collect_tree_nodes(f, part: _Part, root: int, skip: set, leaves: set) -> None:
    """The catalog's /Pages tree nodes (not the leaf pages) go into `skip`."""
    cat = _read_obj(f, part, root).body
    ref = _dict_value(cat, b"/Pages")
    stack = [int(ref.split()[0])] if ref else []
    # Chromium's own outline and names are rebuilt, never copied.
    for key in (b"/Outlines", b"/Dests"):
        value = _dict_value(cat, key)
        if value and value.endswith(b"R"):
            stack_outline = [int(value.split()[0])]
            while stack_outline:
                n = stack_outline.pop()
                if n in skip or n not in part.offsets:
                    continue
                skip.add(n)
                if key == b"/Outlines":
                    body = _read_obj(f, part, n).body
                    for k in (b"/First", b"/Next"):
                        v = _dict_value(body, k)
                        if v and v.endswith(b"R"):
                            stack_outline.append(int(v.split()[0]))
    while stack:
        n = stack.pop()
        if n in leaves or n in skip:
            continue
        skip.add(n)
        body = _read_obj(f, part, n).body
        stack.extend(_refs(_dict_value(body, b"/Kids") or b"[]"))


def _write_outline(w: _Writer, entries: list[Bookmark], page_refs: list[int]) -> int:
    """Bookmarks nested by level, as `with_outline` nests them."""
    root = w.reserve()
    nodes = []  # (num, entry, parent_index)
    stack: list[tuple[int, int]] = []  # (level, node index)
    last = len(page_refs) - 1
    for entry in entries:
        level = max(1, int(entry.level))
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else None
        nodes.append([w.reserve(), entry, parent])
        stack.append((level, len(nodes) - 1))
    children: dict[int | None, list[int]] = {}
    for i, (_num, _e, parent) in enumerate(nodes):
        children.setdefault(parent, []).append(i)

    def count(i: int | None) -> int:
        return sum(1 + count(c) for c in children.get(i, []))

    for i, (num, entry, parent) in enumerate(nodes):
        siblings = children[parent]
        pos = siblings.index(i)
        parent_num = root if parent is None else nodes[parent][0]
        page = page_refs[min(max(int(entry.page), 0), last)]
        body = b"<</Title %s /Parent %d 0 R /Dest [%d 0 R /XYZ null null null]" % (
            pdf_string(entry.title),
            parent_num,
            page,
        )
        if pos > 0:
            body += b" /Prev %d 0 R" % nodes[siblings[pos - 1]][0]
        if pos + 1 < len(siblings):
            body += b" /Next %d 0 R" % nodes[siblings[pos + 1]][0]
        kids = children.get(i, [])
        if kids:
            # Closed by default, as pypdf writes them (negative count).
            body += b" /First %d 0 R /Last %d 0 R /Count -%d" % (
                nodes[kids[0]][0],
                nodes[kids[-1]][0],
                count(i),
            )
        w.write_obj(num, body + b">>")
    top = children.get(None, [])
    body = b"<</Type /Outlines /Count %d" % len(top)
    if top:
        body += b" /First %d 0 R /Last %d 0 R" % (nodes[top[0]][0], nodes[top[-1]][0])
    w.write_obj(root, body + b">>")
    return root


def _write_attachments(w: _Writer, attachments) -> int | None:
    """An EmbeddedFiles name tree, each file read and written one at a time."""
    entries = []
    for name, load in attachments:
        data = load()
        if data is None:
            continue
        stream = w.reserve()
        w.write_data_stream(stream, b"<</Type /EmbeddedFile>>", data)
        spec = w.reserve()
        title = pdf_string(name)
        w.write_obj(
            spec, b"<</Type /Filespec /F %s /UF %s /EF <</F %d 0 R>>>>" % (title, title, stream)
        )
        entries.append((name, spec))
    if not entries:
        return None
    entries.sort(key=lambda e: e[0].encode("utf-16-be"))
    tree = w.reserve()
    names = b" ".join(pdf_string(n) + b" %d 0 R" % s for n, s in entries)
    w.write_obj(tree, b"<</Names [%s]>>" % names)
    return tree


def rewrite_links(src: Path, out_path: Path, rewrite_annot: AnnotRewrite) -> int:
    """Copy `src` to `out_path` object for object -- same numbers, bookmarks,
    attachments and all -- rewriting only its link annotations. Streaming,
    like `join`. Returns how many links were rewritten."""
    part = _read_part(src)
    changed = 0
    with src.open("rb") as f, out_path.open("wb") as out:
        cat = _read_obj(f, part, part.root).body
        pages_ref = _dict_value(cat, b"/Pages")
        if not pages_ref:
            raise NotSimple(f"{src.name}: no /Pages")
        leaves: list[tuple[int, dict[bytes, bytes]]] = []
        _walk_pages(f, part, int(pages_ref.split()[0]), {}, leaves)
        heights = []
        for num, inherited in leaves:
            box = _dict_value(_read_obj(f, part, num).body, b"/MediaBox")
            box = box or inherited.get(b"/MediaBox") or b"[0 0 612 792]"
            heights.append(float(box.strip(b"[]").split()[3]))
        pages = Pages([num for num, _ in leaves], heights)
        out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        w = _Writer(out)
        w.next_num = max(part.offsets) + 1
        for num in sorted(part.offsets):
            obj = _read_obj(f, part, num)
            body = obj.body
            if obj.stream_at is None and b"/Annot" in body and b"/Link" in body:
                new = rewrite_annot(body, pages)
                if new is not None:
                    body = new if new != b"" else link_with(body, b"")
                    changed += 1
            if obj.stream_at is None:
                w.write_obj(num, body)
            else:
                w.write_stream(num, body, f, obj.stream_at, obj.stream_len)
        w.finish(part.root, part.info)
    return changed
