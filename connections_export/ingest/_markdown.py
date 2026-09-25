"""HTML -> Markdown for exporters whose output is rendered by someone else.

Every string that reaches an exporter was written by some user of the source
deployment: page bodies, titles, author names, comment text, file names. The
Markdown they end up in is rendered as HTML -- by Obsidian, and by kramdown
when a Jekyll site is built and served -- so Markdown syntax in them is live:

* `markdownify` writes a text node's characters verbatim, so the escaped text
  `&lt;img src=x onerror=...&gt;` became a literal `<img ...>` tag in the
  Markdown, which the renderer then emitted as HTML;
* a text `[click](javascript:...)` became a working link, and an `href` of
  `javascript:...` was kept as one;
* a `<pre>` holding a line of three backticks closed its own fence early.

This module is where untrusted text is made inert: text is escaped for
Markdown, link and image destinations are checked and encoded, and fences are
long enough for what they hold. It changes nothing about text that was never
dangerous -- ordinary prose converts exactly as before.
"""

from __future__ import annotations

import re

from markdownify import MarkdownConverter

#: Characters no inline context can hold: newlines would start a new block
#: (a title with `\n# ` becomes a heading, a comment line becomes a list), and
#: the rest are invisible. Collapsed to a space in inline text.
_CONTROL = re.compile(r"[\x00-\x1f\x7f\x85  ]+")

#: An `&` that would start a character reference. Only these need escaping:
#: a bare `&` is literal in every Markdown dialect, and escaping it anyway
#: would fill a note's source with `&amp;` for every "R&D".
_ENTITY_START = re.compile(r"&(?=#?[0-9A-Za-z]+;)")

#: Bracketed link text as the sentinel rewriting sees it: backslash escapes
#: (which `escape_text` adds for `[`/`]`) are part of the text, not its end.
BRACKET_TEXT = r"((?:\\.|[^\]\\])*)"

#: Schemes that run code when followed. `data:` is allowed only as an image,
#: which a browser displays but never executes.
_DANGEROUS_SCHEMES = ("javascript:", "vbscript:", "data:")
#: Characters that would end or restructure a Markdown link destination, open
#: a code span around the "not captured" marker that shows one, or -- braces --
#: begin a Liquid tag in a Jekyll page.
_DESTINATION_UNSAFE = re.compile(r"[\x00-\x20\x7f<>()\"'`\\{}]")


def escape_text(text: str, *, braces: bool = False) -> str:
    """`text` as Markdown that renders as exactly that text.

    Escapes what would turn text into structure: `\\` (so an escape cannot be
    un-escaped), `[`/`]` (links and reference definitions), `<`/`>` (raw HTML
    and autolinks), and an `&` that would start an entity. `braces` also
    escapes `{`/`}`: kramdown reads `{: ...}` as attributes for the element
    before it, `onmouseover` included.
    """
    text = text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    if braces:
        text = text.replace("{", "\\{").replace("}", "\\}")
    text = _ENTITY_START.sub("&amp;", text)
    return text.replace("<", "&lt;").replace(">", "&gt;")


def escape_inline(text: str | None, *, braces: bool = False) -> str:
    """`escape_text` for a string placed on one line of generated Markdown --
    a heading, a list item, a comment's author -- so it cannot start another."""
    return escape_text(_CONTROL.sub(" ", text or "").strip(), braces=braces)


def code_span(text: str) -> str:
    """`text` as an inline code span that no backtick inside it can close."""
    text = _CONTROL.sub(" ", text or "")
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    if longest:
        text = f" {text} "
    return f"{fence}{text}{fence}"


def safe_destination(url: str | None, *, image: bool = False) -> str | None:
    """`url` fit to stand in `[text](url)`, or `None` when it must not be linked.

    A `javascript:`/`vbscript:` URL is refused, and so is `data:` except as an
    image. The scheme is judged the way a browser reads it, with the
    whitespace and control characters it ignores removed first (`java\\tscript:`
    is `javascript:` to a browser). What remains is percent-encoded wherever it
    could end the destination or begin a title, so the URL cannot break out of
    its parentheses.
    """
    if not url:
        return None
    judged = re.sub(r"[\x00-\x20\x7f]", "", url).lower()
    if judged.startswith(_DANGEROUS_SCHEMES) and not (image and judged.startswith("data:image/")):
        return None
    url = _ENTITY_START.sub("&amp;", url)
    return _DESTINATION_UNSAFE.sub(lambda m: "".join(f"%{b:02X}" for b in m.group().encode()), url)


def _attribute_text(value: str) -> str:
    """Alt text and titles: escaped like text, on one line."""
    return escape_inline(value)


class _SafeConverter(MarkdownConverter):
    """`markdownify` with every place untrusted text reaches the output made
    inert (see the module docstring). `escape_braces` is an option of ours."""

    def escape(self, text, parent_tags):
        text = escape_text(text, braces=bool(self.options.get("escape_braces")))
        return super().escape(text, parent_tags)

    def convert_a(self, el, text, parent_tags):
        if el.get("href") is not None:
            href = safe_destination(el.get("href"))
            if href is None:
                del el["href"]
            else:
                el["href"] = href
        if el.get("title"):
            el["title"] = _attribute_text(el["title"])
        return super().convert_a(el, text, parent_tags)

    def convert_img(self, el, text, parent_tags):
        src = safe_destination(el.get("src"), image=True)
        el["src"] = src or ""
        if el.get("alt"):
            el["alt"] = _attribute_text(el["alt"])
        if el.get("title"):
            el["title"] = _attribute_text(el["title"])
        return super().convert_img(el, text, parent_tags)

    def convert_video(self, el, text, parent_tags):
        for attribute in ("src", "poster"):
            if el.get(attribute) is not None:
                el[attribute] = safe_destination(el.get(attribute), image=True) or ""
        for source in el.find_all("source"):
            if source.get("src") is not None:
                source["src"] = safe_destination(source.get("src"), image=True) or ""
        return super().convert_video(el, text, parent_tags)

    def convert_pre(self, el, text, parent_tags):
        result = super().convert_pre(el, text, parent_tags)
        # A fence longer than any backtick run inside, so a line of ``` in the
        # code is code rather than the end of the block.
        longest = max((len(run) for run in re.findall(r"`{3,}", text or "")), default=0)
        if longest:
            fence = "`" * (longest + 1)
            result = re.sub(r"^\n\n```", "\n\n" + fence, result, count=1)
            result = re.sub(r"\n```\n\n$", "\n" + fence + "\n\n", result, count=1)
        return result


def to_markdown(html: str, *, escape_braces: bool = False, **options) -> str:
    """`markdownify(html, **options)`, with untrusted text made inert."""
    return _SafeConverter(escape_braces=escape_braces, **options).convert(html)


def html_to_text(html: str | None) -> str:
    """The text of an HTML fragment, as a reader would see it.

    A regex that removes `<...>` leaves an unclosed `<img src=x onerror=...`
    behind; a parser does not.
    """
    from bs4 import BeautifulSoup  # noqa: PLC0415 - ships with markdownify

    return BeautifulSoup(html or "", "html.parser").get_text()


def readable_time(value: str) -> str:
    """An ISO timestamp (`YYYY-MM-DDTHH:MM:SSZ`) as `YYYY-MM-DD HH:MM UTC`;
    anything that is not one as it came."""
    from datetime import UTC, datetime  # noqa: PLC0415

    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if moment.tzinfo is None:
        return moment.strftime("%Y-%m-%d %H:%M")
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def quote_block(text: str, depth: int) -> list[str]:
    """`text` as lines inside `depth` nested blockquotes (unchanged at 0).

    How a reply to a reply is indented in every export: a deeper heading
    only shows in the Markdown, while every renderer and theme indents a
    quote, heading and body together."""
    if depth <= 0:
        return text.split("\n")
    marker = ">" * depth
    return [f"{marker} {line}".rstrip() for line in text.split("\n")] + [""]
