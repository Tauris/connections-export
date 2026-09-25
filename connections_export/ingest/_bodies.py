"""How an exporter writes a captured body: as Markdown, as HTML, or as both.

Markdown cannot say everything HTML can. A table with merged cells, a
coloured phrase, an image at a set width or a figure with a caption has no
Markdown spelling, and converting it anyway keeps the words and loses the
rest. Every exporter therefore takes an `html_mode`:

* ``markdown`` -- the whole body converted to Markdown, whatever that loses;
* ``mixed`` -- each top-level block converted to Markdown only when that is
  provably lossless, and written as cleaned HTML otherwise (below);
* ``html`` -- the whole body written as cleaned HTML;
* ``raw`` -- the whole body written exactly as captured, not cleaned. What
  it holds is then the publishing site's responsibility.

"Cleaned" is the PDF's allowlist cleaner (`pdf.html.clean_for_export`):
nothing that executes, submits or embeds another document survives it.

Proving a block lossless: the block is converted to Markdown, that Markdown
is rendered back to HTML with Python-Markdown, and both sides are reduced to
what a reader would see -- element structure, the attributes that change
the rendering, and text with its insignificant whitespace collapsed
(`canonical`). Equal means the Markdown shows exactly what the HTML showed,
so it is written; anything else and the cleaned HTML block is written
instead. Nothing is guessed from the tag names.

In every mode the exporter's template escaping still applies (Liquid for
Jekyll, shortcodes for Hugo): it renders back to exactly the same text, and
only stops the site generator executing what an author wrote. Links between
exported items and captured images are rewritten in every mode, inside HTML
blocks too, by the same sentinels the Markdown path uses.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote

import lxml.etree
import lxml.html
import markdown as _python_markdown

from connections_export.pdf.html import clean_for_export, drop_active_for_export

HTML_MODES = ("markdown", "mixed", "html", "raw")

#: Each format's default: Obsidian is a Markdown editor, where HTML is
#: awkward to edit; a Jekyll or Hugo site is a web page, where fidelity wins.
DEFAULT_HTML_MODE = {"obsidian": "markdown", "jekyll": "mixed", "hugo": "mixed"}


def html_mode_for(format_name: str, html_mode: str | None) -> str:
    """The mode to use: `html_mode` when given, else the format's default.
    Raises `ValueError` for a mode that does not exist."""
    mode = (html_mode or DEFAULT_HTML_MODE.get(format_name, "markdown")).strip().lower()
    if mode not in HTML_MODES:
        raise ValueError(f"html mode must be one of {', '.join(HTML_MODES)}; not {html_mode!r}")
    return mode


def html_mode_note(mode: str, markdown_blocks: int, html_blocks: int) -> str:
    """One Markdown paragraph for an export's README saying how its bodies
    were written -- and, for `raw`, whose responsibility they now are."""
    described = {
        "markdown": "converted to Markdown",
        "mixed": "converted to Markdown block by block where that loses nothing, "
        "and kept as cleaned HTML where it would",
        "html": "kept as cleaned HTML (scripts, event handlers and embedded documents removed)",
        "raw": "kept exactly as captured, NOT cleaned",
    }[mode]
    note = (
        f"> Page content ({mode!s} mode) was {described}: {markdown_blocks} block(s) "
        f"written as Markdown, {html_blocks} as HTML."
    )
    if mode == "raw":
        note += (
            " Raw HTML can carry scripts and anything else its authors wrote; "
            "whoever publishes this export is responsible for what it contains."
        )
    return note


@dataclass
class BlockCounts:
    """How much of the bodies went out as Markdown and how much as HTML. In
    `html`/`raw` mode each non-empty body is one HTML block."""

    markdown: int = 0
    html: int = 0


@dataclass(frozen=True)
class BodyTarget:
    """What one exporter needs to finish a body, the sentinels being its own.

    `convert` turns an HTML fragment into Markdown (the exporter's own
    options); `finish_markdown` and `finish_html` escape template syntax and
    resolve the sentinels, one for a Markdown block and one for an HTML
    block. `missing_prefix` is the sentinel `src` an image that was never
    captured carries."""

    convert: Callable[[str], str]
    finish_markdown: Callable[[str], str]
    finish_html: Callable[[str], str]
    missing_prefix: str


#: Tags that start an HTML block in CommonMark (type 6) -- every renderer the
#: exporters target passes such a block through as HTML -- plus `pre` (type
#: 1). Everything else is inline content and belongs to a run of text.
_BLOCK_TAGS = frozenset(
    """address article aside blockquote body caption center col colgroup dd details
    dialog dir div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6
    header hgroup hr html legend li main menu nav ol optgroup option p pre search
    section summary table tbody td tfoot th thead tr ul""".split()
)
#: Where whitespace in the text next to it is not shown.
_BOUNDARY_TAGS = _BLOCK_TAGS | {"br"}
_VOID_TAGS = frozenset({"area", "br", "col", "hr", "img", "wbr", "source", "track"})
#: Attributes that change nothing a reader of the exported page sees. A
#: class names a rule in the source deployment's stylesheet, which does not
#: travel (`drop_active_for_export`); the rest are behaviour, not rendering.
_IGNORED_ATTRIBUTES = frozenset(
    {"class", "target", "rel", "contenteditable", "spellcheck", "tabindex", "translate"}
)
#: Spellings a browser renders identically.
_EQUIVALENT_TAGS = {"b": "strong", "i": "em"}
#: Elements that render nothing of their own without attributes.
_TRANSPARENT_TAGS = frozenset({"span", "font", "a"})
#: Table sections render nothing of their own; Markdown writes them always.
_TABLE_SECTIONS = frozenset({"thead", "tbody", "tfoot"})
_WHITESPACE = re.compile(r"[ \t\n\r\f]+")

#: Python-Markdown as the oracle: tables and fenced code, which the
#: converter writes, and a two-space indent, which is what it nests lists by
#: and what CommonMark, kramdown and Obsidian all accept.
_ORACLE_EXTENSIONS = ("tables", "fenced_code", "sane_lists")


def render_markdown(markdown_text: str) -> str:
    """`markdown_text` rendered to HTML the way `mixed` mode checks it."""
    return _python_markdown.markdown(
        markdown_text, extensions=list(_ORACLE_EXTENSIONS), tab_length=2
    )


def _tag(element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower() if isinstance(element.tag, str) else ""


def _style(value: str) -> str:
    declarations = []
    for declaration in value.split(";"):
        name, colon, rest = declaration.partition(":")
        if colon and name.strip():
            declarations.append(f"{name.strip().lower()}:{' '.join(rest.split())}")
    return ";".join(declarations)


def _meaningful_attributes(element) -> tuple[tuple[str, str], ...]:
    """The attributes of `element` that change how it renders, normalised so
    two spellings of the same value compare equal."""
    kept = []
    for name, value in element.attrib.items():
        name = name.lower()
        if name in _IGNORED_ATTRIBUTES or name.startswith("data-"):
            continue
        if name in ("href", "src"):
            # Markdown percent-encodes a destination; the browser decodes it.
            value = unquote(value.strip())
        elif name == "style":
            value = _style(value)
        else:
            value = " ".join(value.split())
        if not value and name in ("alt", "title", "style"):
            continue  # an empty one says nothing
        kept.append((name, value))
    return tuple(sorted(kept))


def _walk(element, tokens: list, in_pre: bool) -> None:
    if element.text:
        tokens.append(["text", element.text, in_pre])
    for child in element:
        tag = _tag(child)
        if tag:
            tag = _EQUIVALENT_TAGS.get(tag, tag)
            attributes = _meaningful_attributes(child)
            child_pre = in_pre or tag == "pre"
            transparent = (
                tag in _TABLE_SECTIONS
                or (tag in _TRANSPARENT_TAGS and not attributes)
                or (tag == "code" and in_pre and not attributes)
            )
            if transparent:
                _walk(child, tokens, child_pre)
            else:
                tokens.append(["open", tag, attributes])
                _walk(child, tokens, child_pre)
                if tag not in _VOID_TAGS:
                    tokens.append(["close", tag])
        if child.tail:
            tokens.append(["text", child.tail, in_pre])


def _merge_text(tokens: list) -> list:
    merged: list = []
    for token in tokens:
        if token[0] == "text" and merged and merged[-1][0] == "text" and merged[-1][2] == token[2]:
            merged[-1] = ["text", merged[-1][1] + token[1], token[2]]
        else:
            merged.append(list(token))
    return merged


def _is_boundary(token) -> bool:
    return token is None or (token[0] in ("open", "close") and token[1] in _BOUNDARY_TAGS)


def _tidy(tokens: list) -> list:
    """Reduce a token stream to what renders: whitespace collapsed the way a
    browser collapses it, and moved out of inline elements' edges, where it
    shows the same either side."""
    tokens = _merge_text(tokens)
    for token in tokens:
        if token[0] == "text" and not token[2]:
            token[1] = _WHITESPACE.sub(" ", token[1])
    for _ in range(8):  # bounded: each pass only moves spaces outward
        changed = False
        for index, token in enumerate(tokens):
            if token[0] != "open" or token[1] in _BOUNDARY_TAGS or token[1] in _VOID_TAGS:
                continue
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if following and following[0] == "text" and not following[2]:
                if following[1].startswith(" "):
                    following[1] = following[1][1:]
                    tokens.insert(index, ["text", " ", False])
                    changed = True
                    break
        for index, token in enumerate(tokens):
            if token[0] != "close" or token[1] in _BOUNDARY_TAGS:
                continue
            previous = tokens[index - 1] if index else None
            if previous and previous[0] == "text" and not previous[2]:
                if previous[1].endswith(" "):
                    previous[1] = previous[1][:-1]
                    tokens.insert(index + 1, ["text", " ", False])
                    changed = True
                    break
        tokens = _merge_text(tokens)
        for token in tokens:
            if token[0] == "text" and not token[2]:
                token[1] = _WHITESPACE.sub(" ", token[1])
        if not changed:
            break
    tidy: list = []
    for index, token in enumerate(tokens):
        if token[0] == "text":
            before = tokens[index - 1] if index else None
            after = tokens[index + 1] if index + 1 < len(tokens) else None
            text = token[1]
            if token[2]:
                # The line break a parser drops after `<pre>`, and the one
                # Markdown adds before `</pre>`, show nothing.
                if after is not None and after[:2] == ["close", "pre"]:
                    text = text[:-1] if text.endswith("\n") else text
            else:
                if _is_boundary(before):
                    text = text.lstrip(" ")
                if _is_boundary(after):
                    text = text.rstrip(" ")
            if text:
                tidy.append(("text", text, token[2]))
        else:
            tidy.append(tuple(token))
    return tidy


def canonical(html_fragment: str) -> list:
    """What `html_fragment` renders as, in a form two spellings of the same
    rendering compare equal in (see the module docstring)."""
    try:
        root = lxml.html.fragment_fromstring(html_fragment or "", create_parent="div")
    except lxml.etree.ParserError:
        return []
    tokens: list = []
    _walk(root, tokens, False)
    return _tidy(tokens)


def same_rendering(html_fragment: str, markdown_text: str) -> bool:
    """Whether `markdown_text` renders to what `html_fragment` renders."""
    return canonical(html_fragment) == canonical(render_markdown(markdown_text))


# --- splitting a body into blocks ------------------------------------------------


def _is_grouping_div(element) -> bool:
    """An attribute-less `<div>` (class aside) renders nothing of its own: its
    content is split into blocks as if it were the body."""
    return _tag(element) == "div" and not _meaningful_attributes(element)


def _split(container, blocks: list) -> None:
    run: list = []

    def flush() -> None:
        if any(not isinstance(node, str) or node.strip() for node in run):
            blocks.append(("run", list(run)))
        run.clear()

    if container.text:
        run.append(container.text)
    for child in container:
        tag = _tag(child)
        if tag in _BLOCK_TAGS:
            flush()
            if _is_grouping_div(child):
                _split(child, blocks)
            else:
                blocks.append(("block", child))
        elif tag:
            run.append(child)
        if child.tail:
            run.append(child.tail)
    flush()


def _run_element(tag: str, nodes: list):
    element = lxml.html.Element(tag)
    last = None
    for node in nodes:
        if isinstance(node, str):
            if last is None:
                element.text = (element.text or "") + node
            else:
                last.tail = (last.tail or "") + node
        else:
            clone = copy.deepcopy(node)
            clone.tail = None
            element.append(clone)
            last = clone
    return element


def _serialize(element) -> str:
    clone = copy.deepcopy(element)
    clone.tail = None
    return lxml.html.tostring(clone, encoding="unicode")


_RAW_TEXT = re.compile(r"(<(script|style)\b.*?</\2\s*>)", re.S | re.I)


def _no_blank_lines(html: str) -> str:
    """`html` without a blank line in it, rendering the same.

    A CommonMark HTML block ends at the first blank line, and what follows is
    read as Markdown again -- a blank line inside a `<pre>` nested in a table
    would turn the rest of the table into text. A line break in text is
    written as the reference `&#10;` instead, which a browser reads as the
    same character; inside a script or stylesheet (only ever in `raw` mode)
    the blank line gets an empty comment, which both languages ignore."""
    parts = _RAW_TEXT.split(html)
    out = []
    index = 0
    while index < len(parts):
        part = parts[index]
        if index % 3 == 1:
            out.append(re.sub(r"\n(?=[ \t]*\n)", "\n/**/", part))
            index += 2
            continue
        out.append(re.sub(r"\n(?=[ \t]*\n)", "&#10;", part))
        index += 1
    return "".join(out)


def _mark_missing_images(element, missing_prefix: str) -> None:
    """An image that was never captured, shown as a visible gap with its
    address -- the same gap the Markdown path writes as a code span."""
    for image in list(element.iter("img")):
        src = image.get("src") or ""
        if not src.startswith(missing_prefix):
            continue
        marker = lxml.html.Element("span", {"class": "hcl-missing-image"})
        marker.text = f"[image not captured: {src[len(missing_prefix) :]}]"
        marker.tail = image.tail
        parent = image.getparent()
        if parent is None:
            continue
        parent.replace(image, marker)


def _html_block(element, target: BodyTarget) -> str:
    clone = copy.deepcopy(element)
    clone.tail = None
    _mark_missing_images(clone, target.missing_prefix)
    return target.finish_html(_no_blank_lines(_serialize(clone)))


def _parse(content_html: str | None):
    if not content_html or not content_html.strip():
        return None
    try:
        return lxml.html.fragment_fromstring(content_html, create_parent="div")
    except lxml.etree.ParserError:
        return None


def _has_content(element) -> bool:
    return bool(("".join(element.itertext())).strip()) or any(
        _tag(node) in ("img", "hr", "svg", "video", "audio", "iframe", "table")
        for node in element.iter()
    )


def render_body(
    content_html: str | None,
    mode: str,
    *,
    rewrite: Callable[[lxml.html.HtmlElement], None],
    target: BodyTarget,
    counts: BlockCounts,
) -> str:
    """`content_html` as the text of an exported page, in `mode`.

    `rewrite` points the body's images and links at the exporter's
    sentinels, on the parsed tree, before anything is converted -- so a
    Markdown block and an HTML block resolve them the same way."""
    tree = _parse(content_html)
    if tree is None:
        return ""
    if mode == "raw":
        rewrite(tree)
        if not _has_content(tree):
            return ""
        counts.html += 1
        return _html_block(tree, target)
    drop_active_for_export(tree)
    rewrite(tree)
    clean_for_export(tree)
    if not _has_content(tree):
        return ""
    if mode == "html":
        counts.html += 1
        return _html_block(tree, target)
    if mode == "markdown":
        text = target.convert(_serialize(tree)).strip()
        if text:
            counts.markdown += 1
        return target.finish_markdown(text)
    return _render_mixed(tree, target, counts)


def _render_mixed(tree, target: BodyTarget, counts: BlockCounts) -> str:
    blocks: list = []
    _split(tree, blocks)
    written: list[str] = []
    previous_markdown_list = False
    for kind, value in blocks:
        element = _run_element("p", value) if kind == "run" else value
        if not _has_content(element):
            continue
        cleaned = _serialize(element)
        markdown_text = target.convert(cleaned).strip()
        is_list = kind == "block" and _tag(element) in ("ul", "ol")
        # Two lists one after the other are one list in Markdown, whatever
        # each converts to on its own.
        lossless = (
            bool(markdown_text)
            and not (is_list and previous_markdown_list)
            and same_rendering(cleaned, markdown_text)
        )
        if lossless:
            counts.markdown += 1
            written.append(target.finish_markdown(markdown_text))
            previous_markdown_list = is_list
            continue
        counts.html += 1
        # A run of text had no element of its own: a `<div>` adds none.
        html_element = _run_element("div", value) if kind == "run" else element
        written.append(_html_block(html_element, target))
        previous_markdown_list = False
    return "\n\n".join(part for part in written if part.strip())


# --- rewriting a body's links and images ------------------------------------------


def rewrite_tree(
    tree,
    *,
    image: Callable[[str], str | None],
    link: Callable[[str], str | None],
) -> None:
    """Point every `<img src>` and `<a href>` the exporter recognises at its
    sentinel -- `image(src)`/`link(href)` return the new value, or `None` to
    leave the attribute as it is. In document order, which is the order the
    Markdown path stores images in."""
    for element in tree.iter("img"):
        new = image(element.get("src", ""))
        if new is not None:
            element.set("src", new)
    for element in tree.iter("a"):
        new = link(element.get("href", ""))
        if new is not None:
            element.set("href", new)


def attribute_sentinels(html: str, prefix: str, resolve: Callable[[str, str], str]) -> str:
    """Replace every `href="<prefix>VALUE"`/`src="<prefix>VALUE"` in `html` with
    `resolve(attribute, VALUE)`, which must return an attribute value that is
    safe between double quotes."""
    import html as _html  # noqa: PLC0415 - the stdlib module, not the argument

    pattern = re.compile(r'\b(href|src)="' + re.escape(prefix) + r'([^"]*)"')

    def replace(match: re.Match[str]) -> str:
        value = resolve(match.group(1), _html.unescape(match.group(2)))
        return f'{match.group(1)}="{value}"'

    return pattern.sub(replace, html)
