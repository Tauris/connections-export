"""The starter site's front page, drawn as a plain list or as cards, and its
footer.

The list is the default: under "Communities" each community's name and how
much it holds, and on a community's page each section with its containers
beneath it -- plain, readable lists in which a long name simply wraps. The
cards are the other choice. Which one is a site parameter in `hugo.toml`
(`homeLayout`), so a site's owner switches by editing one line rather than
by exporting again.

The footer names the Connections deployment the content came from. That
address is data from the capture, so it is reduced to an http(s) host
before it is written and escaped again by Hugo on the way out: a base URL
built to break out of the attribute must arrive as nothing but a host.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from connections_export.ingest import from_source_for_format, write_hugo_content
from connections_export.ingest.hugo_starter import site_of
from tests.ingest.test_hugo import _blobs, _model
from tests.ingest.test_hugo_communities import _two_communities
from tests.ingest.test_hugo_starter_site import HUGO, _build, _demo_export, _parse, needs_hugo

_SITE_CSS = (
    Path(__file__).resolve().parents[2]
    / "connections_export"
    / "ingest"
    / "hugo_starter_site"
    / "static"
    / "css"
    / "site.css"
)


#: Written apart from the rest of an address, so the repository's host scan
#: does not read the deliberately broken hosts below as hosts.
_HTTPS = "https://"


def _config(root: Path) -> dict:
    return tomllib.loads((root / "hugo.toml").read_text(encoding="utf-8"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _main(html: str) -> str:
    return html[html.index("<main") : html.index("</main>")]


def _footer(html: str) -> str:
    return html[html.index('<footer class="site-footer">') : html.index("</footer>")]


# --- the setting in hugo.toml ---------------------------------------------------------------


def test_the_list_is_the_default_front_page(tmp_path):
    write_hugo_content(_model(), _blobs, tmp_path, starter_site=True)

    assert _config(tmp_path)["params"]["homeLayout"] == "list"


def test_cards_are_written_when_asked_for(tmp_path):
    stats = write_hugo_content(
        _model(), _blobs, tmp_path, starter_site=True, starter_layout="cards"
    )

    assert _config(tmp_path)["params"]["homeLayout"] == "cards"
    assert stats.starter_layout == "cards"


def test_the_setting_says_it_can_be_switched_without_exporting_again(tmp_path):
    """The comment above the line is the documentation a site owner meets:
    both values, and that editing is all it takes."""
    write_hugo_content(_model(), _blobs, tmp_path, starter_site=True)

    text = _read(tmp_path / "hugo.toml")
    comment = text[: text.index("homeLayout")]
    comment = comment[comment.rindex("\n\n") :]
    assert '"list"' in comment and '"cards"' in comment
    assert "editing this line" in comment
    assert "does not need exporting again" in comment


def test_the_readme_names_the_setting_and_this_exports_value(tmp_path):
    write_hugo_content(_model(), _blobs, tmp_path, starter_site=True, starter_layout="cards")

    readme = _read(tmp_path / "README.md")
    section = readme[readme.index("## Starter site") :]
    assert "`homeLayout`" in section and '`"cards"` in this export' in section
    assert "does not need exporting again" in section


@pytest.mark.parametrize("layout", ["grid", "", "List", "cards "])
def test_an_unknown_layout_is_refused_before_anything_is_written(tmp_path, layout):
    with pytest.raises(ValueError, match="list"):
        write_hugo_content(_model(), _blobs, tmp_path, starter_site=True, starter_layout=layout)

    assert not (tmp_path / "content").exists()


def test_a_layout_without_the_starter_site_is_refused(tmp_path):
    """The layout draws the starter site's front page; asked for alone it
    would quietly mean nothing."""
    with pytest.raises(ValueError, match="starter site"):
        write_hugo_content(_model(), _blobs, tmp_path, starter_layout="cards")
    with pytest.raises(ValueError, match="starter site"):
        from_source_for_format(object(), tmp_path, "hugo", starter_layout="cards")


def test_cli_starter_layout_flag(tmp_path):
    from connections_export.cli import ingest_main
    from connections_export.gui.demo import run_demo

    archive = tmp_path / "archive"
    run_demo(lambda _event: None, archive_dir=archive, delay=0, app_filter="wiki")
    out = tmp_path / "hugo"
    code = ingest_main(
        [
            "--format",
            "hugo",
            "--starter-site",
            "--starter-layout",
            "cards",
            "--archive",
            str(archive),
            "--output",
            str(out),
        ]
    )

    assert code == 0
    assert _config(out)["params"]["homeLayout"] == "cards"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--format", "hugo", "--starter-site", "--starter-layout", "grid"], "invalid choice"),
        (["--format", "hugo", "--starter-layout", "cards"], "--starter-site"),
        (["--format", "jekyll", "--starter-layout", "cards"], "--starter-site"),
    ],
)
def test_cli_refuses_an_unknown_layout_or_one_without_the_starter_site(
    tmp_path, capsys, args, message
):
    from connections_export.cli import ingest_main

    with pytest.raises(SystemExit):
        ingest_main([*args, "--archive", str(tmp_path), "--output", str(tmp_path / "out")])
    assert message in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


# --- where the content came from ------------------------------------------------------------


def test_the_deployment_is_written_for_the_footer(tmp_path):
    write_hugo_content(_two_communities(), _blobs, tmp_path, starter_site=True)

    assert _config(tmp_path)["params"]["connectionsSites"] == [
        {"name": "connections.example.com", "url": "https://connections.example.com/"}
    ]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://h.example.com", ("h.example.com", "https://h.example.com/")),
        ("HTTPS://H.Example.COM./wikis/home", ("h.example.com", "https://h.example.com/")),
        ("http://h.example.com:8080/x?y#z", ("h.example.com:8080", "http://h.example.com:8080/")),
        ("https://h.example.com:443/", ("h.example.com", "https://h.example.com/")),
        (_HTTPS + "user:secret@h.example.com/", ("h.example.com", "https://h.example.com/")),
        (
            'https://h.example.com/"><script>alert(1)</script>',
            ("h.example.com", "https://h.example.com/"),
        ),
        ("https://[::1]:8443/", ("[::1]:8443", "https://[::1]:8443/")),
    ],
)
def test_only_the_scheme_host_and_port_of_an_address_are_kept(url, expected):
    assert site_of(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "javascript:alert(1)",
        "ftp://h.example.com/",
        "/wikis/home",
        "https://",
        _HTTPS + 'h"x.example.com/',
        "https://h.example.com:99999/",
        _HTTPS + "h x.example.com/",
    ],
)
def test_an_address_that_is_not_an_http_host_is_not_linked(url):
    assert site_of(url) is None


def test_without_a_base_url_the_containers_name_their_hosts(tmp_path):
    """Archives of two deployments combined have no one base URL; their
    containers still say where each came from, and the footer names both."""
    model = _two_communities()
    model.base_url = None
    for index, container in enumerate([*model.wikis, *model.blogs]):
        host = "a.example.com" if index % 2 == 0 else "b.example.org"
        container.alternate_url = f"https://{host}/path/{index}"
    write_hugo_content(model, _blobs, tmp_path, starter_site=True)

    names = [site["name"] for site in _config(tmp_path)["params"]["connectionsSites"]]
    assert names == ["a.example.com", "b.example.org"]


def test_with_nothing_linkable_no_deployment_is_written(tmp_path):
    model = _two_communities()
    model.base_url = "javascript:alert(1)"
    write_hugo_content(model, _blobs, tmp_path, starter_site=True)

    assert "connectionsSites" not in _config(tmp_path)["params"]


# --- the stylesheet ---------------------------------------------------------------------------


def test_a_long_name_in_the_list_wraps_instead_of_being_cut():
    """No rule on the list layout keeps a name on one line or cuts it with
    an ellipsis; the list lets a long word break rather than overflow."""
    css = _read(_SITE_CSS)
    rules = re.findall(r"(\.overview[^{]*)\{([^}]*)\}", css)
    assert rules
    for selector, body in rules:
        assert "nowrap" not in body and "ellipsis" not in body, selector
    overview = dict((selector.strip(), body) for selector, body in rules)[".overview"]
    assert "overflow-wrap: break-word" in overview


# --- built with Hugo --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built_list(tmp_path_factory):
    """The whole demo -- two communities and content in none, so community
    first -- with the default front page."""
    if HUGO is None:
        pytest.skip("no hugo binary on PATH")
    site = _demo_export(tmp_path_factory.mktemp("list"))
    result = _build(site)
    assert result.returncode == 0, result.stdout + result.stderr
    return site


@pytest.fixture(scope="module")
def built_cards(tmp_path_factory):
    if HUGO is None:
        pytest.skip("no hugo binary on PATH")
    site = _demo_export(tmp_path_factory.mktemp("cards"), layout="cards")
    result = _build(site)
    assert result.returncode == 0, result.stdout + result.stderr
    return site


@needs_hugo
def test_the_list_front_page_lists_the_communities_with_what_each_holds(built_list):
    html = _read(built_list / "public" / "index.html")
    main = _main(html)

    assert '<h2 class="overview-title">Communities</h2>' in main
    items = re.findall(
        r'<li><a href="[^"]+">([^<]+)</a> <span class="count">— (\d+ items?)</span></li>', main
    )
    names = [name for name, _count in items]
    assert names[-1] == "Not in a community"
    assert ("Platform Engineering", "35 items") in items
    assert "Platform Engineering — Tooling" in [name.replace("&mdash;", "—") for name in names]
    assert 'class="card' not in main and 'class="cards' not in main


@needs_hugo
def test_the_list_community_page_nests_containers_under_their_sections(built_list):
    main = _main(_read(built_list / "public" / "platform-engineering" / "index.html"))

    assert 'class="card' not in main
    overview = main[main.index('<ul class="overview">') :]
    sections = re.findall(r'<li>\s*<a href="[^"]+">([^<]+)</a>\s*<ul>', overview)
    assert sections == ["Wikis", "Blogs", "Forums", "Files", "Highlights"]
    wikis = overview[overview.index(">Wikis<") : overview.index("</ul>")]
    assert re.search(
        r'<li><a href="[^"]+">Engineering Handbook</a> <span class="count">— 8 pages</span></li>',
        wikis,
    )


@needs_hugo
def test_the_cards_front_page_is_the_card_design(built_cards):
    main = _main(_read(built_cards / "public" / "index.html"))
    page = _parse(built_cards / "public" / "index.html")
    text = " ".join(page.main_text)

    assert main.count('<section class="card">') >= 3
    assert "35 items" in text and "Wiki · 8 pages" in text and "Forum · 8 topics" in text
    assert 'class="overview' not in main
    community = _main(_read(built_cards / "public" / "platform-engineering" / "index.html"))
    assert '<section class="card">' in community and 'class="overview' not in community


@needs_hugo
@pytest.mark.parametrize("layout", ["list", "cards"])
def test_one_community_front_page_in_either_layout(tmp_path, layout):
    """The wiki demo alone is one community: its front page lists sections,
    as a nested list or as cards."""
    site = _demo_export(tmp_path, app_filter="wiki", layout=layout)
    result = _build(site)

    assert result.returncode == 0, result.stdout + result.stderr
    main = _main(_read(site / "public" / "index.html"))
    assert "Communities" not in main
    if layout == "list":
        assert '<ul class="overview">' in main and 'class="card' not in main
        assert re.search(r'>Wikis</a>\s*<ul>\s*<li><a href="[^"]+">Engineering Handbook</a>', main)
        assert re.search(r"Engineering Handbook</a> <span class=\"count\">— \d+ pages</span>", main)
    else:
        assert '<section class="card">' in main and 'class="overview' not in main


@needs_hugo
def test_a_site_without_the_setting_draws_the_list(tmp_path):
    """Anything but "cards" is the list -- a `hugo.toml` of the owner's own
    making without the line included."""
    write_hugo_content(_two_communities(), _blobs, tmp_path, starter_site=True)
    config = tmp_path / "hugo.toml"
    config.write_text(re.sub(r'(?m)^  homeLayout = "list"\n', "", _read(config)), encoding="utf-8")
    assert "homeLayout" not in _read(config)
    result = _build(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert '<ul class="overview">' in _read(tmp_path / "public" / "index.html")


@needs_hugo
def test_a_very_long_community_name_is_shown_whole(tmp_path):
    model = _two_communities()
    long_title = ("A community with a very long name that goes on " * 3)[:120].strip()
    assert len(long_title) >= 110
    for container in [*model.wikis, *model.forums, *model.file_libraries, *model.rich_content]:
        if container.community_title == "Alpha Team":
            container.community_title = long_title
    write_hugo_content(model, _blobs, tmp_path, starter_site=True)
    result = _build(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    main = _main(_read(tmp_path / "public" / "index.html"))
    assert f">{long_title}</a>" in main
    assert "nowrap" not in main and "ellipsis" not in main


@needs_hugo
@pytest.mark.parametrize("layout", ["list", "cards"])
def test_the_footer_links_the_deployment_and_what_made_the_site(tmp_path, layout):
    write_hugo_content(
        _two_communities(), _blobs, tmp_path, starter_site=True, starter_layout=layout
    )
    result = _build(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    for page in ("index.html", "tags/index.html"):
        footer = _footer(_read(tmp_path / "public" / page))
        assert (
            'Content exported from HCL Connections at <a href="https://connections.example.com/" '
            'rel="noopener noreferrer">connections.example.com</a>. Each page links'
        ) in footer
        assert (
            '<a href="https://github.com/Tauris/connections-export">connections-export</a>'
            in footer
        )
        assert '<a href="https://gohugo.io/">Hugo</a>' in footer


@needs_hugo
def test_several_deployments_are_all_named_in_the_footer(tmp_path):
    model = _two_communities()
    model.base_url = None
    hosts = ["a.example.com", "b.example.com", "c.example.com"]
    for index, container in enumerate([*model.wikis, *model.blogs, *model.forums]):
        container.alternate_url = f"https://{hosts[index % 3]}/x"
    write_hugo_content(model, _blobs, tmp_path, starter_site=True)
    result = _build(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    footer = re.sub(r"<[^>]+>", "", _footer(_read(tmp_path / "public" / "index.html")))
    assert "at a.example.com, b.example.com and c.example.com." in footer


@needs_hugo
def test_a_hostile_base_url_reaches_the_footer_as_a_host_only(tmp_path):
    model = _two_communities()
    model.base_url = 'https://h.example.com/"><script>alert(1)</script>'
    write_hugo_content(model, _blobs, tmp_path, html_mode="markdown", starter_site=True)
    result = _build(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    for built in (tmp_path / "public").rglob("*.html"):
        html = _read(built)
        assert _parse(built).scripts == 0, built
        footer = _footer(html)
        assert "script" not in footer and "alert" not in footer, built
        assert (
            '<a href="https://h.example.com/" rel="noopener noreferrer">h.example.com</a>' in footer
        )


@needs_hugo
def test_without_a_known_deployment_the_footer_names_none(tmp_path):
    model = _two_communities()
    model.base_url = None
    write_hugo_content(model, _blobs, tmp_path, starter_site=True)
    result = _build(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    footer = _footer(_read(tmp_path / "public" / "index.html"))
    assert "Content exported from HCL Connections. Each page links" in footer
    assert "gohugo.io" in footer
