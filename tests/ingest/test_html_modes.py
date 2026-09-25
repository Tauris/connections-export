"""How page content is written: Markdown, HTML, or Markdown where it loses nothing.

Markdown has no spelling for a merged table cell, a coloured phrase, an image
at a set width or a figure with a caption, so converting those drops what
made them what they were. `html_mode` lets the reader choose:

* ``markdown`` -- everything converted (Obsidian's default, and what both
  older exporters always wrote);
* ``mixed`` -- each top-level block converted only when the Markdown renders
  back to exactly what the HTML showed, and kept as cleaned HTML otherwise
  (the default for Jekyll and Hugo);
* ``html`` -- all cleaned HTML;
* ``raw`` -- the HTML exactly as captured, not cleaned.

The mixed-mode decision is checked, not guessed: these tests pin which
realistic blocks it proves lossless and which it keeps as HTML.
"""

from __future__ import annotations

import re

import pytest

from connections_export.derive.model import (
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    ResolvedAsset,
)
from connections_export.ingest import write_hugo_content, write_jekyll_site, write_obsidian_vault
from connections_export.ingest._bodies import (
    BlockCounts,
    BodyTarget,
    canonical,
    html_mode_for,
    render_body,
    same_rendering,
)
from connections_export.ingest._markdown import to_markdown

_IMG = "sha256:" + "e" * 64
_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 16


def _plain_target() -> BodyTarget:
    return BodyTarget(
        convert=lambda html: to_markdown(html, heading_style="ATX", bullets="-"),
        finish_markdown=lambda markdown: markdown,
        finish_html=lambda html: html,
        missing_prefix="test-missing:",
    )


def _mixed(html: str) -> tuple[str, BlockCounts]:
    counts = BlockCounts()
    text = render_body(
        html, "mixed", rewrite=lambda _tree: None, target=_plain_target(), counts=counts
    )
    return text, counts


# --- the mixed-mode decision, block by block -----------------------------------------


@pytest.mark.parametrize(
    "html, markdown",
    [
        (
            "<p>Plain <b>bold</b>, <i>italic</i> and "
            '<a href="https://example.com/a b">a link</a>.</p>',
            "Plain **bold**, *italic* and [a link](https://example.com/a%20b).",
        ),
        ("<h2>A heading</h2>", "## A heading"),
        ("<pre>x = 1\n\ny = 2</pre>", "```\nx = 1\n\ny = 2\n```"),
        ("<ul><li>one<ul><li>nested</li></ul></li><li>two</li></ul>", "- one\n  - nested\n- two"),
        ('<p><img src="a.png" alt="diagram"></p>', "![diagram](a.png)"),
        (
            "<table><tr><th>Name</th><th>Role</th></tr><tr><td>Ana</td><td>Lead</td></tr></table>",
            "| Name | Role |\n| --- | --- |\n| Ana | Lead |",
        ),
        ("<blockquote><p>Quoted.</p></blockquote>", "> Quoted."),
        ("<p>first line<br>second line</p>", "first line  \nsecond line"),
    ],
)
def test_a_block_markdown_can_say_exactly_is_written_as_markdown(html, markdown):
    """Ordinary prose, headings, code, simple lists and tables have an exact
    Markdown spelling, so mixed mode writes them as Markdown."""
    text, counts = _mixed(html)

    assert text == markdown
    assert counts.html == 0 and counts.markdown == 1


@pytest.mark.parametrize(
    "html",
    [
        # Merged cells, a cell colour, a list in a cell: no Markdown table has them.
        '<table><tr><td colspan="2">wide</td></tr><tr><td>a</td><td>b</td></tr></table>',
        '<table><tr><td rowspan="2">tall</td><td>a</td></tr><tr><td>b</td></tr></table>',
        "<table><tr><th>h</th></tr>"
        '<tr><td style="background-color: #fde68a">warm</td></tr></table>',
        "<table><tr><th>h</th></tr><tr><td><ul><li>in a cell</li></ul></td></tr></table>",
        # A table with no header row would gain an empty one in Markdown.
        "<table><tr><td>a</td><td>b</td></tr></table>",
        # An image at a set size, or aligned.
        '<p><img src="a.png" alt="x" width="240" height="120"></p>',
        '<p><img src="a.png" alt="x" align="right"></p>',
        # A coloured phrase.
        '<p>Mostly plain, <span style="color: #b91c1c">but this is red</span>.</p>',
        # A figure with its caption.
        '<figure><img src="a.png" alt="x"><figcaption>Caption</figcaption></figure>',
        # Underline has no Markdown at all.
        "<p>An <u>underlined</u> word.</p>",
    ],
)
def test_a_block_markdown_would_change_stays_html(html):
    text, counts = _mixed(html)

    assert counts.html == 1 and counts.markdown == 0
    assert text.startswith("<"), text
    # Whatever made it HTML is still there.
    for attribute in ("colspan", "rowspan", "background-color", "width", "align", "color"):
        if attribute in html:
            assert attribute in text


def test_text_markdownify_would_misread_is_kept_as_html():
    """Literal backticks in prose become a code span once converted -- the
    comparison catches that, and keeps the paragraph as HTML."""
    text, counts = _mixed("<p>Run the `setUp` method.</p>")

    assert counts.html == 1
    assert text == "<p>Run the `setUp` method.</p>"


def test_a_body_is_decided_block_by_block():
    """One lossy block does not turn the whole body into HTML."""
    text, counts = _mixed(
        "<h2>Status</h2>"
        "<p>All good.</p>"
        '<table><tr><td colspan="2">merged</td></tr></table>'
        "<p>More text.</p>"
    )

    assert (counts.markdown, counts.html) == (3, 1)
    assert text.split("\n\n") == [
        "## Status",
        "All good.",
        '<table><tr><td colspan="2">merged</td></tr></table>',
        "More text.",
    ]


def test_a_wrapper_div_is_split_into_its_blocks():
    """Editors wrap a whole body in `<div class="...">`; the wrapper renders
    nothing of its own, so it must not force the whole body into HTML."""
    text, counts = _mixed('<div class="lotusWiki"><p>One.</p><p>Two.</p></div>')

    assert text == "One.\n\nTwo."
    assert counts.html == 0


def test_two_lists_in_a_row_do_not_merge_into_one():
    """Converted separately, two adjacent lists would read as one list."""
    text, counts = _mixed("<ul><li>a</li></ul><ul><li>b</li></ul>")

    assert text == "- a\n\n<ul><li>b</li></ul>"
    assert counts.html == 1


def test_an_html_block_holds_no_blank_line():
    """A CommonMark HTML block ends at a blank line; one inside a `<pre>` in a
    table would turn the rest of the table into Markdown text."""
    text, _ = _mixed('<table><tr><td colspan="2"><pre>a\n\nb</pre></td></tr></table>')

    assert "\n\n" not in text
    assert "a&#10;\nb" in text


def test_the_comparison_ignores_what_does_not_render():
    assert canonical("<p>a  <b>b</b>\n c</p>") == canonical("<p>a <strong>b</strong> c</p>")
    assert canonical('<p class="x"><span>t</span></p>') == canonical("<p>t</p>")
    assert canonical('<p><a href="a%20b">x</a></p>') == canonical('<p><a href="a b">x</a></p>')
    assert canonical("<table><tbody><tr><td>1</td></tr></tbody></table>") == canonical(
        "<table><tr><td>1</td></tr></table>"
    )
    assert canonical("<p>a <strong> b </strong>c</p>") == canonical("<p>a <strong>b</strong> c</p>")


def test_the_comparison_sees_what_does_render():
    assert canonical("<p>a</p>") != canonical("<div>a</div>")
    assert canonical('<p style="color:red">a</p>') != canonical("<p>a</p>")
    assert canonical('<td colspan="2">a</td>') != canonical("<td>a</td>")
    assert canonical("<pre>a\n  b</pre>") != canonical("<pre>a\nb</pre>")
    assert not same_rendering("<p>`x`</p>", "`x`")


# --- the four modes -------------------------------------------------------------------


_BODY = (
    "<p>Hello <b>world</b>.</p>"
    '<p onclick="alert(1)">Clicked <a href="javascript:alert(2)">here</a>.</p>'
    "<script>alert(3)</script>"
    '<table><tr><td colspan="2">merged</td></tr></table>'
)


def _render(mode: str) -> tuple[str, BlockCounts]:
    counts = BlockCounts()
    text = render_body(
        _BODY, mode, rewrite=lambda _tree: None, target=_plain_target(), counts=counts
    )
    return text, counts


def test_html_mode_writes_one_cleaned_html_block():
    text, counts = _render("html")

    assert (counts.markdown, counts.html) == (0, 1)
    assert text.startswith("<div>") and text.endswith("</div>")
    assert "<b>world</b>" in text and 'colspan="2"' in text
    for dangerous in ("onclick", "javascript:", "<script"):
        assert dangerous not in text


def test_raw_mode_writes_the_html_as_captured():
    text, counts = _render("raw")

    assert counts.html == 1
    assert 'onclick="alert(1)"' in text and "<script>alert(3)</script>" in text


def test_markdown_mode_converts_everything():
    text, counts = _render("markdown")

    assert counts.html == 0
    assert "<" not in text.replace("&lt;", "")
    assert "merged" in text


def test_every_mode_name_is_checked():
    assert html_mode_for("obsidian", None) == "markdown"
    assert html_mode_for("jekyll", None) == "mixed"
    assert html_mode_for("hugo", None) == "mixed"
    assert html_mode_for("jekyll", "RAW") == "raw"
    with pytest.raises(ValueError, match="html mode"):
        html_mode_for("jekyll", "pdf")


# --- through each exporter: links, images and escaping in HTML blocks -----------------


def _model(body: str) -> Interchange:
    page = DerivedPage(
        id="home",
        label="home",
        title="Home",
        created="2025-02-07T18:36:49+01:00",
        content_html=body,
        links=[LinkRef(original_href="/child", scope="in_export", target_page_id="child")],
        assets=[
            ResolvedAsset(
                original_href="/img/pic.png",
                resolved_url="https://example.com/img/pic.png",
                blob_hash=_IMG,
                present=True,
                scope="same",
            ),
            ResolvedAsset(
                original_href="/img/gone.png",
                resolved_url="https://example.com/img/gone.png",
                blob_hash=None,
                present=False,
                scope="same",
            ),
        ],
        child_ids=["child"],
    )
    child = DerivedPage(
        id="child",
        label="child",
        title="Child",
        created="2025-02-08T10:00:00Z",
        parent_id="home",
        content_html="<p>Child body</p>",
    )
    return Interchange(
        base_url="https://example.com",
        wikis=[
            DerivedWiki(
                id="w",
                label="w",
                title="Notes",
                root_page_ids=["home"],
                pages={"home": page, "child": child},
            )
        ],
    )


#: A body whose every block must stay HTML in mixed mode, and which links to
#: another page, shows a captured image and a missing one.
_LINKED_HTML = (
    '<table><tr><td colspan="2"><a href="/child">to the child</a></td></tr>'
    '<tr><td><img src="/img/pic.png" alt="pic" width="50"></td>'
    '<td><img src="/img/gone.png" alt="gone" width="50"></td></tr></table>'
)


def _write(writer: str, tmp_path, body: str, mode: str) -> str:
    """The home page's file, as written."""
    model = _model(body)
    blobs = lambda h: _PNG if h == _IMG else None  # noqa: E731
    if writer == "obsidian":
        write_obsidian_vault(model, blobs, tmp_path, html_mode=mode)
        return (tmp_path / "Notes" / "Home.md").read_text(encoding="utf-8")
    if writer == "jekyll":
        write_jekyll_site(model, blobs, tmp_path, html_mode=mode)
        return (tmp_path / "_posts" / "2025-02-07-home.md").read_text(encoding="utf-8")
    write_hugo_content(model, blobs, tmp_path, html_mode=mode)
    return (tmp_path / "content" / "wikis" / "notes" / "home" / "_index.md").read_text(
        encoding="utf-8"
    )


#: Where each exporter's HTML points the link and the image.
_EXPECTED_LINKS = {
    "obsidian": ('href="Home/Child.md"', 'src="../attachments/pic.png"'),
    "jekyll": (
        "href=\"{{ '/posts/2025/02/08/2025-02-08-child/' | relative_url }}\"",
        "src=\"{{ '/assets/images/imported/pic.png' | relative_url }}\"",
    ),
    "hugo": (
        'href="{{< relref "/wikis/notes/home/child/index.md" >}}"',
        'src="pic.png"',
    ),
}


@pytest.mark.parametrize("writer", ["obsidian", "jekyll", "hugo"])
@pytest.mark.parametrize("mode", ["mixed", "html", "raw"])
def test_links_and_images_inside_html_point_at_the_export(tmp_path, writer, mode):
    """An HTML block is not converted, so its links and images are rewritten
    in place -- to the same targets the Markdown path would use."""
    text = _write(writer, tmp_path, _LINKED_HTML, mode)

    link, image = _EXPECTED_LINKS[writer]
    assert link in text
    assert image in text
    assert "[image not captured: /img/gone.png]" in text
    assert "-link:" not in text and "-asset:" not in text and "-embed:" not in text


@pytest.mark.parametrize("writer", ["obsidian", "jekyll", "hugo"])
def test_links_in_markdown_blocks_still_resolve_in_mixed_mode(tmp_path, writer):
    text = _write(writer, tmp_path, '<p>See <a href="/child">the child</a>.</p>', "mixed")

    expected = {
        "obsidian": "[[Child|the child]]",
        "jekyll": "[the child]({{ '/posts/2025/02/08/2025-02-08-child/' | relative_url }})",
        "hugo": '[the child]({{< relref "/wikis/notes/home/child/index.md" >}})',
    }[writer]
    assert expected in text


_TEMPLATE_HTML = (
    '<table><tr><td colspan="2">{{ site | jsonify }} {% include x.html %} '
    "{{< shortcode >}} {{% md %}}</td></tr></table>"
)


@pytest.mark.parametrize("mode", ["markdown", "mixed", "html", "raw"])
def test_jekyll_escapes_liquid_in_every_mode(tmp_path, mode):
    text = _write("jekyll", tmp_path, _TEMPLATE_HTML, mode)
    body = text.split("---\n", 2)[2]

    own = re.compile(r"\{\{ '[A-Za-z0-9/._~%-]*' \| relative_url \}\}|\{\{ \"\{\" \}\}")
    foreign = [
        token
        for token in re.findall(r"\{%.*?%\}|\{\{.*?\}\}?", body, re.S)
        if not own.fullmatch(token)
    ]
    assert foreign == []
    assert "jsonify" in body  # escaped, not deleted


@pytest.mark.parametrize("mode", ["markdown", "mixed", "html", "raw"])
def test_hugo_escapes_shortcodes_in_every_mode(tmp_path, mode):
    text = _write("hugo", tmp_path, _TEMPLATE_HTML, mode)
    body = text.split("---\n", 2)[2]

    # Every opener is Hugo's comment form; nothing else could run.
    assert re.findall(r"\{\{[<%](?!/\*)", body) == []
    if mode != "markdown":  # Markdown escapes the braces themselves instead
        # Hugo drops the comment markers and shows the rest: the original text.
        shown = re.sub(r"\{\{([<%])/\*", r"{{\1", body)
        shown = re.sub(r"\*/([>%])\}\}", r"\1}}", shown)
        assert "{{&lt; shortcode &gt;}} {{% md %}}" in shown or "{{< shortcode >}}" in shown


@pytest.mark.parametrize("writer", ["obsidian", "jekyll", "hugo"])
def test_the_block_counts_reach_the_stats(tmp_path, writer):
    model = _model("<p>Plain.</p>" + _LINKED_HTML)
    blobs = lambda h: _PNG if h == _IMG else None  # noqa: E731
    write = {
        "obsidian": write_obsidian_vault,
        "jekyll": write_jekyll_site,
        "hugo": write_hugo_content,
    }[writer]

    stats = write(model, blobs, tmp_path, html_mode="mixed")

    assert stats.html_mode == "mixed"
    assert stats.html_blocks == 1
    assert stats.markdown_blocks == 2  # the plain paragraph and the child's body


def test_obsidian_stays_markdown_and_jekyll_turns_mixed_by_default(tmp_path):
    model = _model(_LINKED_HTML)
    blobs = lambda h: _PNG if h == _IMG else None  # noqa: E731

    assert write_obsidian_vault(model, blobs, tmp_path / "o").html_blocks == 0
    assert write_jekyll_site(model, blobs, tmp_path / "j").html_blocks == 1
    assert write_hugo_content(model, blobs, tmp_path / "h").html_blocks == 1


def test_a_style_element_does_not_reach_the_site(tmp_path):
    """A stylesheet inside a body would restyle the whole published page,
    theme included; inline styles, which style only their element, stay."""
    body = '<style>p { color: red }</style><p style="color: blue">x</p>'
    text = _write("hugo", tmp_path, body, "html")

    assert "<style" not in text
    assert 'style="color: blue"' in text


def test_the_raw_mode_readme_says_whose_responsibility_it_is(tmp_path):
    write_hugo_content(_model("<p>x</p>"), lambda _h: None, tmp_path, html_mode="raw")
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")

    assert "NOT cleaned" in readme
    assert "responsible" in readme


_HOSTILE = (
    '<p onclick="alert(1)">x <a href=" JaVa&#09;ScRiPt:alert(2)">tabbed</a></p>'
    '<table><tr><td colspan="2" onmouseover="alert(3)">'
    '<img src="/img/pic.png" onerror="alert(4)" width="9"></td></tr></table>'
    '<script>alert(5)</script><iframe src="https://example.com"></iframe>'
    '<form action="https://example.com"><input name="p"><button>Go</button></form>'
    '<a href="data:text/html,&lt;script&gt;alert(6)&lt;/script&gt;">data</a>'
    '<svg><a href="javascript:alert(7)"><text>svg</text></a></svg>'
    "<style>body { display: none }</style>"
)


def _executable(markdown_text: str) -> list[str]:
    """What would run once the page is rendered with raw HTML allowed."""
    import lxml.html

    from connections_export.ingest._bodies import render_markdown

    tree = lxml.html.fragment_fromstring(render_markdown(markdown_text), create_parent="div")
    found = []
    for element in tree.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag in ("script", "iframe", "object", "embed", "form", "style"):
            found.append(element.tag)
        for name, value in element.attrib.items():
            judged = re.sub(r"[\x00-\x20]", "", value).lower()
            if name.startswith("on") or judged.startswith(
                ("javascript:", "vbscript:", "data:text")
            ):
                found.append(f"{name}={value}")
    return found


@pytest.mark.parametrize("writer", ["obsidian", "jekyll", "hugo"])
@pytest.mark.parametrize("mode", ["mixed", "html"])
def test_cleaned_html_cannot_run_in_any_exporter(tmp_path, writer, mode):
    """Mixed and html modes write HTML the renderer passes through, so it is
    the PDF's allowlist cleaner that stands between an author and a reader."""
    text = _write(writer, tmp_path, _HOSTILE, mode)
    body = text.split("---\n", 2)[2].replace('{{ "{" }}', "{")

    assert _executable(body) == []
    assert 'colspan="2"' in body  # the table is still there, as HTML
