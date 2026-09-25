"""The Hugo exporter writes content for someone else's Hugo site.

Content only -- no layouts, no `hugo.toml` -- in the shape Hugo reads: a
section per container, a wiki's page tree as nested bundles (branch bundles
for pages with children, leaf bundles for the rest), every image and
attachment inside the bundle of the page that shows it, and links between
exported pages as `relref` shortcodes Hugo checks at build time. Everything
an author wrote arrives as text: shortcodes in content are written in Hugo's
comment form so they never run, and a title cannot end the front matter.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from connections_export.derive.model import (
    DerivedAttachment,
    DerivedBlog,
    DerivedBlogPost,
    DerivedComment,
    DerivedFile,
    DerivedFileLibrary,
    DerivedForum,
    DerivedForumReply,
    DerivedForumTopic,
    DerivedPage,
    DerivedRichContent,
    DerivedRichContentPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    Provenance,
    ResolvedAsset,
)
from connections_export.ingest import write_hugo_content
from connections_export.ingest.hugo import _hugo_escape

_IMG = "sha256:" + "a" * 64
_DOC = "sha256:" + "b" * 64
_ATT = "sha256:" + "c" * 64
_PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 16
_PDF = b"%PDF-1.4 test"


def _asset(href: str, blob: str | None) -> ResolvedAsset:
    return ResolvedAsset(
        original_href=href,
        resolved_url="https://example.com" + href,
        blob_hash=blob,
        present=blob is not None,
        scope="same",
    )


def _blobs(blob_hash: str) -> bytes | None:
    return {_IMG: _PNG, _DOC: _PDF, _ATT: b"attachment bytes"}.get(blob_hash)


def _model() -> Interchange:
    """A community with every app: a wiki tree three levels deep, a blog, a
    forum with a threaded reply, a file library and a Highlights page --
    linking to each other across apps."""
    root = DerivedPage(
        id="root",
        label="root",
        title="Getting Started",
        author="A. Okafor",
        contributors=["A. Okafor", "R. Delgado"],
        created="2025-02-07T18:36:49+01:00",
        modified="2025-03-01T09:00:00Z",
        tags=["onboarding", "handbook"],
        content_html=(
            '<p>Read <a href="/wiki/deep">the deep page</a> and '
            '<a href="/blog/post">the post</a>, then the <a href="/files/plan">plan</a>.</p>'
            '<p><img src="/img/diagram.png" alt="diagram"></p>'
        ),
        links=[
            LinkRef(original_href="/wiki/deep", scope="in_export", target_page_id="deep"),
            LinkRef(original_href="/blog/post", scope="in_export", target_page_id="post"),
            LinkRef(original_href="/files/plan", scope="in_export", target_file_id="plan"),
        ],
        assets=[_asset("/img/diagram.png", _IMG)],
        provenance=Provenance(source_url="https://example.com/wiki/root", hcl_id="uuid-root"),
        child_ids=["second", "first"],
        comments=[
            DerivedComment(id="c1", author="R. Delgado", content_html="<p>Nice.</p>"),
            DerivedComment(
                id="c2", author="A. Okafor", content_html="Thanks.", parent_comment_id="c1"
            ),
        ],
        attachments=[
            DerivedAttachment(id="a1", filename="Checklist.pdf", asset=_asset("/att/1", _ATT)),
            DerivedAttachment(id="a2", filename="Lost.pdf", asset=_asset("/att/2", None)),
        ],
    )
    second = DerivedPage(
        id="second", label="second", title="Second", parent_id="root", child_ids=["deep"]
    )
    first = DerivedPage(id="first", label="first", title="First", parent_id="root")
    deep = DerivedPage(
        id="deep",
        label="deep",
        title="Deep Page",
        parent_id="second",
        content_html='<p>Back to <a href="/wiki/root">the start</a>.</p>',
        links=[LinkRef(original_href="/wiki/root", scope="in_export", target_page_id="root")],
    )
    wiki = DerivedWiki(
        id="w",
        label="handbook",
        title="Handbook",
        community_title="Platform Team",
        root_page_ids=["root"],
        pages={"root": root, "second": second, "first": first, "deep": deep},
    )
    post = DerivedBlogPost(
        id="post",
        title="Launch Day",
        author="S. Nakamura",
        created="2025-04-02T08:00:00Z",
        tags=["news"],
        content_html='<p>See <a href="/wiki/root">the handbook</a>.</p>',
        links=[LinkRef(original_href="/wiki/root", scope="in_export", target_page_id="root")],
    )
    blog = DerivedBlog(id="b", title="Team News", post_ids=["post"], posts={"post": post})
    reply = DerivedForumReply(
        id="r1",
        author="L. Haddad",
        content_html='<p>Try this <img src="/img/shot.png" alt="shot"></p>',
        assets=[_asset("/img/shot.png", _IMG)],
        flags=["answer"],
        child_ids=["r2"],
    )
    nested = DerivedForumReply(id="r2", author="M. Lindqvist", content_html="<p>Worked.</p>")
    topic = DerivedForumTopic(
        id="topic",
        title="Login fails",
        author="M. Lindqvist",
        created="2025-05-01T12:00:00Z",
        flags=["question", "answered"],
        content_html="<p>It fails.</p>",
        reply_ids=["r1"],
        replies={"r1": reply, "r2": nested},
    )
    forum = DerivedForum(id="f", title="Help", topic_ids=["topic"], topics={"topic": topic})
    library = DerivedFileLibrary(
        id="lib",
        title="Documents",
        community_title="Platform Team",
        file_ids=["plan", "gone"],
        files={
            "plan": DerivedFile(
                id="plan",
                name="Q3 Plan.pdf",
                author="A. Okafor",
                content_type="application/pdf",
                asset=_asset("/files/plan", _DOC),
            ),
            "gone": DerivedFile(id="gone", name="Old Draft.docx", asset=_asset("/files/x", None)),
        },
    )
    highlights = DerivedRichContent(
        id="rc",
        community_title="Platform Team",
        page_ids=["hp"],
        pages={
            "hp": DerivedRichContentPage(
                id="hp", resource_id="hp", title="Welcome", content_html="<p>Hi.</p>"
            )
        },
        placed=2,
        initialized=1,
    )
    return Interchange(
        base_url="https://example.com",
        wikis=[wiki],
        blogs=[blog],
        forums=[forum],
        file_libraries=[library],
        rich_content=[highlights],
    )


def _scalar(value: str):
    """One value as the exporter writes it: an int, a boolean, a list, or a
    YAML double-quoted string (JSON's, plus `\\xNN`)."""
    if value.startswith("["):
        inner = value[1:-1]
        return [_scalar(part) for part in re.findall(r'"(?:\\.|[^"\\])*"', inner)]
    if value in ("true", "false"):
        return value == "true"
    if value.startswith('"'):
        return json.loads(re.sub(r"\\x([0-9a-f]{2})", r"\\u00\1", value))
    return int(value)


def _front_matter(path) -> dict:
    """The page's front matter. Every line is `key: value`, or, under
    `params:`, the same indented -- so a line that is neither is a breakout."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    fields: dict = {}
    for line in text.split("---\n", 2)[1].splitlines():
        if line == "params:":
            fields["params"] = {}
            continue
        match = re.fullmatch(r"(  )?([a-z_]+): (.+)", line)
        assert match, f"not front matter: {line!r}"
        target = fields["params"] if match.group(1) else fields
        target[match.group(2)] = _scalar(match.group(3))
    return fields


@pytest.fixture
def content(tmp_path):
    stats = write_hugo_content(_model(), _blobs, tmp_path)
    return tmp_path / "content", stats


# --- structure -----------------------------------------------------------------------


def test_every_container_is_a_section_with_an_index(content):
    root, _ = content
    for section in ("wikis", "blogs", "forums", "files", "highlights"):
        assert (root / section / "_index.md").is_file()
    assert (root / "wikis" / "handbook" / "_index.md").is_file()
    assert (root / "blogs" / "team-news" / "_index.md").is_file()
    assert (root / "forums" / "help" / "_index.md").is_file()
    assert (root / "files" / "platform-team" / "_index.md").is_file()
    assert (root / "highlights" / "platform-team" / "_index.md").is_file()


def test_content_only_no_site_of_its_own(tmp_path):
    write_hugo_content(_model(), _blobs, tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["README.md", "content"]
    assert not list(tmp_path.rglob("hugo.toml")) and not list(tmp_path.rglob("layouts"))


def test_the_wiki_tree_is_branch_and_leaf_bundles(content):
    """A page with children is a branch bundle, so its children can be
    sections of it; a page without is a leaf bundle."""
    wiki = content[0] / "wikis" / "handbook"

    assert (wiki / "getting-started" / "_index.md").is_file()
    assert (wiki / "getting-started" / "second" / "_index.md").is_file()
    assert (wiki / "getting-started" / "second" / "deep-page" / "index.md").is_file()
    assert (wiki / "getting-started" / "first" / "index.md").is_file()


def test_weight_keeps_sibling_order(content):
    """`child_ids` lists Second before First; alphabetical order would not."""
    wiki = content[0] / "wikis" / "handbook" / "getting-started"

    assert _front_matter(wiki / "second" / "_index.md")["weight"] == 1
    assert _front_matter(wiki / "first" / "index.md")["weight"] == 2
    assert _front_matter(wiki / "_index.md")["weight"] == 1


def test_images_and_attachments_are_resources_of_their_bundle(content):
    root, stats = content
    page = root / "wikis" / "handbook" / "getting-started"
    text = (page / "_index.md").read_text(encoding="utf-8")

    assert (page / "diagram.png").read_bytes() == _PNG
    assert "![diagram](diagram.png)" in text
    assert (page / "checklist.pdf").is_file()
    assert "[Checklist.pdf](checklist.pdf)" in text
    assert "`[not captured: Lost.pdf]`" in text
    # A reply's image goes into its topic's bundle.
    topic = root / "forums" / "help" / "login-fails"
    assert (topic / "shot.png").is_file()
    assert stats.assets_missing == 2  # the lost attachment and the uncaptured library file


def test_blog_posts_and_topics_are_leaf_bundles(content):
    root, _ = content
    post = root / "blogs" / "team-news" / "launch-day" / "index.md"
    topic = root / "forums" / "help" / "login-fails" / "index.md"

    assert _front_matter(post)["date"] == "2025-04-02T08:00:00Z"
    assert _front_matter(topic)["params"]["flags"] == ["question", "answered"]


def test_replies_are_threaded_beneath_the_topic(content):
    text = (content[0] / "forums" / "help" / "login-fails" / "index.md").read_text(encoding="utf-8")

    assert "## Replies" in text
    assert "### L. Haddad · *answer*" in text
    assert "#### M. Lindqvist" in text
    assert text.index("### L. Haddad") < text.index("#### M. Lindqvist")
    assert "![shot](shot.png)" in text


def test_comments_come_after_the_body(content):
    text = (content[0] / "wikis" / "handbook" / "getting-started" / "_index.md").read_text(
        encoding="utf-8"
    )

    assert text.index("Read [the deep page]") < text.index("## Comments")
    assert "- **R. Delgado**: Nice." in text
    assert "  - **A. Okafor**: Thanks." in text


def test_the_files_library_lists_every_document(content):
    library = content[0] / "files" / "platform-team"
    text = (library / "_index.md").read_text(encoding="utf-8")

    assert (library / "q3-plan.pdf").read_bytes() == _PDF
    assert "- [Q3 Plan.pdf](q3-plan.pdf) — A. Okafor" in text
    assert "`[not captured: Old Draft.docx]`" in text
    assert _front_matter(library / "_index.md")["params"]["file_count"] == 2


def test_highlights_pages_are_written(content):
    root, stats = content
    page = root / "highlights" / "platform-team" / "welcome" / "index.md"

    assert "Hi." in page.read_text(encoding="utf-8")
    assert _front_matter(page)["params"]["kind"] == "highlights_page"
    assert _front_matter(page.parent.parent / "_index.md")["params"]["placed"] == 2
    assert stats.highlight_pages == 1


def test_stats_count_what_was_written(content):
    _, stats = content

    assert (stats.wikis, stats.pages, stats.blogs, stats.posts) == (1, 4, 1, 1)
    assert (stats.forums, stats.topics, stats.replies) == (1, 1, 2)
    assert (stats.libraries, stats.files) == (1, 2)
    assert stats.html_mode == "mixed"


# --- front matter -------------------------------------------------------------------------


def test_front_matter_carries_what_a_template_needs(content):
    fields = _front_matter(content[0] / "wikis" / "handbook" / "getting-started" / "_index.md")

    assert fields["title"] == "Getting Started"
    assert fields["date"] == "2025-02-07T18:36:49+01:00"
    assert fields["lastmod"] == "2025-03-01T09:00:00Z"
    assert fields["tags"] == ["onboarding", "handbook"]
    params = fields["params"]
    assert params["kind"] == "wiki_page"
    assert params["source_url"] == "https://example.com/wiki/root"
    assert params["source_id"] == "uuid-root"
    assert params["author"] == "A. Okafor"
    assert params["contributors"] == ["R. Delgado"]
    assert params["community"] == "Platform Team"
    assert params["wiki"] == "Handbook"
    assert params["comment_count"] == 2


def test_the_readme_documents_every_field_it_writes(tmp_path):
    write_hugo_content(_model(), _blobs, tmp_path)
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")

    written: set[str] = set()
    for path in (tmp_path / "content").rglob("*.md"):
        fields = _front_matter(path)
        written |= {key for key in fields if key != "params"}
        written |= {f"params.{key}" for key in fields.get("params", {})}
    for name in written:
        assert f"`{name}`" in readme, name
    assert "markup.goldmark.renderer" in readme and "unsafe = true" in readme
    assert "not an endorsement" in readme
    assert "independent third-party application" in readme


def test_a_date_hugo_cannot_read_is_left_out(tmp_path):
    """Hugo stops the build on a front matter date it cannot parse."""
    model = _model()
    model.blogs[0].posts["post"].created = "yesterday\n# heading"
    write_hugo_content(model, _blobs, tmp_path)

    fields = _front_matter(tmp_path / "content" / "blogs" / "team-news" / "launch-day" / "index.md")
    assert "date" not in fields


# --- links ---------------------------------------------------------------------------------


def test_links_between_pages_are_relrefs_across_components(content):
    root, _ = content
    start = (root / "wikis" / "handbook" / "getting-started" / "_index.md").read_text(
        encoding="utf-8"
    )
    deep = root / "wikis" / "handbook" / "getting-started" / "second" / "deep-page" / "index.md"
    post = root / "blogs" / "team-news" / "launch-day" / "index.md"

    deep_path = "/wikis/handbook/getting-started/second/deep-page/index.md"
    assert f'[the deep page]({{{{< relref "{deep_path}" >}}}})' in start
    assert '[the post]({{< relref "/blogs/team-news/launch-day/index.md" >}})' in start
    assert '[plan]({{< relref "/files/platform-team/_index.md" >}}q3-plan.pdf)' in start
    back = '({{< relref "/wikis/handbook/getting-started/_index.md" >}})'
    assert back in deep.read_text(encoding="utf-8")
    assert back in post.read_text(encoding="utf-8")


def test_every_relref_names_a_page_that_was_written(tmp_path):
    """Hugo fails the build on a `relref` to nothing."""
    for mode in ("markdown", "mixed", "html", "raw"):
        out = tmp_path / mode
        write_hugo_content(_model(), _blobs, out, html_mode=mode)
        for path in (out / "content").rglob("*.md"):
            for target in re.findall(
                r'\{\{< relref "([^"]+)" >\}\}', path.read_text(encoding="utf-8")
            ):
                assert (out / "content" / target.lstrip("/")).is_file(), (mode, target)


# --- hostile content ----------------------------------------------------------------------

_HOSTILE_BODY = (
    "<p>text &lt;img src=x onerror=alert(1)&gt; and &lt;script&gt;alert(2)&lt;/script&gt;</p>"
    '<p><a href="javascript:alert(3)">click</a> '
    '<a href=" JaVa&#09;ScRiPt:alert(4)">tabbed</a> '
    '<a href="data:text/html,&lt;script&gt;alert(5)&lt;/script&gt;">data</a></p>'
    "<p>[fake](javascript:alert(6))</p>"
    '<p onmouseover="alert(7)"><img src="x.png" onerror="alert(8)"></p>'
    '<pre>{{< readfile "/etc/passwd" >}}\n{{% param secret %}}</pre>'
    "<p>{{< shortcode >}} {{% inner %}} {{< unclosed</p>"
    "<p><code>{{< x >}}</code> and {.class onclick=alert(9)}</p>"
    '<script>alert(10)</script><iframe src="https://example.com"></iframe>'
)
_HOSTILE_TITLE = 'Evil\n---\nlayout: pwned\n<script>alert("t")</script> {{< x >}}'


def _hostile_model() -> Interchange:
    model = _model()
    page = model.wikis[0].pages["first"]
    page.title = _HOSTILE_TITLE
    page.author = "<img src=x onerror=alert(11)> {{< author >}}"
    page.content_html = _HOSTILE_BODY
    page.comments = [
        DerivedComment(
            id="c",
            author="{{% who %}}",
            created="2025-01-01\n# heading",
            content_html="<p>hi {{< c >}} <img src=x onerror=alert(12)",
        )
    ]
    return model


def _body(path) -> str:
    return path.read_text(encoding="utf-8").split("---\n", 2)[2]


def _hostile_page(root):
    return next(p for p in (root / "wikis" / "handbook" / "getting-started").glob("*/index.md"))


def _live(body: str) -> list[str]:
    """Whatever in `body` would execute once rendered: the Markdown rendered
    to HTML (raw HTML blocks passing through, as with `unsafe = true`) and
    parsed, then every script-bearing element, handler and script URL."""
    import lxml.html

    from connections_export.ingest._bodies import render_markdown

    tree = lxml.html.fragment_fromstring(render_markdown(body), create_parent="div")
    found = []
    for element in tree.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag in ("script", "iframe", "object", "embed"):
            found.append(element.tag)
        for name, value in element.attrib.items():
            judged = re.sub(r"[\x00-\x20]", "", value).lower()
            if name.startswith("on"):
                found.append(name)
            elif judged.startswith(("javascript:", "vbscript:", "data:text")):
                found.append(f"{name}={value}")
    return found


@pytest.mark.parametrize("mode", ["markdown", "mixed", "html"])
def test_hostile_content_cannot_run(tmp_path, mode):
    write_hugo_content(_hostile_model(), _blobs, tmp_path, html_mode=mode)
    body = _body(_hostile_page(tmp_path / "content"))

    # No shortcode opener except in Hugo's comment form.
    assert re.findall(r"\{\{[<%](?!/\*)", body) == []
    # Rendered, nothing runs -- a `[fake](javascript:...)` in the text
    # included, which stays text in Markdown and in HTML alike.
    assert _live(body) == []
    # A newline in a one-line field cannot start a block of its own.
    assert re.search(r"^# heading", body, flags=re.M) is None


def test_hostile_content_in_raw_mode_is_kept_but_shortcodes_are_not_run(tmp_path):
    """Raw is the HTML as captured -- scripts included, which is the site
    owner's responsibility -- but a shortcode would run inside Hugo itself,
    with the site's templates, so it is escaped in raw mode too."""
    write_hugo_content(_hostile_model(), _blobs, tmp_path, html_mode="raw")
    body = _body(_hostile_page(tmp_path / "content"))

    assert "<script>alert(10)</script>" in body
    assert re.findall(r"\{\{[<%](?!/\*)", body) == []


def test_a_title_cannot_break_out_of_the_front_matter(tmp_path):
    write_hugo_content(_hostile_model(), _blobs, tmp_path)
    page = _hostile_page(tmp_path / "content")

    fields = _front_matter(page)
    assert fields["title"] == _HOSTILE_TITLE
    assert "layout" not in fields
    assert fields["params"]["author"].startswith("<img")  # data, escaped for YAML


def test_shortcode_escapes_render_back_to_the_original_text():
    """What Hugo shows is the author's text: the comment markers are all it
    removes."""
    for original in (
        '{{< readfile "/etc/passwd" >}}',
        "{{% md %}} and {{< a >}}{{< b >}}",
        "{{< nested {{< inner >}} >}}",
        "{{< x*/>}}",
        "{{</* already escaped */>}}",
    ):
        escaped = _hugo_escape(original)
        shown = _hugo_shows(escaped)
        assert shown == original, (original, escaped)


def test_an_unclosed_shortcode_gets_an_invisible_joiner():
    escaped = _hugo_escape("text {{< never closed")

    assert "{{<" not in escaped
    assert escaped.replace("\u2060", "") == "text {{< never closed"


def _hugo_shows(text: str) -> str:
    """Hugo's page lexer, for the part that matters here: at `{{<`/`{{%`
    followed by `/*` it emits everything up to the first `*/>}}`/`*/%}}` as
    text, markers removed. Any other opener would be a shortcode call."""
    out, position = [], 0
    while True:
        match = re.compile(r"\{\{([<%])").search(text, position)
        if match is None:
            out.append(text[position:])
            return "".join(out)
        assert text.startswith("/*", match.end()), f"a live shortcode at {match.start()}"
        right = (">" if match.group(1) == "<" else "%") + "}}"
        closer = "*/" + right
        end = text.index(closer, match.end() + 2)
        out.append(text[position : match.start()] + "{{" + match.group(1))
        out.append(text[match.end() + 2 : end] + right)
        position = end + len(closer)


# --- names ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title", ["CON", "..", "../../etc", "a/b\\c", "NUL\x00", "x" * 400, "Ünïcødé"]
)
def test_a_title_becomes_a_safe_folder(tmp_path, title):
    model = _model()
    model.wikis[0].pages["first"].title = title
    write_hugo_content(model, _blobs, tmp_path)

    parent = tmp_path / "content" / "wikis" / "handbook" / "getting-started"
    names = sorted(p.name for p in parent.iterdir() if p.is_dir())
    assert len(names) == 2  # "second" and the hostile one, nothing escaped upward
    for name in names:
        assert re.fullmatch(r"[a-z0-9_-]{1,60}", name), name
        assert name.split("_")[0] not in {"con", "nul"} or name.endswith("_")


def test_a_file_hugo_would_read_as_content_is_renamed(tmp_path):
    """An attachment called `_content.gotmpl` would be executed by Hugo as a
    content adapter; one called `index.md` would replace the page itself."""
    model = _model()
    page = model.wikis[0].pages["first"]
    page.attachments = [
        DerivedAttachment(id="x1", filename="index.md", asset=_asset("/a/1", _ATT)),
        DerivedAttachment(id="x2", filename="_content.gotmpl", asset=_asset("/a/2", _DOC)),
        DerivedAttachment(id="x3", filename="Page.HTML", asset=_asset("/a/3", _IMG)),
    ]
    write_hugo_content(model, _blobs, tmp_path)
    bundle = tmp_path / "content" / "wikis" / "handbook" / "getting-started" / "first"

    names = sorted(p.name for p in bundle.iterdir())
    assert "index.md.txt" in names
    assert not [n for n in names if n.endswith((".gotmpl", ".md", ".html")) and n != "index.md"]
    assert _front_matter(bundle / "index.md")["title"] == "First"


# --- the CLI, and a real build -----------------------------------------------------------


def test_cli_hugo_format_writes_content(tmp_path, capsys):
    from connections_export.cli import ingest_main
    from connections_export.gui.demo import run_demo

    archive = tmp_path / "archive"
    run_demo(lambda _event: None, archive_dir=archive, delay=0)
    code = ingest_main(
        ["--format", "hugo", "--archive", str(archive), "--output", str(tmp_path / "hugo")]
    )

    assert code == 0
    assert (tmp_path / "hugo" / "content" / "wikis").is_dir()
    out = capsys.readouterr().out
    assert "Highlights page(s)" in out and "kept as HTML" in out


def test_cli_html_option_chooses_the_mode(tmp_path, capsys):
    from connections_export.cli import ingest_main
    from connections_export.gui.demo import run_demo

    archive = tmp_path / "archive"
    run_demo(lambda _event: None, archive_dir=archive, delay=0)
    code = ingest_main(
        [
            "--format",
            "hugo",
            "--html",
            "raw",
            "--archive",
            str(archive),
            "--output",
            str(tmp_path / "hugo"),
        ]
    )

    assert code == 0
    assert "NOT cleaned" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("hugo") is None, reason="no hugo binary on PATH")
@pytest.mark.parametrize("mode", ["markdown", "mixed", "html", "raw"])
def test_the_content_builds_in_a_minimal_hugo_site(tmp_path, mode):
    """With a Hugo installed: the exported content, dropped into an otherwise
    empty site with one-line templates, builds -- every relref resolves and
    no escaped shortcode trips the parser."""
    site = tmp_path / "site"
    write_hugo_content(_hostile_model(), _blobs, site, html_mode=mode)
    (site / "hugo.toml").write_text(
        'baseURL = "https://example.com/"\n[markup.goldmark.renderer]\n  unsafe = true\n',
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
    built = (
        tmp_path / "public" / "wikis" / "handbook" / "getting-started" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'href="/wikis/handbook/getting-started/second/deep-page/"' in built
