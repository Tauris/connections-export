"""What the running header and footer say, for both PDF renderers.

There are two mechanisms, and they are not interchangeable. The Live PDF uses
Chromium's own `header_template`/`footer_template` -- HTML rendered outside the
document, where the page's CSS does not reach. The portable PDF uses paged.js
margin boxes, filled in by script after pagination.

Both accept markup. What differs is what markup can REACH: the Live PDF's template is
fetched outside the document, so an image by URL does not load there and an
icon has to be an inline `<svg>` or a `data:` URI. The portable PDF has no such
limit.

The everyday surface is therefore a set of short strings with placeholders,
which both renderers position identically, and `header_html`/`footer_html` take
a whole band of your own markup when the slots cannot say it -- a title between
two rules, a mark on every page, two lines instead of one.

The placeholders, in either:

    {page} the page number
    {pages} the total page count
    {title} the document title
    {date} the generation date
    {logo} the community's own picture, as captured into the archive --
               inlined, so a PDF made from a two-year-old archive still shows
               it. Nothing at all when the community has none, or when the
               export is not of a community.
    {section} where in the document this page is. The paged renderer answers
               with the container and item ("Engineering Handbook · Onboarding");
               the portable one cannot know -- its template renders outside the
               document -- so it falls back to the document title, the most
               specific location it can name.

Anything else in the string is literal, so `footer_right = "Confidential —
{page}"` does what it looks like.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, replace

#: What a PDF says in its margins when nothing is configured. The footer names
#: the document on the left and numbers the page on the right; there is no
#: header, because an export of someone's wiki does not need its own letterhead.
DEFAULT_MARKS: dict[str, str] = {
    "header_left": "",
    "header_center": "",
    "header_right": "",
    "footer_left": "{section}",
    "footer_center": "",
    "footer_right": "{page} / {pages}",
    "mark_size": "9pt",
    "mark_color": "#555",
    #: Left empty so the marks inherit the document's own typeface. A footer in
    #: a different family from the body reads as a mistake rather than a
    #: choice, so this is worth being able to set -- and worth not setting by
    #: default.
    "mark_font": "",
    #: A CSS border shorthand, e.g. "1px solid #ddd". Empty draws nothing: a
    #: rule with no marks above it is a line across the page for no reason.
    "header_rule": "",
    "footer_rule": "",
    #: Raw HTML for a whole band, replacing its three slots. For anything the
    #: slots cannot express -- a title between two rules, an inline SVG mark on
    #: every page, two lines of text. Placeholders work inside it exactly as
    #: they do in a slot.
    #:
    #: Both renderers honour it, by different routes (see the module note), so
    #: the caveats differ and are worth knowing before reaching for it:
    #: external images do not load in the Live PDF -- its template is fetched
    #: outside the document -- so an icon must be an inline `<svg>` or a
    #: `data:` URI. The portable PDF has no such limit.
    "header_html": "",
    "footer_html": "",
    #: How big `{logo}` is drawn. Community logos are 155x155 natively, so
    #: 6mm is roughly a third of their natural size on paper -- recognisable
    #: in a margin band without pushing the text out of it.
    "logo_size": "6mm",
    #: `circle` or `square`. Circle by default because that is how the source
    #: system shows these: a square crop of a picture everyone has only ever
    #: seen in a circle reads as a different image of the same thing.
    "logo_shape": "circle",
}

#: What each field is for, in the words the dump and the console both use. One
#: list, next to the code that honours it, so a new field cannot appear in one
#: place and be undocumented in the other.
MARK_HELP: dict[str, str] = {
    "header_left": "Top of the page, left.",
    "header_center": "Top of the page, centred.",
    "header_right": "Top of the page, right.",
    "footer_left": "Bottom of the page, left.",
    "footer_center": "Bottom of the page, centred.",
    "footer_right": "Bottom of the page, right.",
    "mark_size": "Type size for every mark, e.g. 9pt.",
    "mark_color": "Colour for every mark, e.g. #555.",
    "mark_font": "Typeface for the marks. Empty follows the document.",
    "header_rule": 'A line under the header, as a CSS border, e.g. "1px solid #ddd".',
    "footer_rule": "A line above the footer, same form.",
    "header_html": "Raw HTML for the whole header, replacing the three slots above.",
    "footer_html": "Raw HTML for the whole footer, replacing the three slots above.",
    "logo_size": "How big {logo} is drawn, e.g. 6mm. Native size is 155x155.",
    "logo_shape": "circle (as the source system shows it) or square.",
}

#: Recognised placeholders, and whether the portable renderer can supply them.
PLACEHOLDERS: dict[str, bool] = {
    "page": True,
    "pages": True,
    "title": True,
    "date": True,
    "section": True,  # portable renderer degrades it to {title}
    #: The community's own picture, as captured into the archive. Inlined as a
    #: data URI rather than linked, because the Live PDF fetches its template
    #: outside the document and an image by URL does not load there -- one
    #: behaviour to explain instead of two. Resolves to nothing when the
    #: community has no picture, or when the export is not of a community.
    "logo": True,
}

_PLACEHOLDER_RE = re.compile(r"\{([a-z]+)\}")


@dataclass(frozen=True)
class Marks:
    """Resolved header/footer settings."""

    header_left: str = ""
    header_center: str = ""
    header_right: str = ""
    footer_left: str = "{section}"
    footer_center: str = ""
    footer_right: str = "{page} / {pages}"
    mark_size: str = "9pt"
    mark_color: str = "#555"
    mark_font: str = ""
    header_rule: str = ""
    footer_rule: str = ""
    header_html: str = ""
    footer_html: str = ""
    logo_size: str = "6mm"
    logo_shape: str = "circle"

    @property
    def has_header(self) -> bool:
        return bool(self.header_html or self.header_left or self.header_center or self.header_right)

    @property
    def has_footer(self) -> bool:
        return bool(self.footer_html or self.footer_left or self.footer_center or self.footer_right)


def resolve(overrides: dict[str, str] | None) -> Marks:
    """`Marks` from user settings, rejecting names and placeholders we do not
    know -- a mark that silently renders nothing is worse than an error."""
    marks = Marks()
    if not overrides:
        return marks
    unknown = sorted(set(overrides) - set(DEFAULT_MARKS))
    if unknown:
        raise ValueError(
            f"unknown header/footer setting(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(DEFAULT_MARKS))}"
        )
    for key, value in overrides.items():
        for name in _PLACEHOLDER_RE.findall(value or ""):
            if name not in PLACEHOLDERS:
                raise ValueError(
                    f"unknown placeholder {{{name}}} in {key}. "
                    f"Known: {', '.join('{' + p + '}' for p in sorted(PLACEHOLDERS))}"
                )
    return replace(marks, **{k: v for k, v in overrides.items() if v is not None})


def logo_img(logo: tuple[bytes, str] | None, marks: Marks | None = None) -> str:
    """A captured picture as an inline `<img>`, or nothing at all.

    Nothing, rather than an empty box or a broken-image icon, when there is no
    picture: not every community has one, and each of those alternatives is
    worse than the gap.

    Both dimensions are set, not height alone: a circle needs a square box, and
    `width:auto` gives an ellipse the moment a deployment serves something
    other than the native 155x155. `object-fit: cover` then crops rather than
    squashes, which is what the source system does with the same image.
    """
    if not logo:
        return ""
    import base64  # noqa: PLC0415

    data, content_type = logo
    if not data:
        return ""
    marks = marks or Marks()
    size = _html.escape(marks.logo_size or "6mm")
    round_off = "border-radius:50%; " if (marks.logo_shape or "circle") != "square" else ""
    encoded = base64.b64encode(data).decode("ascii")
    return (
        f'<img src="data:{_html.escape(content_type or "image/png")};base64,{encoded}" '
        f'style="width:{size}; height:{size}; object-fit:cover; {round_off}'
        'vertical-align:middle;" alt="" />'
    )


def _chromium_field(name: str) -> str:
    """One placeholder as Chromium's header/footer template markup."""
    mapping = {
        "page": '<span class="pageNumber"></span>',
        "pages": '<span class="totalPages"></span>',
        "title": '<span class="title"></span>',
        "date": '<span class="date"></span>',
    }
    # `{section}` means "where in the document this page is". The paged
    # renderer answers with the container and item; Chromium's template is
    # rendered outside the document and cannot know, so it falls back to the
    # document title -- the most specific location it can name. Not a pretence,
    # and it preserves the footer this renderer has always had.
    if name == "section":
        return mapping["title"]
    return mapping.get(name, "")


def chromium_raw(html_text: str, marks: Marks, logo: tuple[bytes, str] | None = None) -> str:
    """A whole Chromium header/footer template from user-authored HTML.

    Placeholders are substituted into it as Chromium's own spans, so `{page}`
    works inside markup exactly as it does inside a slot. The markup itself is
    passed through untouched -- it is the point.

    Wrapped, because Chromium renders its templates at a near-zero default
    font size and in its own colour: without a wrapper carrying size and
    colour, hand-written markup arrives invisible and looks like the feature
    is broken.
    """
    font = f"font-family:{_html.escape(marks.mark_font)}; " if marks.mark_font else ""
    return (
        f'<div style="font-size:{_html.escape(marks.mark_size)}; width:100%; '
        f'padding:0 14mm; color:{_html.escape(marks.mark_color)}; {font}">'
        f"{_substitute(html_text, logo, marks)}"
        "</div>"
    )


def _substitute(text: str, logo: tuple[bytes, str] | None, marks: Marks | None = None) -> str:
    """Placeholders to Chromium markup, `{logo}` included."""
    return _PLACEHOLDER_RE.sub(
        lambda m: logo_img(logo, marks) if m.group(1) == "logo" else _chromium_field(m.group(1)),
        text,
    )


def _chromium_side(
    text: str, logo: tuple[bytes, str] | None = None, marks: Marks | None = None
) -> str:
    return _PLACEHOLDER_RE.sub(
        lambda m: logo_img(logo, marks) if m.group(1) == "logo" else _chromium_field(m.group(1)),
        _html.escape(text, quote=False)
        .replace("&lt;", "<")  # our own spans survive escaping of user text
        .replace("&gt;", ">"),
    )


def chromium_template(
    left: str,
    right: str,
    marks: Marks,
    *,
    center: str = "",
    rule: str = "",
    at_top: bool = False,
    logo: tuple[bytes, str] | None = None,
) -> str:
    """A Chromium header/footer template, or an empty one when every slot is.

    Chromium needs a non-empty template to render anything at all, and an
    empty `<span>` to render nothing -- there is no "off" switch beyond this.

    Three slots, always emitted when anything is set, so the centre stays
    centred whether or not its neighbours have content: with `space-between`
    and two children the middle drifts, which is exactly the sort of thing that
    only shows up on the page with the long title.
    """
    if not (left or right or center):
        return "<span></span>"
    font = f"font-family:{_html.escape(marks.mark_font)}; " if marks.mark_font else ""
    # A rule belongs on the side facing the page: under a header, above a
    # footer. Drawn only when something sits beside it.
    edge = ""
    if rule:
        side = "border-bottom" if at_top else "border-top"
        edge = f"{side}:{_html.escape(rule)}; padding-{'bottom' if at_top else 'top'}:2mm; "
    return (
        f'<div style="font-size:{_html.escape(marks.mark_size)}; width:100%; '
        f"padding:0 14mm; color:{_html.escape(marks.mark_color)}; {font}{edge}"
        'display:flex; justify-content:space-between; align-items:baseline;">'
        f'<span style="flex:1; text-align:left;">{_chromium_side(left, logo, marks)}</span>'
        f'<span style="flex:1; text-align:center;">{_chromium_side(center, logo, marks)}</span>'
        f'<span style="flex:1; text-align:right;">{_chromium_side(right, logo, marks)}</span>'
        "</div>"
    )


def dump_marks() -> str:
    """The whole header/footer configuration, as a pasteable TOML block.

    The same contract as `style --dump`: have the program write its own
    settings out, edit them, hand them back. A field you cannot see is a field
    you do not know you have -- which is how the first six of these went
    unnoticed by the person who asked for them.

    Every value is its current default, so pasting the block back changes
    nothing.
    """
    lines = [
        "# The running header and footer of an exported PDF.",
        "#",
        "# Placeholders, usable in any of the six text slots:",
        "#",
    ]
    described = {
        "page": "the page number",
        "pages": "the total page count",
        "title": "the document title",
        "date": "the generation date",
        "logo": (
            "the community's own picture, as captured into the archive -- inlined, "
            "so a PDF made from a two-year-old archive still shows it. Nothing at "
            "all when the community has none"
        ),
        "section": (
            "where in the document this page is. The paged renderer answers with "
            "the container and item; the portable one renders its template outside "
            "the document and cannot know, so it falls back to the title"
        ),
    }
    for name, meaning in described.items():
        lines.append(f"#   {{{name}}}".ljust(16) + meaning)
    lines += [
        "#",
        '# Anything else is literal text, so footer_right = "Confidential - {page}"',
        "# does what it looks like. An empty slot prints nothing.",
        "",
        "[pdf_marks]",
    ]
    for name, value in DEFAULT_MARKS.items():
        lines.append(f"# {MARK_HELP.get(name, '')}".rstrip())
        lines.append(f'{name} = "{value}"')
    return "\n".join(lines) + "\n"
