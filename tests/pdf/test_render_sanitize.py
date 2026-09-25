"""Captured content is untrusted when it is printed.

A wiki page, blog post or comment is HTML written by whoever could edit it in
the deployment, and an archive may be handed over by someone else entirely.
The PDF renderers print that HTML inside a real Chromium with JavaScript on
(paged.js needs it), so anything that survives sanitization runs -- or loads --
with the whole export in the same document. An `<iframe srcdoc>` could read
`parent.document` and send it away; a `<link>` or an `@import` could reach an
intranet host while the PDF is being made.

The rule these tests pin down: what makes a page LOOK like itself (author
`<style>`, classes, inline styles, tables, embedded images, links shown as
text) survives; anything that executes, navigates, or loads from elsewhere
does not.
"""

from __future__ import annotations

import pytest

from connections_export.pdf.html import _sanitize_fragment, _sanitize_html

PNG_URI = "data:image/png;base64,iVBORw0KGgo="


def _body(html: str) -> str:
    return _sanitize_html(html, [], [], lambda _digest: None)


# --- elements that execute, embed or navigate are gone ----------------------


@pytest.mark.parametrize(
    "markup",
    [
        '<iframe srcdoc="&lt;script&gt;parent.document.title=1&lt;/script&gt;"></iframe>',
        '<iframe src="http://intranet.example/"></iframe>',
        '<frameset><frame src="http://intranet.example/"></frameset>',
        '<object data="http://intranet.example/x.swf"><param name=a value=b></object>',
        '<embed src="http://intranet.example/x.swf">',
        '<applet code="X.class"></applet>',
        '<meta http-equiv="refresh" content="0;url=http://evil.example/">',
        '<base href="http://evil.example/">',
        '<link rel="stylesheet" href="http://intranet.example/a.css">',
        '<input type="image" src="http://intranet.example/x.png">',
        "<button formaction='http://evil.example/'>go</button>",
        "<textarea>x</textarea><select><option>1</option></select>",
        '<noscript><img src="http://intranet.example/x.png"></noscript>',
        "<template><script>alert(1)</script></template>",
        '<portal src="http://evil.example/"></portal>',
        "<script>alert(1)</script>",
        "<svg><script>alert(1)</script></svg>",
        '<svg><set attributeName="href" to="javascript:alert(1)"/></svg>',
    ],
)
def test_elements_that_run_or_load_something_are_removed(markup):
    """Each of these either runs script, loads a document or resource from
    elsewhere, or redirects the render. None of them has anything to show on
    paper, so they go -- tag and content."""
    out = _body(f"<p>before</p>{markup}<p>after</p>").lower()
    for tag in (
        "<iframe",
        "<frame",
        "<object",
        "<embed",
        "<applet",
        "<meta",
        "<base",
        "<link",
        "<input",
        "<button",
        "<textarea",
        "<select",
        "<noscript",
        "<template",
        "<portal",
        "<script",
        "<set",
    ):
        assert tag not in out
    assert "http://intranet.example" not in out
    assert "evil.example" not in out
    assert "before" in out and "after" in out


def test_text_after_a_removed_element_is_kept():
    """Removing an element must not take the sentence that follows it: lxml
    keeps that text as the element's tail, and a plain `remove()` drops it."""
    out = _body("<p>one <script>x()</script>two</p>")
    assert "one" in out and "two" in out


def test_a_form_wrapper_is_unwrapped_so_its_text_survives():
    """A form around ordinary content is structure, not content: the wrapper
    (and its submit target) goes, what it wrapped stays."""
    out = _body('<form action="http://evil.example/"><p>Kept paragraph</p></form>')
    assert "<form" not in out
    assert "evil.example" not in out
    assert "Kept paragraph" in out


# --- attributes that load or execute ---------------------------------------


def test_srcset_is_dropped_so_the_browser_cannot_fetch_it():
    """`src` is rewritten to a data URI, but Chromium prefers `srcset` -- and
    fetches it -- when it is there."""
    out = _body(f'<img src="{PNG_URI}" srcset="http://intranet.example/a.png 2x">')
    assert "srcset" not in out
    assert PNG_URI in out


def test_picture_source_srcset_is_dropped():
    out = _body(
        f'<picture><source srcset="http://intranet.example/a.webp"><img src="{PNG_URI}"></picture>'
    )
    assert "intranet.example" not in out


@pytest.mark.parametrize(
    "markup",
    [
        '<video poster="http://intranet.example/p.png"></video>',
        '<video src="http://intranet.example/v.mp4"></video>',
        '<audio><source src="http://intranet.example/a.mp3"></audio>',
        '<table background="http://intranet.example/bg.png"><tr><td>x</td></tr></table>',
        '<a href="https://ok.example/" ping="http://intranet.example/track">x</a>',
        '<svg><image href="http://intranet.example/i.png"/></svg>',
        '<svg><image xlink:href="http://intranet.example/i.png"/></svg>',
        '<svg><use href="http://intranet.example/sprite.svg#a"/></svg>',
        '<svg><filter><feImage href="http://intranet.example/f.png"/></filter></svg>',
    ],
)
def test_resource_attributes_pointing_elsewhere_are_dropped(markup):
    assert "intranet.example" not in _body(markup)


def test_svg_references_inside_the_document_are_kept():
    """`<use href="#icon">` points at the page's own markup -- no load."""
    out = _body('<svg><symbol id="i"></symbol><use href="#i"/></svg>')
    assert 'href="#i"' in out


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        " javascript:alert(1)",
        "java\tscript:alert(1)",
        "vbscript:msgbox(1)",
        "data:text/html,<script>alert(1)</script>",
    ],
)
def test_script_urls_are_neutralised(href):
    out = _body(f'<a href="{href}">click</a>').lower()
    assert "script:" not in out
    assert "data:text/html" not in out
    assert "click" in out


def test_ordinary_links_and_anchors_are_kept():
    """External links are printed visibly and in-export anchors navigate the
    PDF: both are the point of keeping links at all."""
    out = _body(
        '<a href="https://example.org/x">ext</a><a href="#section-2">in</a>'
        '<a href="mailto:a@example.org">mail</a>'
    )
    assert 'href="https://example.org/x"' in out
    assert 'href="#section-2"' in out
    assert 'href="mailto:a@example.org"' in out


def test_event_handlers_are_still_removed():
    out = _body('<p onclick="x()" ONMOUSEOVER="y()">t</p>').lower()
    assert "onclick" not in out and "onmouseover" not in out


def test_an_image_left_pointing_elsewhere_becomes_a_visible_marker():
    """A comment has no asset list, so nothing rewrote its image: a live `src`
    would be fetched at render time. The address is kept as text instead."""
    out = _sanitize_fragment('<img src="http://intranet.example/x.png">')
    assert "<img" not in out
    assert "image not captured: http://intranet.example/x.png" in out


# --- author CSS: kept, but it cannot load anything --------------------------


def test_author_style_block_survives():
    """Fidelity: the captured page's own CSS is part of how it looks."""
    out = _body("<style>.box { color: red; border: 1px solid #333 }</style><p class=box>x</p>")
    assert ".box { color: red; border: 1px solid #333 }" in out
    assert 'class="box"' in out


@pytest.mark.parametrize(
    "css",
    [
        "@import url(http://intranet.example/a.css);",
        '@import "http://intranet.example/a.css";',
        "@import 'http://intranet.example/a.css' screen;",
        "@IMPORT url(http://intranet.example/a.css);",
        r"@\69mport url(http://intranet.example/a.css);",
    ],
)
def test_css_imports_are_stripped(css):
    out = _body(f"<style>{css} .x {{ color: red }}</style>")
    assert "intranet.example" not in out
    assert ".x { color: red }" in out


@pytest.mark.parametrize(
    "css",
    [
        ".x { background: url(http://intranet.example/bg.png) no-repeat }",
        ".x { background: url('http://intranet.example/bg.png') }",
        '.x { background: URL( "http://intranet.example/bg.png" ) }',
        r".x { background: \75 rl(http://intranet.example/bg.png) }",
        r".x { background: u\72l(http://intranet.example/bg.png) }",
        "@font-face { font-family: F; src: url(//intranet.example/f.woff) }",
        '.x { background-image: image-set("http://intranet.example/a.png" 1x) }',
        '.x { background-image: -webkit-image-set("http://intranet.example/a.png" 1x) }',
    ],
)
def test_remote_urls_in_style_blocks_are_stripped(css):
    out = _body(f"<style>{css}</style><p class=x>t</p>")
    assert "intranet.example" not in out
    assert "<style>" in out


def test_data_urls_in_css_are_kept():
    """An embedded background loads nothing; there is no reason to lose it."""
    out = _body(f"<style>.x {{ background: url({PNG_URI}) }}</style>")
    assert f"url({PNG_URI})" in out


def test_remote_urls_in_style_attributes_are_stripped_and_the_rest_kept():
    out = _body('<div style="color: blue; background: url(http://intranet.example/bg.png)">t</div>')
    assert "intranet.example" not in out
    assert "color: blue" in out


def test_css_escapes_that_are_not_letters_are_left_alone():
    """Decoding escapes to find a disguised `url(` must not change selectors
    that NEED an escape, like a class starting with a digit."""
    out = _body(r"<style>.\31 23 { color: red }</style>")
    assert r".\31 23 { color: red }" in out


# --- comments get the same treatment ---------------------------------------


def test_comment_fragments_are_sanitized_the_same_way():
    out = _sanitize_fragment(
        '<iframe srcdoc="&lt;script&gt;x&lt;/script&gt;"></iframe>'
        "<style>@import url(http://intranet.example/a.css);</style>"
        '<a href="javascript:alert(1)">x</a>kept text'
    ).lower()
    assert "<iframe" not in out
    assert "intranet.example" not in out
    assert "javascript:" not in out
    assert "kept text" in out
