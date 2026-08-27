"""The reference Obsidian ingester (docs/reference/interchange-format.md
§7 as a worked example): a package -> a vault, with hierarchy as nested
folders, in-export links as [[wikilinks]], images/attachments embedded
from blobs, missing assets shown as visible gaps, and threaded comments.
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedAttachment,
    DerivedComment,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    Provenance,
    ResolvedAsset,
)
from connections_export.ingest import write_obsidian_vault

_IMG = "sha256:" + "a" * 64
_PDF = "sha256:" + "b" * 64


def _model() -> Interchange:
    home = DerivedPage(
        id="h",
        label="home",
        title="Home",
        author="A. Okafor",
        tags=["intro"],
        content_html=('<p>Hello <a href="/x">see child</a></p><img src="/img/a.png" alt="pic">'),
        links=[LinkRef(original_href="/x", scope="in_export", target_page_id="c")],
        assets=[
            ResolvedAsset(
                original_href="/img/a.png",
                resolved_url="https://fake/img/a.png",
                blob_hash=_IMG,
                present=True,
                scope="same",
            )
        ],
        child_ids=["c"],
        comments=[
            DerivedComment(id="c1", author="M. Lindqvist", content_html="<p>nice</p>"),
            DerivedComment(
                id="c2", author="R. Delgado", content_html="<p>agreed</p>", parent_comment_id="c1"
            ),
        ],
        attachments=[
            DerivedAttachment(
                id="a1",
                filename="spec.pdf",
                content_type="application/pdf",
                asset=ResolvedAsset(
                    original_href="spec.pdf",
                    resolved_url="https://fake/spec.pdf",
                    blob_hash=_PDF,
                    present=True,
                    scope="same",
                ),
            )
        ],
        provenance=Provenance(hcl_id="uuid-home"),
    )
    child = DerivedPage(
        id="c",
        label="child",
        title="Child",
        parent_id="h",
        content_html='<p>child body</p><img src="/img/gone.png" alt="missing">',
        assets=[
            ResolvedAsset(
                original_href="/img/gone.png",
                resolved_url="https://fake/img/gone.png",
                blob_hash=None,
                present=False,
                scope="same",
            )
        ],
    )
    wiki = DerivedWiki(
        id="w", label="docs", title="Docs", root_page_ids=["h"], pages={"h": home, "c": child}
    )
    return Interchange(base_url="https://fake", wikis=[wiki])


def _blobs():
    data = {_IMG: b"\x89PNG\r\n\x1a\n fake png", _PDF: b"%PDF-1.4 fake"}
    return lambda h: data.get(h)


def test_vault_structure_mirrors_the_hierarchy(tmp_path):
    write_obsidian_vault(_model(), _blobs(), tmp_path)
    assert (tmp_path / "Docs" / "Home.md").is_file()  # root page
    assert (tmp_path / "Docs" / "Home" / "Child.md").is_file()  # child nested under Home/
    assert (tmp_path / "README.md").is_file()


def test_body_is_markdown_with_frontmatter(tmp_path):
    write_obsidian_vault(_model(), _blobs(), tmp_path)
    home = (tmp_path / "Docs" / "Home.md").read_text(encoding="utf-8")
    assert home.startswith("---\n")  # YAML frontmatter
    assert 'title: "Home"' in home and "hcl_id:" in home
    assert "Hello" in home and "<p>" not in home  # converted to markdown


def test_page_tags_become_native_obsidian_frontmatter_tags(tmp_path):
    """Tags are a documented, retrievable characteristic: they must surface as
    Obsidian-native `tags:` frontmatter (interchange §3.3, ingester docstring)."""
    write_obsidian_vault(_model(), _blobs(), tmp_path)
    home = (tmp_path / "Docs" / "Home.md").read_text(encoding="utf-8")
    assert 'tags: ["intro"]' in home


def test_no_tags_key_when_page_has_none(tmp_path):
    from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange

    page = DerivedPage(id="p", label="p", title="Untagged", tags=[], content_html="<p>x</p>")
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=["p"], pages={"p": page})
    write_obsidian_vault(Interchange(wikis=[wiki]), (lambda _h: None), tmp_path)
    note = (tmp_path / "W" / "Untagged.md").read_text(encoding="utf-8")
    assert "tags:" not in note


def test_in_export_link_becomes_a_wikilink(tmp_path):
    write_obsidian_vault(_model(), _blobs(), tmp_path)
    home = (tmp_path / "Docs" / "Home.md").read_text(encoding="utf-8")
    assert "[[Child|see child]]" in home  # in-export link -> wikilink to the target note


def test_present_image_is_embedded_and_copied(tmp_path):
    write_obsidian_vault(_model(), _blobs(), tmp_path)
    home = (tmp_path / "Docs" / "Home.md").read_text(encoding="utf-8")
    assert "![[a.png]]" in home
    assert (tmp_path / "attachments" / "a.png").read_bytes().startswith(b"\x89PNG")


def test_attachment_is_linked_and_copied(tmp_path):
    write_obsidian_vault(_model(), _blobs(), tmp_path)
    home = (tmp_path / "Docs" / "Home.md").read_text(encoding="utf-8")
    assert "## Attachments" in home and "[[spec.pdf]]" in home
    assert (tmp_path / "attachments" / "spec.pdf").read_bytes().startswith(b"%PDF")


def test_missing_asset_is_a_visible_gap_not_dropped(tmp_path):
    stats = write_obsidian_vault(_model(), _blobs(), tmp_path)
    child = (tmp_path / "Docs" / "Home" / "Child.md").read_text(encoding="utf-8")
    assert "image not captured" in child
    assert stats.assets_missing == 1


def test_asset_extension_follows_the_bytes_not_the_url(tmp_path):
    """The fake server (and real deployments) can serve an SVG at a
    `.png` path; naming the vault file `.png` renders broken in Obsidian.
    The ingester sniffs the bytes and names it truthfully."""
    svg = "sha256:" + "c" * 64
    page = DerivedPage(
        id="p",
        label="p",
        title="P",
        content_html='<img src="/img/thumb.png">',
        assets=[
            ResolvedAsset(
                original_href="/img/thumb.png",
                resolved_url="https://fake/img/thumb.png",
                blob_hash=svg,
                present=True,
                scope="same",
            )
        ],
    )
    wiki = DerivedWiki(id="w", label="w", title="W", root_page_ids=["p"], pages={"p": page})
    model = Interchange(base_url="https://fake", wikis=[wiki])
    blobs = {svg: b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'}

    write_obsidian_vault(model, lambda h: blobs.get(h), tmp_path)

    note = (tmp_path / "W" / "P.md").read_text(encoding="utf-8")
    assert "![[thumb.svg]]" in note  # named by content, not the URL's.png
    assert (tmp_path / "attachments" / "thumb.svg").is_file()
    assert not (tmp_path / "attachments" / "thumb.png").exists()


def test_comments_are_threaded(tmp_path):
    write_obsidian_vault(_model(), _blobs(), tmp_path)
    home = (tmp_path / "Docs" / "Home.md").read_text(encoding="utf-8")
    assert "## Comments" in home
    assert "- **M. Lindqvist**" in home
    assert "    - **R. Delgado**" in home  # reply indented under its parent


def test_end_to_end_from_a_demo_package(tmp_path):
    """The whole chain: demo crawl -> derive -> write_package -> ingest
    -> a vault of the prototype wikis (proves the contract is buildable
    from a real package with no HCL knowledge)."""
    from connections_export.archive.store import Archive
    from connections_export.gui.demo import run_demo
    from connections_export.ingest import from_package
    from connections_export.interchange.package import write_package

    archive_dir = tmp_path / "archive"
    result = run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    package = tmp_path / "package"
    write_package(
        result.interchange, Archive.open(archive_dir), package, generated_at="2026-07-21T00:00:00Z"
    )

    stats = from_package(package, tmp_path / "vault")

    assert stats.wikis == 3 and stats.pages == 20
    # a known prototype page landed as a note with its real, topic-specific
    # body prose, and the inline <style> was converted away (not left as HTML)
    assert (tmp_path / "vault" / "Engineering Handbook" / "Onboarding.md").is_file()
    body = (tmp_path / "vault" / "Engineering Handbook" / "Onboarding.md").read_text(
        encoding="utf-8"
    )
    assert "Welcome aboard" in body and "<style>" not in body
    # tags survive the full demo -> archive -> package -> obsidian path as
    # native Obsidian frontmatter tags (retrievable characteristic)
    assert 'tags: ["engineering", "onboarding"]' in body


def test_cli_ingest_writes_a_vault(tmp_path):
    from connections_export.archive.store import Archive
    from connections_export.cli import ingest_main
    from connections_export.gui.demo import run_demo
    from connections_export.interchange.package import write_package

    archive_dir = tmp_path / "archive"
    result = run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    package = tmp_path / "package"
    write_package(
        result.interchange, Archive.open(archive_dir), package, generated_at="2026-07-21T00:00:00Z"
    )

    code = ingest_main(
        ["--format", "obsidian", "--package", str(package), "--output", str(tmp_path / "vault")]
    )
    assert code == 0
    assert (tmp_path / "vault" / "README.md").is_file()
