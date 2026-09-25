"""What the exporters write must not do what its authors' text says.

Every title, body, comment and name in a capture was written by some user of
the source deployment, and both exporters' output is rendered as HTML: by
Obsidian, and -- after Liquid has run over it -- by kramdown when a Jekyll
site is built and published. So text has to arrive as text:

* an escaped `&lt;img onerror&gt;` in a body must not become a live tag;
* a `javascript:` link must not survive as one;
* `{{ site | jsonify }}` in a post must not be executed by Jekyll at build
  time, where it would publish the site's config and every other page;
* a title with a newline must not end the front matter early;
* a name that Windows reserves, or one holding NUL, must not abort the export.
"""

from __future__ import annotations

import re

import pytest

from connections_export.derive.model import (
    DerivedAttachment,
    DerivedComment,
    DerivedPage,
    DerivedWiki,
    Interchange,
    ResolvedAsset,
)
from connections_export.ingest import write_obsidian_vault
from connections_export.ingest.jekyll import write_jekyll_site

_IMG = "sha256:" + "a" * 64

HOSTILE_BODY = (
    "<p>text &lt;img src=x onerror=alert(1)&gt; and &lt;script&gt;alert(2)&lt;/script&gt;</p>"
    '<p><a href="javascript:alert(3)">click</a> '
    '<a href=" JaVa&#09;ScRiPt:alert(4)">tabbed</a> '
    '<a href="data:text/html,&lt;script&gt;alert(5)&lt;/script&gt;">data</a></p>'
    "<p>[fake](javascript:alert(6))</p>"
    '<p><a href="https://example.test/a)&lt;img src=x onerror=alert(7)&gt;">paren</a></p>'
    '<p><img src="x.png" alt="a](b) &lt;img src=x onerror=alert(8)&gt; ![c"></p>'
    "<pre>before\n```\n&lt;img src=x onerror=alert(9)&gt;\n```\nafter</pre>"
    "<p>{{ site | jsonify }} {% include secret.html %} {: onmouseover=alert(10)}</p>"
    "<p><code>{{ site.github }}{%raw%}</code></p>"
    "<table><tr><td>&lt;b onclick=alert(11)&gt;cell&lt;/b&gt;</td></tr></table>"
)
HOSTILE_TITLE = 'Evil\n---\nlayout: pwned\n<script>alert("t")</script>'
HOSTILE_AUTHOR = "<img src=x onerror=alert(12)>"


def _model(**page_overrides) -> Interchange:
    fields = dict(
        id="p1",
        label="p1",
        title=HOSTILE_TITLE,
        author=HOSTILE_AUTHOR,
        created="2025-02-07T18:36:49+01:00",
        content_html=HOSTILE_BODY,
        comments=[
            DerivedComment(
                id="c1",
                author=HOSTILE_AUTHOR,
                created="2025-02-08\n# heading",
                content_html="<p>hi <img src=x onerror=alert(13)",
            )
        ],
        attachments=[
            DerivedAttachment(
                id="a1",
                filename="NUL\x00.pdf",
                asset=ResolvedAsset(
                    original_href="x",
                    resolved_url="https://fake/x",
                    blob_hash=None,
                    present=False,
                    scope="same",
                ),
            )
        ],
    )
    fields.update(page_overrides)
    page = DerivedPage(**fields)
    return Interchange(
        base_url="https://fake",
        wikis=[DerivedWiki(id="w", label="w", title="W", root_page_ids=["p1"], pages={"p1": page})],
    )


def _all_text(root) -> str:
    """Every note's Markdown, front matter removed: YAML is data a template
    may print (escaped), not Markdown anything renders."""
    return "\n".join(
        re.sub(r"\A---\n.*?\n---\n", "", path.read_text(encoding="utf-8"), flags=re.S)
        for path in sorted(root.rglob("*.md"))
        if path.is_file()
    )


def _outside_code(text: str) -> str:
    """The Markdown a renderer interprets: fenced blocks and code spans hold
    `<img` as characters, which is exactly what they are for."""
    text = re.sub(r"^(`{3,})[^\n]*\n.*?\n\1$", "", text, flags=re.S | re.M)
    return re.sub(r"(`+).*?\1", "", text, flags=re.S)


def _assert_inert(text: str) -> None:
    rendered = _outside_code(text).lower()
    # No tag the renderer would emit: every `<` from content arrives escaped.
    assert re.search(r"<\s*/?\s*(img|script|b|a)\b", rendered) is None, text
    # No link or image whose destination runs code. An escaped `\](` is text.
    assert re.search(r"(?<!\\)\]\(\s*(javascript|vbscript|data:text)", rendered) is None, text
    # A newline in a one-line field cannot start a block of its own.
    assert re.search(r"^# heading", rendered, flags=re.M) is None, text


@pytest.mark.parametrize("writer", ["obsidian", "jekyll"])
def test_hostile_content_is_written_as_text(tmp_path, writer):
    if writer == "obsidian":
        write_obsidian_vault(_model(), lambda _h: None, tmp_path)
    else:
        write_jekyll_site(_model(), lambda _h: None, tmp_path)

    _assert_inert(_all_text(tmp_path))


@pytest.mark.parametrize("writer", ["obsidian", "jekyll"])
def test_ordinary_prose_converts_as_before(tmp_path, writer):
    """The escaping changes dangerous text only. A sentence with an
    ampersand, an emphasis and a real link reads the same as it always did."""
    body = '<p>R&amp;D is <em>fine</em>, see <a href="https://example.test/x?a=1&amp;b=2">docs</a>.</p>'
    model = _model(title="Plain", author="A. Okafor", content_html=body, comments=[])
    if writer == "obsidian":
        write_obsidian_vault(model, lambda _h: None, tmp_path)
    else:
        write_jekyll_site(model, lambda _h: None, tmp_path)

    assert "R&D is *fine*, see [docs](https://example.test/x?a=1&b=2)." in _all_text(tmp_path)


def test_a_fence_inside_code_does_not_close_the_code_block(tmp_path):
    write_obsidian_vault(_model(), lambda _h: None, tmp_path)
    text = _all_text(tmp_path)

    assert "````\nbefore\n```\n<img src=x onerror=alert(9)>\n```\nafter\n````" in text


@pytest.mark.parametrize("writer", ["obsidian", "jekyll"])
def test_a_title_with_newlines_stays_inside_its_front_matter(tmp_path, writer):
    """`\\n---\\n` in a title used to end the front matter and start the body;
    Jekyll would then read `layout: pwned` as the page's layout."""
    if writer == "obsidian":
        write_obsidian_vault(_model(), lambda _h: None, tmp_path)
        note = next(p for p in tmp_path.rglob("*.md") if p.name != "README.md")
    else:
        write_jekyll_site(_model(), lambda _h: None, tmp_path)
        note = next((tmp_path / "_posts").glob("*.md"))
    text = note.read_text(encoding="utf-8")

    front = text.split("---\n")[1]
    assert "layout: pwned" not in text.splitlines()
    assert 'title: "Evil\\n---\\nlayout: pwned\\n<script>alert(\\"t\\")</script>"' in front


def test_yaml_scalar_escapes_every_control_character():
    from connections_export.ingest.obsidian import _yaml_scalar

    assert _yaml_scalar('a\\b"c\r\n\t\x00\x7f ') == '"a\\\\b\\"c\\r\\n\\t\\x00\\x7f\\u2028"'


# --- Jekyll: Liquid ---------------------------------------------------------

#: The only Liquid the exporter may write: its own URL filters, and the
#: escape it uses to print a literal `{`.
_OWN_LIQUID = re.compile(r"\{\{ '[A-Za-z0-9/._~%-]*' \| relative_url \}\}|\{\{ \"\{\" \}\}")
#: How Liquid 4 (Jekyll 3 and 4) splits a template into tags and output.
_LIQUID_TOKEN = re.compile(r"\{%.*?%\}|\{\{.*?\}\}?", re.S)


def _foreign_liquid(text: str) -> list[str]:
    return [t for t in _LIQUID_TOKEN.findall(text) if not _OWN_LIQUID.fullmatch(t)]


def test_liquid_in_content_is_never_executed(tmp_path):
    """Jekyll renders Liquid in every page with front matter before kramdown
    sees it, so `{{ site | jsonify }}` would dump the site config and every
    post into the published page. Only the exporter's own `relative_url`
    expressions may remain as Liquid -- including inside code, where content
    is not Markdown-escaped."""
    image = ResolvedAsset(
        original_href="/img/a'}}{{ site }}{{'.png",
        resolved_url="https://fake/img/x.png",
        blob_hash=_IMG,
        present=True,
        scope="same",
    )
    body = HOSTILE_BODY + "<p><img src=\"/img/a'}}{{ site }}{{'.png\"> trailing {</p>"
    write_jekyll_site(
        _model(content_html=body, assets=[image]),
        lambda h: b"\x89PNG\r\n\x1a\nx" if h == _IMG else None,
        tmp_path,
    )
    post = next((tmp_path / "_posts").glob("*.md")).read_text(encoding="utf-8")

    assert _foreign_liquid(post) == []
    assert "relative_url" in post  # the image link is still there


def test_liquid_escapes_render_back_to_the_original_text():
    """What a reader of the built site sees is the author's text, braces
    included -- escaped for Liquid, not deleted."""
    from connections_export.ingest.jekyll import _liquid_escape

    escaped = _liquid_escape("{{ x }} {%raw%} {")
    rendered = escaped.replace('{{ "{" }}', "{")

    assert rendered == "{{ x }} {%raw%} {"
    assert _foreign_liquid(escaped) == []


def test_kramdown_attribute_lists_in_text_are_escaped(tmp_path):
    """kramdown reads `{: onmouseover=...}` as attributes for the element before
    it. Escaped braces are just braces."""
    write_jekyll_site(_model(), lambda _h: None, tmp_path)
    post = next((tmp_path / "_posts").glob("*.md")).read_text(encoding="utf-8")

    rendered = post.replace('{{ "{" }}', "{")  # what kramdown sees after Liquid
    assert "\\{: onmouseover" in rendered
    assert re.search(r"(?<!\\)\{: onmouseover", rendered) is None


# --- Obsidian: names --------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    ["CON", "con.txt", "Aux", "COM1", "lpt9.tar.gz", "NUL\x00byte", "tab\there", "..", "a" * 400],
)
def test_a_title_no_filesystem_can_hold_still_exports(tmp_path, title):
    """NUL raised and aborted the whole export; a reserved device name wrote a
    note Windows cannot open; `..` walked out of its folder."""
    write_obsidian_vault(_model(title=title, content_html="<p>x</p>"), lambda _h: None, tmp_path)

    notes = [p for p in (tmp_path / "W").rglob("*.md")]
    assert len(notes) == 1
    name = notes[0].name
    stem = name.split(".")[0].rstrip(" ").lower()
    assert stem not in {"con", "prn", "aux", "nul", "com1", "lpt9"}
    assert not re.search(r"[\x00-\x1f]", name)
    assert len(name) <= 255
    assert notes[0].parent == tmp_path / "W"


def test_wikilinks_to_a_hostile_title_cannot_break_out(tmp_path):
    """A wikilink's target is the note's name, so it is the same safe name
    the note was written under -- `]]` in a title cannot close it early."""
    target = DerivedPage(id="t", label="t", title="x]] <img src=x onerror=alert(1)> [[y")
    from connections_export.derive.model import LinkRef

    source = DerivedPage(
        id="s",
        label="s",
        title="Source",
        content_html='<p><a href="/t">go</a></p>',
        links=[LinkRef(original_href="/t", scope="in_export", target_page_id="t")],
    )
    model = Interchange(
        base_url="https://fake",
        wikis=[
            DerivedWiki(
                id="w",
                label="w",
                title="W",
                root_page_ids=["s", "t"],
                pages={"s": source, "t": target},
            )
        ],
    )
    write_obsidian_vault(model, lambda _h: None, tmp_path)

    _assert_inert(_all_text(tmp_path))
    note = (tmp_path / "W" / "Source.md").read_text(encoding="utf-8")
    safe_name = next(p.stem for p in (tmp_path / "W").glob("*.md") if p.stem != "Source")
    assert f"[[{safe_name}|go]]" in note
