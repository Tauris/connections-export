"""A Hugo export holding more than one community is written community first.

One community's wikis, blogs and forums side by side under `content/wikis/`,
`content/blogs/` ... is how a reader thinks of one community. Several
communities in those same folders would mix them: a reader looking for one
community's content would find it spread across five sections, each also
holding everyone else's. So when the export holds more than one community,
each community gets a folder of its own -- an overview page, then the same
sections as before beneath it -- and content that belongs to no community
gets a folder of its own too. One community, or none, keeps the plain layout
every other test in this folder pins.
"""

from __future__ import annotations

import re

from connections_export.derive.combine import CombinedSource, CombineInput
from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedFile,
    DerivedFileLibrary,
    DerivedForum,
    DerivedForumTopic,
    DerivedPage,
    DerivedRichContent,
    DerivedRichContentPage,
    DerivedWiki,
    Interchange,
    LinkRef,
    ResolvedAsset,
)
from connections_export.ingest import from_source_for_format, write_hugo_content
from connections_export.ingest.hugo import _containers
from connections_export.interchange.filenames import disambiguator
from tests.ingest.test_hugo import _front_matter
from tests.ingest.test_hugo_starter_site import _build, _parse, needs_hugo

_DOC = "sha256:" + "d" * 64
_ALPHA = "11111111-1111-4111-8111-111111111111"
_BETA = "22222222-2222-4222-8222-222222222222"
BASE = "https://connections.example.com"


def _blobs(blob_hash: str) -> bytes | None:
    return b"%PDF-1.4 plan" if blob_hash == _DOC else None


def _wiki(wiki_id: str, title: str, community: str | None, community_title: str | None, **links):
    home = DerivedPage(
        id=f"{wiki_id}-home",
        label="home",
        title="Home",
        child_ids=[f"{wiki_id}-child"],
        content_html=links.get("html", "<p>Home.</p>"),
        links=links.get("links", []),
    )
    child = DerivedPage(
        id=f"{wiki_id}-child", label="child", title="Child", parent_id=f"{wiki_id}-home"
    )
    return DerivedWiki(
        id=wiki_id,
        label=wiki_id,
        title=title,
        community_uuid=community,
        community_title=community_title,
        root_page_ids=[home.id],
        pages={home.id: home, child.id: child},
    )


def _two_communities() -> Interchange:
    """Alpha has a wiki, a forum, a file library and Highlights; Beta a wiki
    and a blog. Alpha's wiki home links to Beta's blog post, and the post
    back to Alpha's child page -- links that cross communities."""
    alpha_wiki = _wiki(
        "wa",
        "Alpha Handbook",
        _ALPHA,
        "Alpha Team",
        html='<p>See <a href="/beta/post">the Beta post</a>.</p>',
        links=[LinkRef(original_href="/beta/post", scope="in_export", target_page_id="post")],
    )
    beta_wiki = _wiki("wb", "Beta Handbook", _BETA, "Beta Team")
    post = DerivedBlogPost(
        id="post",
        title="Beta News",
        created="2025-04-02T08:00:00Z",
        content_html='<p>Read <a href="/alpha/child">Alpha\'s child page</a>.</p>',
        links=[LinkRef(original_href="/alpha/child", scope="in_export", target_page_id="wa-child")],
    )
    blog = DerivedBlog(
        id="b",
        title="Beta Blog",
        community_uuid=_BETA,
        community_title="Beta Team",
        post_ids=["post"],
        posts={"post": post},
    )
    topic = DerivedForumTopic(id="t", title="A question", content_html="<p>Why?</p>")
    forum = DerivedForum(
        id="f",
        title="Alpha Forum",
        community_uuid=_ALPHA,
        community_title="Alpha Team",
        topic_ids=["t"],
        topics={"t": topic},
    )
    library = DerivedFileLibrary(
        id="lib",
        title="Files",
        community_uuid=_ALPHA,
        community_title="Alpha Team",
        file_ids=["plan"],
        files={
            "plan": DerivedFile(
                id="plan",
                name="Plan.pdf",
                asset=ResolvedAsset(
                    original_href="/files/plan",
                    resolved_url=BASE + "/files/plan",
                    blob_hash=_DOC,
                    present=True,
                    scope="same",
                ),
            )
        },
    )
    highlights = DerivedRichContent(
        id=_ALPHA,
        title="Highlights",
        community_uuid=_ALPHA,
        community_title="Alpha Team",
        page_ids=["hp"],
        pages={"hp": DerivedRichContentPage(id="hp", resource_id="hp", title="Welcome")},
    )
    return Interchange(
        base_url=BASE,
        wikis=[alpha_wiki, beta_wiki],
        blogs=[blog],
        forums=[forum],
        file_libraries=[library],
        rich_content=[highlights],
    )


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


def _relrefs(content) -> list[tuple[str, str]]:
    return [
        (page.relative_to(content).as_posix(), target)
        for page in content.rglob("*.md")
        for target in re.findall(r'\{\{< relref "([^"]+)" >\}\}', _read(page))
    ]


# --- the layout ---------------------------------------------------------------------------


def test_two_communities_each_get_a_folder_with_their_sections_beneath(tmp_path):
    stats = write_hugo_content(_two_communities(), _blobs, tmp_path)

    content = tmp_path / "content"
    assert sorted(p.name for p in content.iterdir()) == ["alpha-team", "beta-team"]
    alpha, beta = content / "alpha-team", content / "beta-team"
    assert sorted(p.name for p in alpha.iterdir() if p.is_dir()) == [
        "files",
        "forums",
        "highlights",
        "wikis",
    ]
    assert sorted(p.name for p in beta.iterdir() if p.is_dir()) == ["blogs", "wikis"]
    # Everything beneath is laid out exactly as in a one-community export,
    # one level deeper: the wiki tree as bundles, a leaf bundle per post.
    assert (alpha / "wikis" / "alpha-handbook" / "home" / "_index.md").is_file()
    assert (alpha / "wikis" / "alpha-handbook" / "home" / "child" / "index.md").is_file()
    assert (beta / "blogs" / "beta-blog" / "beta-news" / "index.md").is_file()
    assert (alpha / "forums" / "alpha-forum" / "a-question" / "index.md").is_file()
    assert (alpha / "files" / "alpha-team" / "plan.pdf").is_file()
    assert (alpha / "highlights" / "alpha-team" / "welcome" / "index.md").is_file()
    assert stats.communities == 2


def test_a_community_overview_names_the_community_and_counts_its_sections(tmp_path):
    write_hugo_content(_two_communities(), _blobs, tmp_path)

    overview = tmp_path / "content" / "alpha-team" / "_index.md"
    fields = _front_matter(overview)
    assert fields["title"] == "Alpha Team"
    assert fields["weight"] == 1
    assert fields["params"] == {"kind": "community", "source_id": _ALPHA, "item_count": 5}
    body = _read(overview).split("---\n", 2)[2]
    assert '- [Wikis]({{< relref "/alpha-team/wikis/_index.md" >}}): 1 wiki, 2 pages' in body
    assert '- [Forums]({{< relref "/alpha-team/forums/_index.md" >}}): 1 forum, 1 topic' in body
    assert '- [Files]({{< relref "/alpha-team/files/_index.md" >}}): 1 library, 1 file' in body
    assert "- [Highlights]" in body and "1 page" in body
    assert "Blogs" not in body
    assert _front_matter(tmp_path / "content" / "beta-team" / "_index.md")["weight"] == 2


def test_the_sections_beneath_a_community_name_it(tmp_path):
    write_hugo_content(_two_communities(), _blobs, tmp_path)

    section = _front_matter(tmp_path / "content" / "beta-team" / "blogs" / "_index.md")
    assert section == {"title": "Blogs", "weight": 2, "params": {"community": "Beta Team"}}
    wiki = _front_matter(
        tmp_path / "content" / "beta-team" / "wikis" / "beta-handbook" / "_index.md"
    )
    assert wiki["params"]["kind"] == "wiki"
    assert wiki["params"]["community"] == "Beta Team"


def test_a_container_in_no_community_gets_a_folder_of_its_own(tmp_path):
    model = _two_communities()
    model.wikis.append(_wiki("solo", "Personal Notes", None, None))

    write_hugo_content(model, _blobs, tmp_path)

    other = tmp_path / "content" / "other"
    fields = _front_matter(other / "_index.md")
    assert fields["title"] == "Not in a community"
    assert fields["params"]["kind"] == "no_community"
    # Last on the home page, after every community.
    assert fields["weight"] == 3
    assert (other / "wikis" / "personal-notes" / "home" / "_index.md").is_file()
    assert "community" not in _front_matter(other / "wikis" / "_index.md").get("params", {})


def test_two_communities_with_the_same_title_get_distinct_stable_folders(tmp_path):
    """The second folder carries a tag of the community's own id, not a
    counter: the same export writes the same names every time, and a
    community added later does not rename one already there."""
    model = _two_communities()
    for container in (*model.wikis, *model.blogs):
        if container.community_uuid == _BETA:
            container.community_title = "Alpha Team"

    write_hugo_content(model, _blobs, tmp_path / "one")
    write_hugo_content(model, _blobs, tmp_path / "two")

    names = sorted(p.name for p in (tmp_path / "one" / "content").iterdir())
    assert names == ["alpha-team", f"alpha-team-{disambiguator(_BETA)}"]
    assert names == sorted(p.name for p in (tmp_path / "two" / "content").iterdir())


def test_a_community_cannot_take_a_name_the_layout_needs(tmp_path):
    """`tags` is where Hugo puts its tag pages and `other` holds content in
    no community; a community called either gets a tagged folder instead."""
    model = _two_communities()
    for container in (*model.wikis, *model.blogs, *model.forums):
        container.community_title = "Tags" if container.community_uuid == _ALPHA else "Other"
    model.wikis.append(_wiki("solo", "Personal Notes", None, None))

    write_hugo_content(model, _blobs, tmp_path)

    names = sorted(p.name for p in (tmp_path / "content").iterdir())
    assert names == sorted(
        ["other", f"tags-{disambiguator(_ALPHA)}", f"other-{disambiguator(_BETA)}"]
    )


def test_links_between_communities_are_relrefs_that_resolve(tmp_path):
    write_hugo_content(_two_communities(), _blobs, tmp_path)

    content = tmp_path / "content"
    home = _read(content / "alpha-team" / "wikis" / "alpha-handbook" / "home" / "_index.md")
    post = _read(content / "beta-team" / "blogs" / "beta-blog" / "beta-news" / "index.md")
    assert '{{< relref "/beta-team/blogs/beta-blog/beta-news/index.md" >}}' in home
    assert '{{< relref "/alpha-team/wikis/alpha-handbook/home/child/index.md" >}}' in post
    relrefs = _relrefs(content)
    assert len(relrefs) >= 8
    missing = [(page, target) for page, target in relrefs if not (content / target[1:]).is_file()]
    assert not missing


# --- when it does not apply ----------------------------------------------------------------


def test_one_community_and_content_in_none_keep_the_plain_layout(tmp_path):
    model = _two_communities()
    model.blogs = []
    model.wikis = [model.wikis[0], _wiki("solo", "Personal Notes", None, None)]

    stats = write_hugo_content(model, _blobs, tmp_path)

    names = sorted(p.name for p in (tmp_path / "content").iterdir())
    assert names == ["files", "forums", "highlights", "wikis"]
    assert (tmp_path / "content" / "wikis" / "personal-notes" / "_index.md").is_file()
    assert stats.communities == 0
    readme = _read(tmp_path / "README.md")
    assert "## Communities" not in readme and "`content/wikis/<wiki>/`" in readme


# --- combining, and an existing folder --------------------------------------------------------


def test_archives_from_two_communities_combine_into_a_community_first_export(tmp_path):
    """Two archives, one community each: combined, they hold two, and the
    links between them -- resolved by the combining step -- land on the
    pages' community-first paths."""
    alpha = Interchange(
        base_url=BASE,
        wikis=[
            _wiki(
                "wa",
                "Alpha Handbook",
                _ALPHA,
                "Alpha Team",
                html=f'<p><a href="{BASE}/blogs/beta/entry/news">news</a></p>',
                links=[
                    LinkRef(
                        original_href=f"{BASE}/blogs/beta/entry/news",
                        resolved_url=f"{BASE}/blogs/beta/entry/news",
                        scope="hcl_deployment",
                    )
                ],
            )
        ],
    )
    post = DerivedBlogPost(id="news", title="News", alternate_url=f"{BASE}/blogs/beta/entry/news")
    beta = Interchange(
        base_url=BASE,
        blogs=[
            DerivedBlog(
                id="b",
                title="Beta Blog",
                community_uuid=_BETA,
                community_title="Beta Team",
                post_ids=["news"],
                posts={"news": post},
            )
        ],
    )
    source = CombinedSource(
        [
            CombineInput(label="alpha", model=alpha, blob_reader=lambda _d: None),
            CombineInput(label="beta", model=beta, blob_reader=lambda _d: None),
        ]
    )

    from_source_for_format(source, tmp_path, "hugo")

    content = tmp_path / "content"
    assert sorted(p.name for p in content.iterdir()) == ["alpha-team", "beta-team"]
    home = _read(content / "alpha-team" / "wikis" / "alpha-handbook" / "home" / "_index.md")
    assert '[news]({{< relref "/beta-team/blogs/beta-blog/news/index.md" >}})' in home
    assert '  source_archive: "alpha"' in home


def test_nothing_already_in_the_folder_is_deleted(tmp_path):
    """The export may be pointed at a site that already exists, or at an
    earlier export in the other layout: what is there stays."""
    earlier = tmp_path / "content" / "wikis" / "old-wiki" / "index.md"
    earlier.parent.mkdir(parents=True)
    earlier.write_text("---\ntitle: Old\n---\n", encoding="utf-8")
    mine = tmp_path / "content" / "about.md"
    mine.write_text("mine\n", encoding="utf-8")

    write_hugo_content(_two_communities(), _blobs, tmp_path)

    assert _read(earlier) == "---\ntitle: Old\n---\n"
    assert _read(mine) == "mine\n"
    assert (tmp_path / "content" / "alpha-team" / "_index.md").is_file()


# --- the README ------------------------------------------------------------------------------


def test_the_readme_explains_the_community_first_layout(tmp_path):
    write_hugo_content(_two_communities(), _blobs, tmp_path)

    readme = _read(tmp_path / "README.md")
    section = readme[readme.index("## Communities") :]
    assert "`content/<community>/`" in section
    assert "more than one community" in section
    assert "`content/other/`" in section
    assert "fresh folder" in section and "left in place" in section
    assert "`content/<community>/wikis/<wiki>/`" in readme
    assert "(`/<community>/wikis/…/index.md`)" in readme
    assert "`community`" in readme and "`no_community`" in readme
    assert "`params.item_count`" in readme


def test_the_site_title_stays_generic_for_several_communities(tmp_path):
    write_hugo_content(_two_communities(), _blobs, tmp_path, starter_site=True)

    config = _read(tmp_path / "hugo.toml")
    assert 'title = "Connections content"' in config


# --- the starter site, built -------------------------------------------------------------------


@needs_hugo
def test_a_community_title_is_text_in_the_built_site(tmp_path):
    """A community's title is its owner's: on the front page, in the header,
    on its own page and in every breadcrumb it is escaped like any title."""
    model = _two_communities()
    hostile = "<script>alert(1)</script><img src=x onerror=alert(2)> {{< x >}}"
    for container in _containers(model):
        if container.community_uuid == _ALPHA:
            container.community_title = hostile

    write_hugo_content(model, _blobs, tmp_path, html_mode="markdown", starter_site=True)
    result = _build(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    built = list((tmp_path / "public").rglob("*.html"))
    assert len(built) > 10
    for page in built:
        html = page.read_text(encoding="utf-8")
        assert _parse(page).scripts == 0, page
        assert not re.search(r"<img[^>]*onerror", html), page
    home = (tmp_path / "public" / "index.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in home
