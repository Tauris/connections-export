"""Two different things with the same name never share a note or a folder.

A vault names folders after containers and notes after titles, and a title is
whatever its author typed: two wikis are both "Handbook", a community captured
from a test and a production deployment is combined into one export, a page is
"Setup" and its neighbour "setup" (one file on Windows and macOS), "A: B" and
"A- B" are one name once a colon is made safe. Each of those would otherwise share one
folder, or overwrite one note with the other.

The first claimant keeps the plain name; every later, different one gets a
short tag derived from its Connections id -- stable across exports, so an
unchanged capture always yields the same vault -- and every link, embed and
index entry points at the name that was actually written.
"""

from __future__ import annotations

import filecmp

from connections_export.derive.combine import CombinedSource, CombineInput
from connections_export.derive.model import (
    DerivedAttachment,
    DerivedBlog,
    DerivedBlogPost,
    DerivedPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    ResolvedAsset,
)
from connections_export.ingest import from_source_for_format, write_obsidian_vault
from connections_export.interchange.filenames import disambiguator


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


def _link(href: str, target: str) -> LinkRef:
    return LinkRef(original_href=href, scope="in_export", target_page_id=target)


def _handbook(wiki_id: str, prefix: str, words: str) -> DerivedWiki:
    """A wiki titled "Handbook": "Home", and "Setup" linking back to "Home".
    `words` makes each wiki's text its own, so a test can tell them apart."""
    home = DerivedPage(
        id=f"{prefix}home",
        label="home",
        title="Home",
        content_html=f"<p>{words} home</p>",
        child_ids=[f"{prefix}setup"],
    )
    setup = DerivedPage(
        id=f"{prefix}setup",
        label="setup",
        title="Setup",
        parent_id=f"{prefix}home",
        content_html=f'<p>{words}: <a href="/home">back home</a></p>',
        links=[_link("/home", f"{prefix}home")],
    )
    return DerivedWiki(
        id=wiki_id,
        label="handbook",
        title="Handbook",
        root_page_ids=[home.id],
        pages={home.id: home, setup.id: setup},
    )


def _no_blobs(_digest: str) -> bytes | None:
    return None


def _same_tree(left, right) -> None:
    compared = filecmp.dircmp(left, right)
    assert not compared.left_only and not compared.right_only, compared.report()
    for name in compared.common_files:
        assert (left / name).read_bytes() == (right / name).read_bytes(), name
    for name in compared.common_dirs:
        _same_tree(left / name, right / name)


# --- containers ------------------------------------------------------------------------


def test_two_wikis_with_the_same_title_get_a_folder_each(tmp_path):
    """Both wikis are "Handbook": sharing one folder would write the second wiki's
    "Home" over the first's."""
    model = Interchange(wikis=[_handbook("w1", "a-", "alpha"), _handbook("w2", "b-", "beta")])

    stats = write_obsidian_vault(model, _no_blobs, tmp_path)

    second = f"Handbook ({disambiguator('w2')})"
    assert "alpha home" in _read(tmp_path / "Handbook" / "Home.md")
    assert "beta home" in _read(tmp_path / second / "Home.md")
    assert "beta" in _read(tmp_path / second / "Home" / "Setup.md")
    assert [entry.path for entry in stats.disambiguated] == [second]


def test_links_in_same_titled_wikis_resolve_into_their_own_wiki(tmp_path):
    """Obsidian resolves `[[Home]]` by note name across the whole vault, and
    both wikis now hold a "Home": a link to one names its folder too."""
    model = Interchange(wikis=[_handbook("w1", "a-", "alpha"), _handbook("w2", "b-", "beta")])

    write_obsidian_vault(model, _no_blobs, tmp_path)

    second = f"Handbook ({disambiguator('w2')})"
    first_setup = _read(tmp_path / "Handbook" / "Home" / "Setup.md")
    second_setup = _read(tmp_path / second / "Home" / "Setup.md")
    assert "[[Handbook/Home|back home]]" in first_setup
    assert f"[[{second}/Home|back home]]" in second_setup
    readme = _read(tmp_path / "README.md")
    assert "- [[Handbook/Home|Home]]" in readme
    assert f"- [[{second}/Home|Home]]" in readme


def test_a_wiki_and_a_blog_with_the_same_title_do_not_share_a_folder(tmp_path):
    """Every container is a folder at the top of the vault, whatever its app."""
    page = DerivedPage(id="p", label="p", title="Launch", content_html="<p>wiki</p>")
    wiki = DerivedWiki(id="w", label="n", title="News", root_page_ids=["p"], pages={"p": page})
    post = DerivedBlogPost(id="q", title="Launch", content_html="<p>blog</p>")
    blog = DerivedBlog(id="b", title="News", post_ids=["q"], posts={"q": post})

    write_obsidian_vault(Interchange(wikis=[wiki], blogs=[blog]), _no_blobs, tmp_path)

    assert "wiki" in _read(tmp_path / "News" / "Launch.md")
    assert "blog" in _read(tmp_path / f"News ({disambiguator('b')})" / "Launch.md")


def test_a_container_titled_like_the_attachments_folder_keeps_out_of_it(tmp_path):
    """`attachments/` holds every copied file; a wiki called "Attachments"
    would otherwise be written into it (one folder on Windows and macOS)."""
    page = DerivedPage(id="p", label="p", title="Intro", content_html="<p>x</p>")
    wiki = DerivedWiki(
        id="w", label="a", title="Attachments", root_page_ids=["p"], pages={"p": page}
    )

    write_obsidian_vault(Interchange(wikis=[wiki]), _no_blobs, tmp_path)

    assert (tmp_path / f"Attachments ({disambiguator('w')})" / "Intro.md").is_file()


# --- notes ------------------------------------------------------------------------------


def _wiki_of(*pages: DerivedPage) -> Interchange:
    roots = [page.id for page in pages if not page.parent_id]
    return Interchange(
        wikis=[
            DerivedWiki(
                id="w",
                label="docs",
                title="Docs",
                root_page_ids=roots,
                pages={page.id: page for page in pages},
            )
        ]
    )


def _index_linking(*targets: str) -> DerivedPage:
    return DerivedPage(
        id="index",
        label="index",
        title="Index",
        content_html="".join(f'<p><a href="/{t}">to {t}</a></p>' for t in targets),
        links=[_link(f"/{t}", t) for t in targets],
    )


def test_two_pages_with_the_same_title_get_a_note_each_and_links_find_each(tmp_path):
    first = DerivedPage(id="s1", label="s1", title="Setup", content_html="<p>first</p>")
    second = DerivedPage(
        id="s2", label="s2", title="Setup", content_html="<p>second</p>", child_ids=["kid"]
    )
    kid = DerivedPage(id="kid", label="kid", title="Kid", parent_id="s2", content_html="<p>k</p>")

    stats = write_obsidian_vault(
        _wiki_of(_index_linking("s1", "s2"), first, second, kid), _no_blobs, tmp_path
    )

    renamed = f"Setup ({disambiguator('s2')})"
    assert "first" in _read(tmp_path / "Docs" / "Setup.md")
    assert "second" in _read(tmp_path / "Docs" / f"{renamed}.md")
    # a page's children sit in a folder named as its note is
    assert (tmp_path / "Docs" / renamed / "Kid.md").is_file()
    index = _read(tmp_path / "Docs" / "Index.md")
    assert "[[Setup|to s1]]" in index
    assert f"[[{renamed}|to s2]]" in index
    assert [entry.path for entry in stats.disambiguated] == [f"Docs/{renamed}.md"]


def test_titles_that_differ_only_in_case_do_not_overwrite_each_other(tmp_path):
    """ "Setup" and "setup" are two pages in Connections and one file on
    Windows and macOS."""
    upper = DerivedPage(id="u", label="u", title="Setup", content_html="<p>upper</p>")
    lower = DerivedPage(id="l", label="l", title="setup", content_html="<p>lower</p>")

    write_obsidian_vault(_wiki_of(_index_linking("u", "l"), upper, lower), _no_blobs, tmp_path)

    renamed = f"setup ({disambiguator('l')})"
    names = sorted(path.name for path in (tmp_path / "Docs").iterdir())
    assert names == ["Index.md", "Setup.md", f"{renamed}.md"]
    assert "lower" in _read(tmp_path / "Docs" / f"{renamed}.md")
    assert f"[[{renamed}|to l]]" in _read(tmp_path / "Docs" / "Index.md")


def test_titles_that_only_become_equal_once_made_safe_stay_apart(tmp_path):
    """A colon is not allowed in a file name, so "A: B" is written "A- B" --
    the very name another page already has."""
    colon = DerivedPage(id="c", label="c", title="A: B", content_html="<p>colon</p>")
    dash = DerivedPage(id="d", label="d", title="A- B", content_html="<p>dash</p>")

    write_obsidian_vault(_wiki_of(_index_linking("c", "d"), colon, dash), _no_blobs, tmp_path)

    renamed = f"A- B ({disambiguator('d')})"
    assert "colon" in _read(tmp_path / "Docs" / "A- B.md")
    assert "dash" in _read(tmp_path / "Docs" / f"{renamed}.md")
    assert f"[[{renamed}|to d]]" in _read(tmp_path / "Docs" / "Index.md")


def test_same_titled_posts_in_one_blog_get_a_note_each(tmp_path):
    posts = {
        pid: DerivedBlogPost(id=pid, title="Weekly update", content_html=f"<p>{pid}</p>")
        for pid in ("w1", "w2")
    }
    blog = DerivedBlog(id="b", title="News", post_ids=["w1", "w2"], posts=posts)

    write_obsidian_vault(Interchange(blogs=[blog]), _no_blobs, tmp_path)

    renamed = f"Weekly update ({disambiguator('w2')})"
    assert "w2" in _read(tmp_path / "News" / f"{renamed}.md")
    assert f"- [[{renamed}]]" in _read(tmp_path / "README.md")


def test_an_html_body_links_to_the_renamed_note(tmp_path):
    """In an HTML mode a link is a relative path, not a wikilink; it must
    reach the note that was written, not the one it collided with."""
    first = DerivedPage(id="s1", label="s1", title="Setup", content_html="<p>first</p>")
    second = DerivedPage(id="s2", label="s2", title="Setup", content_html="<p>second</p>")

    write_obsidian_vault(
        _wiki_of(_index_linking("s1", "s2"), first, second), _no_blobs, tmp_path, html_mode="html"
    )

    index = _read(tmp_path / "Docs" / "Index.md")
    assert 'href="Setup.md"' in index
    assert f'href="Setup%20%28{disambiguator("s2")}%29.md"' in index


def test_attachments_differing_only_in_case_are_two_files(tmp_path):
    """Two different files called "Logo.png" and "logo.png" would be one file
    on Windows and macOS, the second overwriting the first."""
    blobs = {"sha256:" + "1" * 64: b"one", "sha256:" + "2" * 64: b"two"}

    def attachment(att_id: str, name: str, digest: str) -> DerivedAttachment:
        asset = ResolvedAsset(
            original_href=name, resolved_url=name, blob_hash=digest, present=True, scope="same"
        )
        return DerivedAttachment(id=att_id, filename=name, asset=asset)

    page = DerivedPage(
        id="p",
        label="p",
        title="Logos",
        content_html="<p>x</p>",
        attachments=[
            attachment("a1", "Logo.png", "sha256:" + "1" * 64),
            attachment("a2", "logo.png", "sha256:" + "2" * 64),
        ],
    )

    write_obsidian_vault(_wiki_of(page), blobs.get, tmp_path)

    written = sorted(path.name.casefold() for path in (tmp_path / "attachments").iterdir())
    assert len(written) == len(set(written)) == 2
    note = _read(tmp_path / "Docs" / "Logos.md")
    for path in (tmp_path / "attachments").iterdir():
        assert f"[[{path.name}]]" in note


# --- reporting --------------------------------------------------------------------------


def test_the_readme_lists_every_disambiguated_name(tmp_path):
    model = Interchange(wikis=[_handbook("w1", "a-", "alpha"), _handbook("w2", "b-", "beta")])

    write_obsidian_vault(model, _no_blobs, tmp_path)

    readme = _read(tmp_path / "README.md")
    assert "## Disambiguated names" in readme
    assert f"`Handbook ({disambiguator('w2')})`" in readme


def test_the_cli_summary_counts_disambiguated_names(tmp_path, capsys):
    from connections_export.archive.store import Archive
    from connections_export.cli import ingest_main
    from connections_export.interchange.package import write_package

    model = Interchange(wikis=[_handbook("w1", "a-", "alpha"), _handbook("w2", "b-", "beta")])
    package = tmp_path / "package"
    write_package(
        model, Archive.open(tmp_path / "archive"), package, generated_at="2026-07-21T00:00:00Z"
    )

    code = ingest_main(["--package", str(package), "--output", str(tmp_path / "vault")])

    assert code == 0

    assert "1 name(s) disambiguated" in capsys.readouterr().out


def test_the_console_summary_counts_disambiguated_names(tmp_path):
    from connections_export.gui.routes.ingest import _summary

    model = Interchange(wikis=[_handbook("w1", "a-", "alpha"), _handbook("w2", "b-", "beta")])

    stats = write_obsidian_vault(model, _no_blobs, tmp_path)

    assert "1 name(s) disambiguated" in _summary("obsidian", stats)


def test_nothing_is_reported_when_no_names_collide(tmp_path):
    model = Interchange(wikis=[_handbook("w1", "a-", "alpha")])

    stats = write_obsidian_vault(model, _no_blobs, tmp_path)

    assert stats.disambiguated == []
    assert "Disambiguated" not in _read(tmp_path / "README.md")
    # a note name held once in the vault is linked by name alone, as ever
    assert "[[Home|back home]]" in _read(tmp_path / "Handbook" / "Home" / "Setup.md")


def test_the_same_capture_always_yields_the_same_vault(tmp_path):
    def model() -> Interchange:
        return Interchange(
            wikis=[
                _handbook("w1", "a-", "alpha"),
                _handbook("w2", "b-", "beta"),
                _handbook("w3", "c-", "gamma"),
            ]
        )

    write_obsidian_vault(model(), _no_blobs, tmp_path / "one")
    write_obsidian_vault(model(), _no_blobs, tmp_path / "two")

    _same_tree(tmp_path / "one", tmp_path / "two")
    assert len([path for path in (tmp_path / "one").iterdir() if path.is_dir()]) == 3


# --- combined exports -------------------------------------------------------------------


def test_same_titled_wikis_from_two_deployments_combine_into_two_folders(tmp_path):
    """A test and a production deployment each hold "Handbook", under the
    same ids. Combining keeps them apart (different deployments), and the
    vault must too: one folder each, each wiki's links inside its own."""

    def archive(base_url: str, words: str) -> Interchange:
        return Interchange(base_url=base_url, wikis=[_handbook("w1", "", words)])

    source = CombinedSource(
        [
            CombineInput(
                label="test",
                model=archive("https://test.example.com", "alpha"),
                blob_reader=_no_blobs,
            ),
            CombineInput(
                label="production",
                model=archive("https://prod.example.com", "beta"),
                blob_reader=_no_blobs,
            ),
        ]
    )
    renamed_wiki = source.combine_report.collisions[0].renamed_to
    assert renamed_wiki == "w1~2"

    from_source_for_format(source, tmp_path, "obsidian")

    second = f"Handbook ({disambiguator(renamed_wiki)})"
    assert "alpha home" in _read(tmp_path / "Handbook" / "Home.md")
    assert "beta home" in _read(tmp_path / second / "Home.md")
    assert "[[Handbook/Home|back home]]" in _read(tmp_path / "Handbook" / "Home" / "Setup.md")
    assert f"[[{second}/Home|back home]]" in _read(tmp_path / second / "Home" / "Setup.md")
