"""Every exporter writes a combined export the way it writes one archive.

The combining step (`derive.combine`) hands the exporters one model and one
blob reader; what the exporters add is only what a reader of the result needs
to know about the combining: which archive each page came from, and -- in the
README or its equivalent -- which archives went in and whose copy won. A link
that crossed from one archive into another is an ordinary internal link in
each format, and a single archive's output does not change at all.
"""

from __future__ import annotations

import filecmp
import re
import shutil
import subprocess

import pytest

from connections_export.derive.combine import (
    CombinedSource,
    CombineInput,
    capture_times,
    combine_interchanges,
    input_from_source,
)
from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    Provenance,
    ResolvedAsset,
)
from connections_export.ingest import from_source_for_format
from tests.ingest.test_exporters_untrusted_content import (
    _all_text,
    _assert_inert,
)
from tests.ingest.test_exporters_untrusted_content import (
    _model as _hostile_model,
)

BASE = "https://connections.example.com"
_IMG = "sha256:" + "f" * 64
_PNG = b"\x89PNG\r\n\x1a\n" + b"q" * 16


def _wiki_archive() -> Interchange:
    """A wiki whose home page links to a blog post this archive never held,
    and shows an image whose bytes only the OTHER archive holds."""
    home = DerivedPage(
        id="home",
        label="home",
        title="Home",
        created="2025-02-01T09:00:00Z",
        content_html=(
            f'<p>Read <a href="{BASE}/blogs/news/entry/hello">the launch post</a>.</p>'
            '<p><img src="/img/logo.png" alt="logo"></p>'
        ),
        links=[
            LinkRef(
                original_href=f"{BASE}/blogs/news/entry/hello",
                resolved_url=f"{BASE}/blogs/news/entry/hello",
                scope="hcl_deployment",
            )
        ],
        assets=[
            ResolvedAsset(
                original_href="/img/logo.png",
                resolved_url=f"{BASE}/img/logo.png",
                blob_hash=_IMG,
                present=True,
                scope="same",
            )
        ],
        alternate_url=f"{BASE}/wikis/home/wiki/handbook/page/home",
        provenance=Provenance(source_url=f"{BASE}/wikis/basic/api/wiki/handbook/page/home/entry"),
    )
    wiki = DerivedWiki(
        id="w1", label="handbook", title="Handbook", root_page_ids=["home"], pages={"home": home}
    )
    return Interchange(base_url=BASE, wikis=[wiki])


def _blog_archive() -> Interchange:
    post = DerivedBlogPost(
        id="hello",
        title="Hello",
        created="2025-03-01T09:00:00Z",
        content_html=(
            f'<p>Back to <a href="{BASE}/wikis/home/wiki/handbook/page/home">home</a>.</p>'
        ),
        links=[
            LinkRef(
                original_href=f"{BASE}/wikis/home/wiki/handbook/page/home",
                resolved_url=f"{BASE}/wikis/home/wiki/handbook/page/home",
                scope="hcl_deployment",
            )
        ],
        alternate_url=f"{BASE}/blogs/news/entry/hello",
    )
    blog = DerivedBlog(id="b1", title="News", post_ids=["hello"], posts={"hello": post})
    return Interchange(base_url=BASE, blogs=[blog])


def _source(wiki_label="wiki-archive", blog_label="blog-archive") -> CombinedSource:
    return CombinedSource(
        [
            CombineInput(
                label=wiki_label,
                model=_wiki_archive(),
                blob_reader=lambda _d: None,
                captured_at="2025-02-02T00:00:00Z",
            ),
            CombineInput(
                label=blog_label,
                model=_blog_archive(),
                blob_reader=lambda d: _PNG if d == _IMG else None,
                captured_at="2025-03-02T00:00:00Z",
            ),
        ]
    )


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


# --- Hugo ------------------------------------------------------------------------------


def test_hugo_links_across_archives_are_relrefs(tmp_path):
    from_source_for_format(_source(), tmp_path, "hugo")

    home = _read(tmp_path / "content" / "wikis" / "handbook" / "home" / "index.md")
    post = _read(tmp_path / "content" / "blogs" / "news" / "hello" / "index.md")
    assert '[the launch post]({{< relref "/blogs/news/hello/index.md" >}})' in home
    assert '[home]({{< relref "/wikis/handbook/home/index.md" >}})' in post


def test_hugo_names_each_pages_archive_and_the_readme_lists_them(tmp_path):
    from_source_for_format(_source(), tmp_path, "hugo")

    home = _read(tmp_path / "content" / "wikis" / "handbook" / "home" / "index.md")
    section = _read(tmp_path / "content" / "blogs" / "news" / "_index.md")
    readme = _read(tmp_path / "README.md")
    assert '  source_archive: "wiki-archive"' in home
    assert '  source_archive: "blog-archive"' in section
    assert "## Combined from several archives" in readme
    assert "- wiki-archive — captured 2025-02-02T00:00:00Z" in readme
    assert "2 link(s) between archives now point inside this export" in readme
    assert "`params.source_archive`" in readme


def test_hugo_copies_an_image_the_other_archive_holds(tmp_path):
    stats = from_source_for_format(_source(), tmp_path, "hugo")

    bundle = tmp_path / "content" / "wikis" / "handbook" / "home"
    assert (bundle / "logo.png").read_bytes() == _PNG
    assert stats.assets_missing == 0


# --- Jekyll ------------------------------------------------------------------------------


def test_jekyll_links_across_archives_are_relative_url_paths(tmp_path):
    from_source_for_format(_source(), tmp_path, "jekyll")

    home = _read(tmp_path / "_posts" / "2025-02-01-home.md")
    post = _read(tmp_path / "_posts" / "2025-03-01-hello.md")
    assert "[the launch post]({{ '/posts/2025/03/01/2025-03-01-hello/' | relative_url }})" in home
    assert "[home]({{ '/posts/2025/02/01/2025-02-01-home/' | relative_url }})" in post
    assert 'source_archive: "wiki-archive"' in home.split("---\n")[1]


def test_jekyll_publishes_a_sources_page(tmp_path):
    from_source_for_format(_source(), tmp_path, "jekyll")

    sources = _read(tmp_path / "sources.md")
    assert sources.startswith("---\ntitle: Sources\n---\n")
    assert "- blog-archive — captured 2025-03-02T00:00:00Z" in sources


# --- Obsidian ------------------------------------------------------------------------------


def test_obsidian_links_across_archives_are_wikilinks(tmp_path):
    from_source_for_format(_source(), tmp_path, "obsidian")

    home = _read(tmp_path / "Handbook" / "Home.md")
    post = _read(tmp_path / "News" / "Hello.md")
    assert "[[Hello|the launch post]]" in home
    assert "[[Home|home]]" in post
    assert 'source_archive: "blog-archive"' in post.split("---\n")[1]
    assert "## Combined from several archives" in _read(tmp_path / "README.md")
    assert (tmp_path / "attachments" / "logo.png").read_bytes() == _PNG


# --- untrusted text ------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["obsidian", "jekyll", "hugo"])
def test_combining_leaves_hostile_content_as_inert_as_ever(tmp_path, fmt):
    """Bodies go through the same exporter paths, and an archive's name -- its
    owner's choice -- is text in the README like any title."""
    hostile_label = "<img src=x onerror=alert(1)>\n# heading {{ site }}"
    source = CombinedSource(
        [
            CombineInput(label=hostile_label, model=_hostile_model(), blob_reader=lambda _d: None),
            CombineInput(label="other", model=_blog_archive(), blob_reader=lambda _d: None),
        ]
    )

    from_source_for_format(source, tmp_path, fmt, html_mode="markdown")

    text = _all_text(tmp_path)
    _assert_inert(text)
    if fmt == "jekyll":
        # Liquid is Jekyll's alone; elsewhere `{{ site }}` is plain text.
        assert "{{ site }}" not in text.replace('{{ "{" }}', "")


# --- one archive ------------------------------------------------------------------------------


def _same_tree(left, right) -> None:
    compared = filecmp.dircmp(left, right)
    assert not compared.left_only and not compared.right_only, compared.report()
    for name in compared.common_files:
        assert (left / name).read_bytes() == (right / name).read_bytes(), name
    for name in compared.common_dirs:
        _same_tree(left / name, right / name)


@pytest.mark.parametrize("fmt", ["obsidian", "jekyll", "hugo"])
def test_one_archive_through_the_combiner_writes_exactly_what_it_did(tmp_path, fmt):
    """A single archive's export is pinned by every other test in this
    folder; combining one archive must not change a byte of it."""
    from connections_export.gui.demo import run_demo
    from connections_export.gui.model_source import ModelSource

    archive = tmp_path / "archive"
    run_demo(lambda _e: None, archive_dir=archive, delay=0)
    plain = ModelSource.from_archive(archive)

    from_source_for_format(plain, tmp_path / "plain", fmt)
    combined = CombinedSource([input_from_source("archive", plain, archive)])
    from_source_for_format(combined, tmp_path / "combined", fmt)

    _same_tree(tmp_path / "plain", tmp_path / "combined")


def test_capture_times_come_from_the_archives_own_records(tmp_path):
    from connections_export.gui.demo import run_demo

    archive = tmp_path / "archive"
    run_demo(lambda _e: None, archive_dir=archive, delay=0, app_filter="wiki")

    captured_at, fetched_at = capture_times(archive)

    assert captured_at and re.match(r"\d{4}-\d{2}-\d{2}T", captured_at)
    entry = next(url for url in fetched_at if url.endswith("/entry"))
    assert fetched_at[entry] >= captured_at[:10]
    assert capture_times(tmp_path / "missing") == (None, {})


def test_a_preview_capture_links_into_the_full_one_it_is_combined_with(tmp_path):
    """Real captures: a full wiki capture, then a newer one-page preview of
    the same wiki. The preview's page wins (it is newer), and its links to
    pages it never fetched -- deployment links in its own archive -- land on
    the full capture's pages."""
    from connections_export.cli import _combined_source
    from connections_export.gui.demo import run_demo

    full, preview = tmp_path / "full", tmp_path / "preview"
    run_demo(lambda _e: None, archive_dir=full, delay=0, app_filter="wiki")
    run_demo(lambda _e: None, archive_dir=preview, delay=0, app_filter="wiki", max_entries=1)
    source = _combined_source([str(full), str(preview)], author=None)

    from_source_for_format(source, tmp_path / "out", "hugo")

    assert source.combine_report.links_resolved >= 1
    onboarding = next((tmp_path / "out" / "content" / "wikis").rglob("onboarding/_index.md"))
    text = _read(onboarding)
    assert 'source_archive: "preview"' in text
    assert (
        '{{< relref "/wikis/engineering-handbook/onboarding/dev-environment/index.md" >}}' in text
    )


# --- a real Hugo -----------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("hugo") is None, reason="no hugo binary on PATH")
def test_a_combined_export_builds_in_a_minimal_hugo_site(tmp_path):
    """Hugo checks every relref at build time: the build succeeding proves the
    links between archives land on pages that exist."""
    site = tmp_path / "site"
    model, read, report = combine_interchanges(
        [
            CombineInput(label="w", model=_wiki_archive(), blob_reader=lambda _d: None),
            CombineInput(label="b", model=_blog_archive(), blob_reader=lambda _d: None),
        ]
    )
    from connections_export.ingest import write_hugo_content

    write_hugo_content(model, read, site, combined=report)
    (site / "hugo.toml").write_text(
        'baseURL = "https://example.org/"\n[markup.goldmark.renderer]\n  unsafe = true\n',
        encoding="utf-8",
    )
    layouts = site / "layouts" / "_default"
    layouts.mkdir(parents=True)
    for name in ("single.html", "list.html"):
        (layouts / name).write_text("{{ .Title }}{{ .Content }}", encoding="utf-8")

    result = subprocess.run(
        ["hugo", "--source", str(site), "--destination", str(tmp_path / "public")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    built = _read(tmp_path / "public" / "wikis" / "handbook" / "home" / "index.html")
    assert 'href="/blogs/news/hello/"' in built
