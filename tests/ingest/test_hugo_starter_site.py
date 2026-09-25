"""The optional Hugo starter site: just enough beside `content/` to view the
export with `hugo server`.

Content-only stays the default -- a site owner merging `content/` into their
own site must not find a second `hugo.toml` or a stray `layouts/` in the
export. Asked for, the starter site is a `hugo.toml`, our own templates and
one stylesheet, all NEXT TO `content/` and never inside it, and it turns on
raw HTML only when the chosen mode actually wrote some.

With a Hugo binary on PATH the demo export is built with the starter site,
and the result is checked page by page.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import pytest

from connections_export.ingest import from_source_for_format, write_hugo_content
from connections_export.ingest.hugo_starter import STARTER_FILES
from tests.ingest.test_hugo import _blobs, _hostile_model, _model

HUGO = shutil.which("hugo")
needs_hugo = pytest.mark.skipif(HUGO is None, reason="no hugo binary on PATH")


def _config(root: Path) -> dict:
    return tomllib.loads((root / "hugo.toml").read_text(encoding="utf-8"))


# --- what is written -----------------------------------------------------------------


def test_without_the_option_the_export_is_content_only(tmp_path):
    """The default output is unchanged: a site owner's own `hugo.toml` and
    layouts are never shadowed by ours."""
    stats = write_hugo_content(_model(), _blobs, tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["README.md", "content"]
    assert stats.starter_site is False
    assert "## Starter site" not in (tmp_path / "README.md").read_text(encoding="utf-8")


def test_the_starter_site_is_written_beside_content_never_inside_it(tmp_path):
    stats = write_hugo_content(_model(), _blobs, tmp_path, starter_site=True)

    assert stats.starter_site is True
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "README.md",
        "content",
        "hugo.toml",
        "layouts",
        "static",
    ]
    for name in STARTER_FILES:
        assert (tmp_path / name).is_file(), name
    assert (tmp_path / "layouts" / "baseof.html").is_file()
    assert (tmp_path / "static" / "css" / "site.css").is_file()
    inside = {p.name for p in (tmp_path / "content").rglob("*")}
    assert not inside & {"hugo.toml", "layouts", "baseof.html", "site.css"}


def test_the_stylesheet_loads_nothing_from_anywhere_else(tmp_path):
    """No web fonts, no CDN: the site must render offline, and a reader's
    browser must not be sent to a third party to see their own content."""
    write_hugo_content(_model(), _blobs, tmp_path, starter_site=True)

    css = (tmp_path / "static" / "css" / "site.css").read_text(encoding="utf-8")
    assert "@import" not in css and "url(" not in css and "//" not in css.replace("/*", "")
    assert "prefers-color-scheme: dark" in css
    templates = "".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / "layouts").rglob("*.html")
    )
    assert "http://" not in templates and "https://" not in templates
    assert "<script" not in templates


@pytest.mark.parametrize("mode", ["html", "mixed", "raw"])
def test_raw_html_is_allowed_only_when_the_mode_wrote_some(tmp_path, mode):
    write_hugo_content(_model(), _blobs, tmp_path, html_mode=mode, starter_site=True)

    config = _config(tmp_path)
    assert config["markup"]["goldmark"]["renderer"]["unsafe"] is True
    text = (tmp_path / "hugo.toml").read_text(encoding="utf-8")
    assert "# " in text.split("unsafe")[0][-400:], "the switch explains itself"


def test_markdown_mode_leaves_raw_html_off(tmp_path):
    """With nothing written as HTML there is nothing to allow -- and an
    author's `<script>` typed as text stays text."""
    write_hugo_content(_model(), _blobs, tmp_path, html_mode="markdown", starter_site=True)

    config = _config(tmp_path)
    assert "unsafe" not in (tmp_path / "hugo.toml").read_text(encoding="utf-8")
    assert config.get("markup", {}).get("goldmark", {}).get("renderer", {}).get("unsafe") is None


def test_the_config_works_from_a_folder_and_keeps_only_what_it_renders(tmp_path):
    write_hugo_content(_model(), _blobs, tmp_path, starter_site=True)

    config = _config(tmp_path)
    assert config["baseURL"] == "/"
    assert config["relativeURLs"] is True
    assert config["taxonomies"] == {"tag": "tags"}
    assert {"rss", "sitemap"} <= {kind.lower() for kind in config["disableKinds"]}
    assert config["title"] == "Platform Team"


def test_the_title_cannot_break_out_of_the_config(tmp_path):
    """The title is an author's; it is written as one TOML string."""
    model = _model()
    for wiki in model.wikis:
        wiki.community_title = 'Evil"\ntitle = "x" \\ \x7f \U0001f600'
    model.blogs, model.forums, model.file_libraries, model.rich_content = [], [], [], []
    write_hugo_content(model, _blobs, tmp_path, starter_site=True)

    assert _config(tmp_path)["title"] == 'Evil"\ntitle = "x" \\ \x7f \U0001f600'


def test_the_readme_says_what_the_starter_site_is(tmp_path):
    write_hugo_content(_model(), _blobs, tmp_path, starter_site=True)

    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Starter site") :]
    assert "hugo server" in section
    assert "starting point" in section
    assert "`content/`" in section and "existing site" in section
    for name in ("`hugo.toml`", "`layouts/`", "`static/css/site.css`"):
        assert name in section


def test_the_starter_site_is_for_hugo_only(tmp_path):
    with pytest.raises(ValueError, match="Hugo"):
        from_source_for_format(object(), tmp_path, "jekyll", starter_site=True)


def test_cli_starter_site_flag(tmp_path, capsys):
    from connections_export.cli import ingest_main
    from connections_export.gui.demo import run_demo

    archive = tmp_path / "archive"
    run_demo(lambda _event: None, archive_dir=archive, delay=0)
    out = tmp_path / "hugo"
    code = ingest_main(
        ["--format", "hugo", "--starter-site", "--archive", str(archive), "--output", str(out)]
    )

    assert code == 0
    assert (out / "hugo.toml").is_file() and (out / "layouts" / "baseof.html").is_file()
    assert "hugo server" in capsys.readouterr().out


def test_cli_refuses_the_starter_site_for_another_format(tmp_path, capsys):
    from connections_export.cli import ingest_main

    with pytest.raises(SystemExit):
        ingest_main(
            [
                "--format",
                "jekyll",
                "--starter-site",
                "--archive",
                str(tmp_path),
                "--output",
                str(tmp_path / "out"),
            ]
        )
    assert "--starter-site" in capsys.readouterr().err


# --- a real build ----------------------------------------------------------------------


class _Page(HTMLParser):
    """What a built page holds that the checks below care about."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.scripts = 0
        self.tree_links: list[str] = []
        self._in_tree = 0
        self.main_text: list[str] = []
        self._in_main = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script":
            self.scripts += 1
        if tag == "a" and attributes.get("href"):
            self.hrefs.append(attributes["href"])
            if self._in_tree:
                self.tree_links.append(attributes["href"])
        if tag in ("nav", "aside") and "tree" in (attributes.get("class") or ""):
            self._in_tree += 1
        if tag == "main":
            self._in_main = True

    def handle_endtag(self, tag):
        if tag in ("nav", "aside") and self._in_tree:
            self._in_tree -= 1
        if tag == "main":
            self._in_main = False

    def handle_data(self, data):
        if self._in_main and data.strip():
            self.main_text.append(data.strip())


def _parse(path: Path) -> _Page:
    page = _Page()
    page.feed(path.read_text(encoding="utf-8"))
    return page


def _demo_export(tmp_path: Path, mode: str = "mixed") -> Path:
    from connections_export.cli import _content_source
    from connections_export.gui.demo import run_demo

    archive = tmp_path / "archive"
    run_demo(lambda _event: None, archive_dir=archive, delay=0)
    source, _kind = _content_source(str(archive), author=None)
    out = tmp_path / "export"
    from_source_for_format(source, out, "hugo", html_mode=mode, starter_site=True)
    return out


def _build(site: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [HUGO, "--panicOnWarning", "--logLevel", "info"],
        cwd=site,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )


@pytest.fixture(scope="module")
def built_demo(tmp_path_factory):
    if HUGO is None:
        pytest.skip("no hugo binary on PATH")
    tmp_path = tmp_path_factory.mktemp("starter")
    site = _demo_export(tmp_path)
    result = _build(site)
    return site, result


@needs_hugo
def test_the_demo_export_builds_without_errors_or_warnings(built_demo):
    _site, result = built_demo
    log = result.stdout + result.stderr
    assert result.returncode == 0, log
    assert "WARN" not in log and "ERROR" not in log, log
    assert "deprecated" not in log.lower(), log


@needs_hugo
def test_every_content_page_is_rendered_and_not_empty(built_demo):
    site, _ = built_demo
    public = site / "public"
    content = site / "content"
    pages = [p for p in content.rglob("*.md") if p.name in ("index.md", "_index.md")]
    assert len(pages) > 40
    for page in pages:
        built = public / page.parent.relative_to(content) / "index.html"
        assert built.is_file(), f"{page} was not rendered"
        parsed = _parse(built)
        assert len(" ".join(parsed.main_text)) > 20, f"{built} is empty"
    home = _parse(public / "index.html")
    for section in ("Wikis", "Blogs", "Forums", "Files", "Highlights"):
        assert section in home.main_text


@needs_hugo
def test_the_wiki_tree_follows_the_wiki_order_not_the_alphabet(built_demo):
    """`dev-environment` comes before `coding-standards` in the wiki, so a
    tree sorted by title would get it wrong; `weight` gets it right."""
    site, _ = built_demo
    content = site / "content" / "wikis" / "engineering-handbook" / "onboarding"
    children = []
    for folder in content.iterdir():
        if folder.is_dir():
            index = folder / ("_index.md" if (folder / "_index.md").exists() else "index.md")
            weight = re.search(r"^weight: (\d+)$", index.read_text(encoding="utf-8"), re.M)
            children.append((int(weight.group(1)), folder.name))
    expected = [name for _, name in sorted(children)]
    assert expected != sorted(expected), "the fixture must not already be alphabetical"

    wiki = _parse(site / "public" / "wikis" / "engineering-handbook" / "index.html")
    shown = [
        href.rstrip("/").rsplit("/", 1)[-1]
        for href in wiki.tree_links
        if re.search(r"onboarding/[^/]+/?$", href)
    ]
    assert shown == expected


@needs_hugo
def test_a_wiki_page_shows_its_tree_and_breadcrumb(built_demo):
    site, _ = built_demo
    path = site / "public" / "wikis" / "engineering-handbook" / "onboarding" / "index.html"
    html = path.read_text(encoding="utf-8")

    assert 'aria-current="page"' in html
    assert 'class="crumbs"' in html and "Engineering Handbook" in html
    assert "View in Connections" in html
    assert "Sub-pages" in html


@needs_hugo
def test_tags_have_pages(built_demo):
    site, _ = built_demo
    public = site / "public"
    assert (public / "tags" / "index.html").is_file()
    assert (public / "tags" / "onboarding" / "index.html").is_file()
    tagged = _parse(public / "tags" / "onboarding" / "index.html")
    assert any("onboarding" in href for href in tagged.hrefs)


@needs_hugo
def test_internal_links_all_resolve(built_demo):
    """Every relative link in every built page lands on a file that exists
    -- the relrefs, the tree, the navigation and the stylesheet.

    Not checked: a link the author wrote as a path on the Connections server
    (`/wikis/home?...`) to something that was never captured. The exporter
    keeps it as written, and relative URLs make it relative like any other;
    whether it resolves is the content's business, not the templates'."""
    site, _ = built_demo
    public = site / "public"
    authored = set()
    for page in (site / "content").rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        authored |= set(re.findall(r"\]\((/[^)\s]+)\)", text))
        authored |= set(re.findall(r'href="(/[^"{]+)"', text))
    missing = []
    for built in public.rglob("*.html"):
        here = "/" + built.parent.relative_to(public).as_posix() + "/"
        for href in _parse(built).hrefs:
            if re.match(r"^[a-z]+:|^#|^//", href):
                continue
            if urljoin(here, href) in authored:
                continue
            target = (built.parent / href.split("#")[0]).resolve()
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                missing.append(f"{built.relative_to(public)} -> {href}")
    assert not missing, missing[:20]
    assert (public / "css" / "site.css").is_file()


@needs_hugo
@pytest.mark.parametrize("mode", ["markdown", "mixed"])
def test_no_script_from_content_reaches_the_site(tmp_path, mode):
    """An author's `<script>`, event handler and shortcode stay inert in
    the built site in the modes that promise it: the templates bring no
    script, so any `<script>` would have come from content."""
    site = tmp_path / "site"
    write_hugo_content(_hostile_model(), _blobs, site, html_mode=mode, starter_site=True)
    result = _build(site)

    assert result.returncode == 0, result.stdout + result.stderr
    for built in (site / "public").rglob("*.html"):
        html = built.read_text(encoding="utf-8")
        assert _parse(built).scripts == 0, built
        assert not re.search(r"<[a-z]+[^>]*\son[a-z]+=", html, re.I), built
