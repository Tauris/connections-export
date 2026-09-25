from connections_export.derive.model import (
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    Provenance,
    ResolvedAsset,
)
from connections_export.ingest.jekyll import write_jekyll_site


def _model() -> Interchange:
    image_hash = "sha256:" + "a" * 64
    page = DerivedPage(
        id="home",
        title="Home",
        label="home",
        author="A. Okafor",
        created="2025-02-07T18:36:49+01:00",
        tags=["notes"],
        content_html='<p>Hello <a href="/child">child</a></p><img src="/image.png" alt="diagram">',
        links=[LinkRef(original_href="/child", scope="in_export", target_page_id="child")],
        assets=[
            ResolvedAsset(
                original_href="/image.png",
                resolved_url="https://example.test/image.png",
                blob_hash=image_hash,
                present=True,
                scope="same",
            )
        ],
        provenance=Provenance(source_url="https://example.test/wiki/home"),
    )
    child = DerivedPage(
        id="child",
        title="Child",
        label="child",
        created="2025-02-08T10:00:00Z",
        content_html='<p>Child body</p><img src="/missing.png" alt="missing">',
        assets=[
            ResolvedAsset(
                original_href="/missing.png",
                resolved_url="https://example.test/missing.png",
                blob_hash=None,
                present=False,
                scope="same",
            )
        ],
    )
    return Interchange(
        base_url="https://example.test",
        wikis=[
            DerivedWiki(
                id="wiki",
                label="notes",
                title="Notes",
                root_page_ids=["home"],
                pages={"home": page, "child": child},
            )
        ],
    )


def test_jekyll_posts_use_standard_frontmatter_and_paths(tmp_path):
    image_hash = "sha256:" + "a" * 64
    stats = write_jekyll_site(
        _model(),
        lambda blob_hash: b"\x89PNG\r\n\x1a\nimage" if blob_hash == image_hash else None,
        tmp_path,
    )

    home = (tmp_path / "_posts" / "2025-02-07-home.md").read_text(encoding="utf-8")
    assert home.startswith('---\ntitle: "Home"\ndate: 2025-02-07 18:36:49 +0100\n')
    assert 'source_url: "https://example.test/wiki/home"' in home
    assert 'tags: ["notes"]' in home
    assert "{{ '/assets/images/imported/image.png' | relative_url }}" in home
    assert "{{ '/posts/2025/02/08/2025-02-08-child/' | relative_url }}" in home
    assert stats.posts == 2 and stats.assets_written == 1 and stats.assets_missing == 1


def test_jekyll_missing_assets_are_visible(tmp_path):
    write_jekyll_site(_model(), lambda _blob_hash: None, tmp_path)
    child = (tmp_path / "_posts" / "2025-02-08-child.md").read_text(encoding="utf-8")
    assert "[image not captured: /missing.png]" in child


def test_cli_jekyll_format_writes_posts(tmp_path, capsys):
    from connections_export.cli import ingest_main

    archive = tmp_path / "archive"
    from connections_export.gui.demo import run_demo

    run_demo(lambda _event: None, archive_dir=archive, delay=0)
    code = ingest_main(
        ["--format", "jekyll", "--archive", str(archive), "--output", str(tmp_path / "site")]
    )

    assert code == 0
    assert (tmp_path / "site" / "_posts").is_dir()
    assert "post(s)" in capsys.readouterr().out


def test_every_post_names_the_layout_that_shows_its_title_author_and_charset(tmp_path):
    """Without `layout:` a post renders bare: the theme never runs, so the
    title and author (front matter only a layout prints) are missing, and so
    is the page head with `<meta charset="utf-8">` -- a browser then guesses
    the encoding and shows a typographic apostrophe as "â€˜". Forum topics
    were the case reported (issues #3 and #4); every post gets it."""
    from connections_export.cli import ingest_main
    from connections_export.gui.demo import run_demo

    archive = tmp_path / "archive"
    run_demo(lambda _event: None, archive_dir=archive, delay=0)
    assert (
        ingest_main(
            ["--format", "jekyll", "--archive", str(archive), "--output", str(tmp_path / "site")]
        )
        == 0
    )

    posts = sorted((tmp_path / "site" / "_posts").glob("*.md"))
    assert any("kind: forum" in p.read_text(encoding="utf-8") for p in posts)
    for post in posts:
        front = post.read_text(encoding="utf-8").split("\n---\n", 1)[0]
        assert "\nlayout: post" in front, post.name
