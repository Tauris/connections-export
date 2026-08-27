"""Tasks 3 and 4: tree assembly from small, hand-built archives --
no crawl, no HTTP. Each archive is built directly with `Archive.
write_response`/`write_failure` at the exact URLs `connections_export.
adapters.wikis`' own builders produce, using `connections_export.fakeserver`'s
Atom/JSON serializers for realistic bytes.
"""

import json

import pytest

from connections_export import paging
from connections_export.adapters.model import Comment
from connections_export.adapters.wikis import (
    artifacts_url,
    nav_feed_url,
    page_entry_url,
    wikis_feed_url,
)
from connections_export.archive.records import Outcome
from connections_export.archive.store import Archive
from connections_export.derive.assemble import DeriveError, derive, thread_comments
from connections_export.fakeserver import atom as fake_atom
from connections_export.fakeserver import navjson
from connections_export.fakeserver.model import (
    FakeAttachment,
    FakeComment,
    FakePage,
    FakeVersion,
    FakeWiki,
    WikiSet,
)
from tests.archive.conftest import FakeResponse

BASE_URL = "https://fake"
AUTH_ROOT = "basic"
PAGE_SIZE = 500


def _page(
    *,
    uuid,
    label,
    parent_uuid,
    ordinal,
    title=None,
    body_html="<p>body</p>",
    comments=(),
    versions=(),
    attachments=(),
):
    return FakePage(
        uuid=uuid,
        label=label,
        title=title or label,
        parent_uuid=parent_uuid,
        ordinal=ordinal,
        body_html=body_html,
        created="2026-01-01T00:00:00Z",
        modified="2026-01-02T00:00:00Z",
        version_label=max(len(versions), 1),
        author="Fake Author",
        comments=list(comments),
        versions=list(versions),
        attachments=list(attachments),
    )


class _ArchiveBuilder:
    def __init__(self, tmp_path):
        self.archive = Archive.open(tmp_path / "archive")

    def write(self, url, content, *, content_type="application/atom+xml"):
        self.archive.write_response(
            FakeResponse(
                url=url,
                method="GET",
                status=200,
                headers={"Content-Type": content_type},
                content=content,
            ),
            fetched_at="2026-01-01T00:00:00Z",
        )

    def fail(self, url, *, error="simulated failure"):
        self.archive.write_failure(
            url=url,
            method="GET",
            fetched_at="2026-01-01T00:00:00Z",
            outcome=Outcome.transport_error,
            error=error,
        )


def _seed_wiki_and_nav(builder: _ArchiveBuilder, wiki: FakeWiki) -> None:
    wikiset = WikiSet(wikis=[wiki])
    wikis_url = f"{wikis_feed_url(base_url=BASE_URL, auth_root=AUTH_ROOT)}?ps={PAGE_SIZE}&page=1"
    builder.write(
        wikis_url,
        fake_atom.wikis_feed(wikiset, auth_root=AUTH_ROOT, base_url=BASE_URL, ps=PAGE_SIZE, page=1),
    )
    nav_base = nav_feed_url(base_url=BASE_URL, auth_root=AUTH_ROOT, wiki_label=wiki.label)
    nav_url = f"{nav_base}?tree=true&acls=true"
    nav_data = navjson.nav_feed(wiki, tree=True, timestamp=0)
    builder.write(nav_url, json.dumps(nav_data).encode(), content_type="application/json")


def _seed_page_entry(builder: _ArchiveBuilder, wiki: FakeWiki, page: FakePage) -> bytes:
    entry_bytes = fake_atom.page_entry(wiki, page, auth_root=AUTH_ROOT, base_url=BASE_URL)
    url = page_entry_url(
        base_url=BASE_URL, auth_root=AUTH_ROOT, wiki_label=wiki.label, page_label=page.label
    )
    builder.write(url, entry_bytes)
    return entry_bytes


def _artifacts_page1_url(wiki: FakeWiki, page: FakePage, *, category: str) -> str:
    base = artifacts_url(
        base_url=BASE_URL,
        auth_root=AUTH_ROOT,
        wiki_label=wiki.label,
        page_label=page.label,
        category=category,
    )
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}ps={PAGE_SIZE}&page=1"


def _seed_artifacts(builder: _ArchiveBuilder, wiki: FakeWiki, page: FakePage) -> None:
    # Comments: written at the plain feed URL (no `category=`) --
    # `assemble.py` prefers the page entry's own `replies_url` (link-
    # following first, project.md principle 4), which is exactly that
    # plain URL; fakeserver's category-less feed route defaults to
    # comments, so this is what a real crawl actually archives there.
    replies_base = artifacts_url(
        base_url=BASE_URL,
        auth_root=AUTH_ROOT,
        wiki_label=wiki.label,
        page_label=page.label,
        category=None,
    )
    comment_content = fake_atom.artifacts_feed(
        wiki,
        page,
        category="comment",
        auth_root=AUTH_ROOT,
        base_url=BASE_URL,
        ps=PAGE_SIZE,
        page_num=1,
    )
    builder.write(f"{replies_base}?ps={PAGE_SIZE}&page=1", comment_content)

    for category in ("version", "attachment"):
        content = fake_atom.artifacts_feed(
            wiki,
            page,
            category=category,
            auth_root=AUTH_ROOT,
            base_url=BASE_URL,
            ps=PAGE_SIZE,
            page_num=1,
        )
        builder.write(_artifacts_page1_url(wiki, page, category=category), content)


def _seed_body(
    builder: _ArchiveBuilder, wiki: FakeWiki, page: FakePage, entry_bytes: bytes
) -> None:
    from connections_export.adapters.wikis import parse_page_entry

    parsed = parse_page_entry(entry_bytes)
    if parsed.content_src:
        builder.write(parsed.content_src, page.body_html.encode(), content_type="text/html")


def _seed_full_page(builder: _ArchiveBuilder, wiki: FakeWiki, page: FakePage) -> None:
    entry_bytes = _seed_page_entry(builder, wiki, page)
    _seed_body(builder, wiki, page, entry_bytes)
    _seed_artifacts(builder, wiki, page)


def _wiki(pages, *, uuid="wiki-uuid", label="wiki0") -> FakeWiki:
    return FakeWiki(
        uuid=uuid,
        label=label,
        title="Wiki 0",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        pages=list(pages),
    )


# --- 3.1: nav feed drives the tree; entry/body/comments/versions/attachments attached


def test_tree_and_order_come_from_nav_feed_and_artifacts_are_attached(tmp_path):
    root = _page(uuid="root-uuid", label="root", parent_uuid=None, ordinal=0)
    child_b = _page(
        uuid="b-uuid",
        label="b",
        parent_uuid="root-uuid",
        ordinal=1,
        comments=[
            FakeComment(uuid="c1", author="A", content_html="<p>hi</p>", published="p", updated="u")
        ],
        versions=[
            FakeVersion(
                uuid="v1", version_label=1, author="A", created="c", content_html="<p>v1</p>"
            )
        ],
        attachments=[
            FakeAttachment(
                uuid="a1",
                filename="f.bin",
                content_type="application/octet-stream",
                size=1,
                author="A",
                created="c",
            )
        ],
    )
    child_a = _page(uuid="a-uuid", label="a", parent_uuid="root-uuid", ordinal=0)
    wiki = _wiki([root, child_a, child_b])

    builder = _ArchiveBuilder(tmp_path)
    _seed_wiki_and_nav(builder, wiki)
    for page in wiki.pages:
        _seed_full_page(builder, wiki, page)

    interchange = derive(builder.archive)

    assert len(interchange.wikis) == 1
    derived_wiki = interchange.wikis[0]
    assert derived_wiki.id == "wiki-uuid"
    assert derived_wiki.label == "wiki0"
    assert derived_wiki.root_page_ids == ["root-uuid"]

    root_page = derived_wiki.pages["root-uuid"]
    assert root_page.parent_id is None
    assert root_page.child_ids == ["a-uuid", "b-uuid"]  # ordinal order, not insertion order

    a_page = derived_wiki.pages["a-uuid"]
    b_page = derived_wiki.pages["b-uuid"]
    assert a_page.parent_id == "root-uuid"
    assert a_page.ordinal == 0
    assert b_page.ordinal == 1

    # entry/body/author/dates attached
    assert b_page.title == "b"
    assert b_page.author == "Fake Author"
    assert b_page.created == "2026-01-01T00:00:00Z"
    assert b_page.content_html == "<p>body</p>"

    # provenance carries source url + blob hash, never leaking into the shape itself
    assert b_page.provenance.source_url == page_entry_url(
        base_url=BASE_URL, auth_root=AUTH_ROOT, wiki_label="wiki0", page_label="b"
    )
    assert b_page.provenance.blob_hash is not None
    assert b_page.provenance.hcl_id == "b-uuid"

    # comments/versions/attachments attached
    assert len(b_page.comments) == 1
    assert b_page.comments[0].id == "c1"
    assert len(b_page.versions) == 1
    assert len(b_page.attachments) == 1
    assert b_page.attachments[0].filename == "f.bin"


def test_duplicate_ordinals_keep_stable_relative_order(tmp_path):
    root = _page(uuid="root-uuid", label="root", parent_uuid=None, ordinal=0)
    # Three children sharing ordinal 0 -- duplicate-ordinals nasty case.
    kids = [
        _page(uuid=f"k{i}-uuid", label=f"k{i}", parent_uuid="root-uuid", ordinal=0)
        for i in range(3)
    ]
    wiki = _wiki([root, *kids])

    builder = _ArchiveBuilder(tmp_path)
    _seed_wiki_and_nav(builder, wiki)
    for page in wiki.pages:
        _seed_full_page(builder, wiki, page)

    interchange = derive(builder.archive)
    root_page = interchange.wikis[0].pages["root-uuid"]

    assert root_page.child_ids == ["k0-uuid", "k1-uuid", "k2-uuid"]


# --- 3.2: hierarchy cross-check is soft -- nav wins, discrepancy recorded, never raises


def test_page_entry_parent_disagreement_is_recorded_not_fatal(tmp_path):
    root = _page(uuid="root-uuid", label="root", parent_uuid=None, ordinal=0)
    child = _page(uuid="child-uuid", label="child", parent_uuid="root-uuid", ordinal=0)
    wiki = _wiki([root, child])

    builder = _ArchiveBuilder(tmp_path)
    _seed_wiki_and_nav(builder, wiki)
    for page in wiki.pages:
        _seed_full_page(builder, wiki, page)

    # Overwrite the nav feed so `child` now appears as a top-level page
    # (its `parElem` points at the tree root) even though its own
    # (already-archived) page entry still carries td:parentUuid=root-uuid
    # -- the disagreement.
    nav_data = navjson.nav_feed(wiki, tree=True, timestamp=0)
    for item in nav_data["items"]:
        if item.get("id") == "child-uuid":
            item["parElem"] = nav_data["identifier"]  # -> top-level
    nav_base = nav_feed_url(base_url=BASE_URL, auth_root=AUTH_ROOT, wiki_label=wiki.label)
    nav_url = f"{nav_base}?tree=true&acls=true"
    builder.write(nav_url, json.dumps(nav_data).encode(), content_type="application/json")

    interchange = derive(builder.archive)  # must not raise

    derived_wiki = interchange.wikis[0]
    child_page = derived_wiki.pages["child-uuid"]
    assert child_page.parent_id is None  # nav parent kept
    assert "child-uuid" in derived_wiki.root_page_ids
    assert child_page.parent_discrepancy is not None
    assert "root-uuid" in child_page.parent_discrepancy


def test_no_discrepancy_recorded_when_sources_agree(tmp_path):
    root = _page(uuid="root-uuid", label="root", parent_uuid=None, ordinal=0)
    child = _page(uuid="child-uuid", label="child", parent_uuid="root-uuid", ordinal=0)
    wiki = _wiki([root, child])

    builder = _ArchiveBuilder(tmp_path)
    _seed_wiki_and_nav(builder, wiki)
    for page in wiki.pages:
        _seed_full_page(builder, wiki, page)

    interchange = derive(builder.archive)

    assert interchange.wikis[0].pages["child-uuid"].parent_discrepancy is None


# --- 3.3: a failed/unparsed/missing piece is represented, never dropped


def test_page_with_failed_body_gets_empty_body_and_a_note(tmp_path):
    page = _page(uuid="p1-uuid", label="p1", parent_uuid=None, ordinal=0)
    wiki = _wiki([page])

    builder = _ArchiveBuilder(tmp_path)
    _seed_wiki_and_nav(builder, wiki)
    entry_bytes = _seed_page_entry(builder, wiki, page)
    from connections_export.adapters.wikis import parse_page_entry

    parsed = parse_page_entry(entry_bytes)
    builder.fail(parsed.content_src, error="connection reset")
    _seed_artifacts(builder, wiki, page)

    interchange = derive(builder.archive)
    derived = interchange.wikis[0].pages["p1-uuid"]

    assert derived.content_html == ""
    assert derived.provenance.note is not None
    assert "body" in derived.provenance.note
    assert "connection reset" in derived.provenance.note


def test_page_entry_never_archived_is_omitted_from_reduced_model(tmp_path):
    # Two top-level pages so nav-derived ordinal (sibling position, not
    # the fake's own literal FakePage.ordinal value) is meaningful.
    p0 = _page(uuid="p0-uuid", label="p0", parent_uuid=None, ordinal=0)
    p1 = _page(uuid="p1-uuid", label="p1", parent_uuid=None, ordinal=1)
    wiki = _wiki([p0, p1])

    builder = _ArchiveBuilder(tmp_path)
    _seed_wiki_and_nav(builder, wiki)
    _seed_full_page(builder, wiki, p0)
    # Deliberately never seed p1's page entry at all.

    interchange = derive(builder.archive)

    assert set(interchange.wikis[0].pages) == {"p0-uuid"}


def test_page_entry_that_fails_to_fetch_records_the_failure_reason(tmp_path):
    page = _page(uuid="p1-uuid", label="p1", parent_uuid=None, ordinal=0)
    wiki = _wiki([page])

    builder = _ArchiveBuilder(tmp_path)
    _seed_wiki_and_nav(builder, wiki)
    entry_url = page_entry_url(
        base_url=BASE_URL, auth_root=AUTH_ROOT, wiki_label=wiki.label, page_label="p1"
    )
    builder.fail(entry_url, error="HTTP 500")

    interchange = derive(builder.archive)
    derived = interchange.wikis[0].pages["p1-uuid"]

    assert derived.provenance.note is not None
    assert "HTTP 500" in derived.provenance.note


def test_page_entry_malformed_xml_is_unparsed_not_fatal(tmp_path):
    page = _page(uuid="p1-uuid", label="p1", parent_uuid=None, ordinal=0)
    wiki = _wiki([page])

    builder = _ArchiveBuilder(tmp_path)
    _seed_wiki_and_nav(builder, wiki)
    entry_url = page_entry_url(
        base_url=BASE_URL, auth_root=AUTH_ROOT, wiki_label=wiki.label, page_label="p1"
    )
    builder.write(entry_url, b"<entry><unterminated")

    interchange = derive(builder.archive)  # must not raise
    derived = interchange.wikis[0].pages["p1-uuid"]

    assert derived.content_html == ""
    assert derived.provenance.note is not None
    assert "unparsed" in derived.provenance.note


def test_comments_feed_fetch_failure_is_noted(tmp_path):
    page = _page(uuid="p1-uuid", label="p1", parent_uuid=None, ordinal=0)
    wiki = _wiki([page])

    builder = _ArchiveBuilder(tmp_path)
    _seed_wiki_and_nav(builder, wiki)
    entry_bytes = _seed_page_entry(builder, wiki, page)
    _seed_body(builder, wiki, page, entry_bytes)
    from connections_export.adapters.wikis import parse_page_entry

    parsed = parse_page_entry(entry_bytes)
    # assemble.py prefers the entry's own replies_url (link-following
    # first, project.md principle 4) over constructing a `?category=
    # comment` URL, and it carries no query string of its own.
    comments_page1_url = f"{parsed.replies_url}?ps={PAGE_SIZE}&page=1"
    builder.fail(comments_page1_url, error="timeout")
    # versions/attachments still fetch fine
    for category in ("version", "attachment"):
        content = fake_atom.artifacts_feed(
            wiki,
            page,
            category=category,
            auth_root=AUTH_ROOT,
            base_url=BASE_URL,
            ps=PAGE_SIZE,
            page_num=1,
        )
        builder.write(_artifacts_page1_url(wiki, page, category=category), content)

    interchange = derive(builder.archive)
    derived = interchange.wikis[0].pages["p1-uuid"]

    assert derived.comments == []
    assert derived.provenance.note is not None
    assert "comments" in derived.provenance.note


def test_a_comments_feed_truncated_at_the_safety_cap_is_noted(tmp_path, monkeypatch):
    """A capped read is a SHORT read, and must not look like a complete one.

    The crawler marks an issue and warns (`pagination_cap`) rather than
    truncate silently. Derive replays that walk and stopped at the same cap
    saying nothing, so an archive holding more comments than the cap allows
    derived a page with fewer comments and no indication why.
    """

    monkeypatch.setattr(paging, "MAX_PAGES", 2)
    # Small enough that two comments fill a page, so the walk still wants a
    # third when the cap stops it -- the situation the cap exists for.
    monkeypatch.setattr("tests.derive.test_assemble.PAGE_SIZE", 2)

    comments = [
        FakeComment(
            uuid=f"c{i}",
            author="A",
            content_html=f"<p>comment {i}</p>",
            published="2026-01-01T00:00:00Z",
            updated="2026-01-01T00:00:00Z",
        )
        for i in range(6)
    ]
    page = _page(uuid="p1-uuid", label="p1", parent_uuid=None, ordinal=0, comments=comments)
    wiki = _wiki([page])

    builder = _ArchiveBuilder(tmp_path)
    _seed_wiki_and_nav(builder, wiki)
    entry_bytes = _seed_page_entry(builder, wiki, page)
    _seed_body(builder, wiki, page, entry_bytes)
    replies_base = artifacts_url(
        base_url=BASE_URL,
        auth_root=AUTH_ROOT,
        wiki_label=wiki.label,
        page_label=page.label,
        category=None,
    )
    # All three pages ARE archived -- the crawler fetched them. Only derive's
    # cap keeps the third from being read, which is the whole point.
    for page_num in (1, 2, 3):
        builder.write(
            f"{replies_base}?ps=2&page={page_num}",
            fake_atom.artifacts_feed(
                wiki,
                page,
                category="comment",
                auth_root=AUTH_ROOT,
                base_url=BASE_URL,
                ps=2,
                page_num=page_num,
            ),
        )
    for category in ("version", "attachment"):
        builder.write(
            _artifacts_page1_url(wiki, page, category=category),
            fake_atom.artifacts_feed(
                wiki,
                page,
                category=category,
                auth_root=AUTH_ROOT,
                base_url=BASE_URL,
                ps=2,
                page_num=1,
            ),
        )

    interchange = derive(builder.archive)
    derived = interchange.wikis[0].pages["p1-uuid"]

    assert len(derived.comments) == 4, "the cap should have stopped the walk after two pages"
    assert derived.provenance.note is not None, "a truncated read left no trace at all"
    assert "comments" in derived.provenance.note
    assert "cap" in derived.provenance.note


# --- wiki whose nav feed itself never archived / unparsed ----------------


def test_wiki_with_no_archived_nav_feed_yields_an_empty_but_present_wiki(tmp_path):
    page = _page(uuid="p1-uuid", label="p1", parent_uuid=None, ordinal=0)
    wiki = _wiki([page])

    builder = _ArchiveBuilder(tmp_path)
    wikiset = WikiSet(wikis=[wiki])
    wikis_url = f"{wikis_feed_url(base_url=BASE_URL, auth_root=AUTH_ROOT)}?ps={PAGE_SIZE}&page=1"
    builder.write(
        wikis_url,
        fake_atom.wikis_feed(wikiset, auth_root=AUTH_ROOT, base_url=BASE_URL, ps=PAGE_SIZE, page=1),
    )
    # Nav feed deliberately never archived.

    interchange = derive(builder.archive)

    assert len(interchange.wikis) == 1
    derived_wiki = interchange.wikis[0]
    assert derived_wiki.id == "wiki-uuid"
    assert derived_wiki.pages == {}
    assert derived_wiki.root_page_ids == []


# --- DeriveError: no wikis-feed entry point at all ------------------------


def test_derive_raises_when_archive_has_no_wikis_feed(tmp_path):
    archive = Archive.open(tmp_path / "archive")

    with pytest.raises(DeriveError):
        derive(archive)


# --- Comment threading, pure-function level ---------------------


def test_comment_with_in_reply_to_gets_parent_comment_id():
    comments = [
        Comment(id="c1", author="A", content_html="<p>root</p>", in_reply_to=None),
        Comment(id="c2", author="B", content_html="<p>reply</p>", in_reply_to="c1"),
    ]

    derived = thread_comments(comments)
    by_id = {c.id: c for c in derived}

    assert by_id["c1"].parent_comment_id is None
    assert by_id["c2"].parent_comment_id == "c1"


def test_comments_without_reply_info_are_flat():
    comments = [
        Comment(id="c1", author="A", content_html="<p>1</p>"),
        Comment(id="c2", author="B", content_html="<p>2</p>"),
    ]

    derived = thread_comments(comments)

    assert all(c.parent_comment_id is None for c in derived)


def test_thread_comments_on_empty_list_is_empty_list():
    assert thread_comments([]) == []
