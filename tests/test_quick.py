""": `quick_blog_model` builds an `Interchange`
by fetching the blog feeds directly — no archive, no derive-from-disk —
so a browser PDF (or the reader) needs only the list of entries, not a
full import. Runs the real HTTP client over the in-process fake server.
"""

from __future__ import annotations

from connections_export.config import Config
from connections_export.fakeserver import SynthSeed, make_app, synthesize_blogs
from connections_export.pdf.html import render_html
from connections_export.quick import quick_blog_model
from tests.crawler.conftest import make_client

BASE = "https://fake"


def _client_and_blogs():
    from connections_export.fakeserver.model import WikiSet

    blogset = synthesize_blogs(SynthSeed(seed=0))
    app = make_app(WikiSet(wikis=[]), blogset=blogset)
    return make_client(app), blogset


def _config() -> Config:
    return Config(base_url=BASE, page_size=2)  # small page size -> exercise pagination


def test_quick_blog_model_fetches_entries_without_an_archive(tmp_path):
    client, blogset = _client_and_blogs()

    model = quick_blog_model(_config(), client, homepage=blogset.homepage)

    # Every synthesized blog + post is present, bodies included.
    assert len(model.blogs) == len(blogset.blogs)
    posts = [p for b in model.blogs for p in b.posts.values()]
    assert len(posts) == sum(len(b.posts) for b in blogset.blogs)
    assert all(p.content_html for p in posts), "post bodies should come through"
    # No comments by default (the browser path just needs the list).
    assert all(not p.comments for p in posts)
    # No archive was created anywhere (quick model writes nothing to disk).
    assert not any(tmp_path.iterdir())


def test_quick_blog_model_with_comments_threads_them():
    client, blogset = _client_and_blogs()

    model = quick_blog_model(_config(), client, homepage=blogset.homepage, with_comments=True)

    commented = [p for b in model.blogs for p in b.posts.values() if p.comments]
    assert commented, "with_comments should fetch and attach comments"
    # threading preserved (one-level: some comment replies to another)
    assert any(c.parent_comment_id for p in commented for c in p.comments), (
        "blog comment threading (parent_comment_id) should survive the quick fetch"
    )


def test_a_quick_model_renders_to_pdf_html():
    client, blogset = _client_and_blogs()
    model = quick_blog_model(_config(), client, homepage=blogset.homepage)

    # The PDF renderer consumes the quick model unchanged (no blobs needed).
    html = render_html(model, blob_bytes=lambda _d: None)
    first = next(iter(model.blogs[0].posts.values()))
    assert first.title in html
    assert "<!DOCTYPE html>" in html


def test_cli_pdf_quick_writes_a_blog_pdf_with_no_archive(tmp_path):
    """`connections-export pdf --quick --app blog` fetches the entry list and
    renders a PDF without creating any archive. `client` + `render` are
    injected so no socket and no browser are needed."""
    from connections_export.cli import pdf_main

    client, blogset = _client_and_blogs()
    out = tmp_path / "blog.pdf"
    seen = {}

    def fake_render(model, blob_bytes):
        seen["posts"] = sum(len(b.posts) for b in model.blogs)
        seen["wikis"] = len(model.wikis)
        return b"%PDF-1.4 quick"

    code = pdf_main(
        [
            "--quick",
            "--app",
            "blog",
            "--base-url",
            BASE,
            "--blogs-homepage",
            blogset.homepage,
            "--output",
            str(out),
        ],
        env={},
        render=fake_render,
        client=client,
    )

    assert code == 0
    assert out.read_bytes().startswith(b"%PDF-")
    assert seen["posts"] == sum(len(b.posts) for b in blogset.blogs)
    assert seen["wikis"] == 0  # blog-only quick model
    # No archive directory was written anywhere under tmp.
    assert list(tmp_path.iterdir()) == [out]
