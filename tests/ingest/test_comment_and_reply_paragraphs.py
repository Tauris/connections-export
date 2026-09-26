"""Comments and replies keep their paragraphs and line breaks in every export.

Connections often delivers comment and reply text as plain text with line
breaks rather than HTML -- the reason the PDF turns them into breaks before
rendering. The Markdown exporters squashed a comment onto one list line and
merged a reply's paragraphs into one, so a thread read as a wall of text.
"""

from __future__ import annotations

import markdown
import pytest

from connections_export.derive.model import (
    DerivedComment,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    DerivedPage,
    DerivedWiki,
    Interchange,
)
from connections_export.ingest import hugo, jekyll, obsidian

TEXT = "First paragraph, line one.\nLine two of it.\n\nSecond paragraph."


def _model() -> Interchange:
    comment = DerivedComment(
        id="c1", author="A. Author", created="2025-05-01T12:00:00Z", content_html=TEXT
    )
    page = DerivedPage(
        id="p1", label="p1", title="Page", content_html="<p>Body</p>", comments=[comment]
    )
    wiki = DerivedWiki(id="w1", label="w1", title="Wiki", root_page_ids=["p1"], pages={"p1": page})
    reply = DerivedForumReply(
        id="r1", author="R. Replier", created="2025-05-01T13:00:00Z", content_html=TEXT
    )
    topic = DerivedForumTopic(
        id="t1", title="Topic", content_html="<p>Q</p>", replies={"r1": reply}, reply_ids=["r1"]
    )
    forum = DerivedForum(id="f1", title="Forum", topics={"t1": topic}, topic_ids=["t1"])
    return Interchange(wikis=[wiki], forums=[forum])


def _export(fmt: str, out) -> dict[str, str]:
    writers = {
        "hugo": hugo.write_hugo_content,
        "jekyll": jekyll.write_jekyll_site,
        "obsidian": obsidian.write_obsidian_vault,
    }
    writers[fmt](_model(), lambda _hash: None, out)
    texts = {}
    for path in out.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        if "A. Author" in text:
            texts["comment"] = text[text.index("## Comments") :]
        if "R. Replier" in text:
            texts["reply"] = text[text.index("## Replies") :]
    return texts


def _html(markdown_text: str) -> str:
    """Rendered, whitespace between tags and lines collapsed."""
    import re

    html = markdown.markdown(markdown_text, extensions=["sane_lists"])
    return re.sub(r"\s*\n\s*", "\n", html)


@pytest.mark.parametrize("fmt", ["hugo", "jekyll", "obsidian"])
def test_a_comment_keeps_its_paragraphs_and_line_breaks(tmp_path, fmt):
    comment = _export(fmt, tmp_path)["comment"]
    html = _html(comment)

    assert "First paragraph, line one.<br />\nLine two of it." in html
    assert "<p>Second paragraph.</p>" in html
    # Inside the comment's list item, not after the list.
    assert html.index("Second paragraph.") < html.index("</li>")
    assert "2025-05-01 12:00 UTC" in comment  # readable, as replies are


@pytest.mark.parametrize("fmt", ["hugo", "jekyll", "obsidian"])
def test_a_reply_keeps_its_paragraphs_and_line_breaks(tmp_path, fmt):
    reply = _export(fmt, tmp_path)["reply"]
    html = _html(reply)

    assert "Line two of it." in html
    assert "<p>Second paragraph.</p>" in html
    assert "First paragraph, line one.<br />" in html


@pytest.mark.parametrize("fmt", ["hugo", "jekyll", "obsidian"])
def test_a_one_line_comment_stays_on_its_list_line(tmp_path, fmt):
    """Short comments are most of them; they read as before."""
    model = _model()
    model.wikis[0].pages["p1"].comments[0].content_html = "Nice."
    writers = {
        "hugo": hugo.write_hugo_content,
        "jekyll": jekyll.write_jekyll_site,
        "obsidian": obsidian.write_obsidian_vault,
    }
    writers[fmt](model, lambda _hash: None, tmp_path)
    text = next(
        p.read_text(encoding="utf-8")
        for p in tmp_path.rglob("*.md")
        if "A. Author" in p.read_text(encoding="utf-8")
    )
    assert "**A. Author**" in text and "UTC): Nice." in text or "UTC: Nice." in text
