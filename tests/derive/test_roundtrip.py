"""The derive-level round-trip proof of losslessness.

synthesize(seed) -> crawl (real client + fake app via the ASGI bridge)
-> derive(archive) -> assert the assembled model matches the source
`WikiSet`: page identities/labels/titles, parent + sibling ordinal,
comment/version/attachment counts, and every same-deployment body
asset resolved present=True. Run under both a plain seed and the
nasty-case seed (deep nesting, unicode/RTL, empty page, duplicate
ordinals, high comment count) -- the design, "Round-trip proof".

Derives strictly from the archive on disk: `derive(archive)` never
sees `wikiset` -- everything below only reads it to build the
independent oracle used to assert against.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import crawl
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import FakeWiki
from connections_export.fakeserver.synth import SynthSeed, synthesize
from tests.crawler.conftest import make_client

BASE_URL = "https://fake"


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url=BASE_URL, output_dir=tmp_path / "archive", **overrides)


def _expected_positions(wiki: FakeWiki) -> dict[str, tuple[str | None, int]]:
    """Each page's expected (parent, sibling rank), straight from the
    source model's own `children_of` -- the same oracle
    `tests/crawler/test_roundtrip.py` uses."""
    expected: dict[str, tuple[str | None, int]] = {}
    parents = {p.parent_uuid for p in wiki.pages}
    for parent in parents:
        for idx, sibling in enumerate(wiki.children_of(parent)):
            expected[sibling.uuid] = (parent, idx)
    return expected


def _assert_matches_source(interchange, wikiset) -> None:
    derived_by_label = {w.label: w for w in interchange.wikis}

    for wiki in wikiset.wikis:
        assert wiki.label in derived_by_label, f"wiki {wiki.label} missing from derived model"
        derived_wiki = derived_by_label[wiki.label]
        expected_positions = _expected_positions(wiki)

        for page in wiki.pages:
            assert page.uuid in derived_wiki.pages, (
                f"page {page.uuid} ({page.label}) missing from the derived model"
            )
            derived_page = derived_wiki.pages[page.uuid]

            assert derived_page.label == page.label
            assert derived_page.title == page.title

            expected_parent, expected_ordinal = expected_positions[page.uuid]
            assert derived_page.parent_id == expected_parent
            assert derived_page.ordinal == expected_ordinal

            assert len(derived_page.comments) == len(page.comments)
            assert len(derived_page.versions) == len(page.versions)
            assert len(derived_page.attachments) == len(page.attachments)

            # Every same-deployment body asset resolved to a blob.
            same_deployment_assets = [a for a in derived_page.assets if a.scope == "same"]
            assert all(a.present for a in same_deployment_assets), (
                f"page {page.label} has a same-deployment asset with no archived blob: "
                f"{[a for a in same_deployment_assets if not a.present]}"
            )

            # Nothing was silently dropped: no failure note for a page
            # that (per the source model) crawled and derived cleanly.
            assert derived_page.provenance.note is None

            # HCL identifiers never leak into the primary shape -- only
            # into provenance.
            assert derived_page.provenance.hcl_id == page.uuid


def _derive_from_fresh_crawl(tmp_path: Path, wikiset, *, page_size: int | None = None) -> object:
    app = make_app(wikiset, default_page_size=page_size)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    overrides = {} if page_size is None else {"page_size": page_size}
    result = crawl(config=_config(tmp_path, **overrides), client=client, archive=archive)
    assert result.ok is True

    # `derive` reads only the archive on disk -- `wikiset`/`app`/
    # `client` are never passed to it, only used afterwards to build
    # the assertion oracle.
    return derive(archive)


# --- 7.1: plain seed -------------------------------------------------------


def test_roundtrip_structural_identity(tmp_path):
    wikiset = synthesize(
        SynthSeed(
            seed=200,
            wiki_count=1,
            depth=2,
            pages_per_level=2,
            comments_per_page=3,
            versions_per_page=2,
            attachments_per_page=2,
            tags_per_page=1,
        )
    )

    interchange = _derive_from_fresh_crawl(tmp_path, wikiset)

    _assert_matches_source(interchange, wikiset)


def test_wiki_tags_are_captured_and_derived(tmp_path):
    """Tags flow end to end: the fake serves a `?category=tag` feed, the crawler
    archives it, and derive attaches the terms to `DerivedPage.tags`."""
    wikiset = synthesize(
        SynthSeed(seed=205, wiki_count=1, depth=1, pages_per_level=2, tags_per_page=3)
    )
    interchange = _derive_from_fresh_crawl(tmp_path, wikiset)

    src_wiki = wikiset.wikis[0]
    derived_wiki = next(w for w in interchange.wikis if w.label == src_wiki.label)
    for page in src_wiki.pages:
        assert page.tags, "precondition: the synth page should carry tags"
        assert derived_wiki.pages[page.uuid].tags == page.tags


def test_roundtrip_with_multiple_wikis(tmp_path):
    wikiset = synthesize(
        SynthSeed(seed=201, wiki_count=3, depth=2, pages_per_level=2, comments_per_page=1)
    )

    interchange = _derive_from_fresh_crawl(tmp_path, wikiset)

    _assert_matches_source(interchange, wikiset)
    assert len(interchange.wikis) == 3


# --- 7.2: nasty-case seed ----------------------------------------------


def test_roundtrip_nasty_case_seed_survives_intact(tmp_path):
    wikiset = synthesize(
        SynthSeed(
            seed=101,
            wiki_count=1,
            depth=2,
            pages_per_level=2,
            comments_per_page=1,
            versions_per_page=1,
            attachments_per_page=1,
            tags_per_page=1,
            deep_nesting=True,
            deep_nesting_depth=8,
            high_comment_count=True,
            high_comment_count_n=60,
            unicode_rtl=True,
            empty_pages=True,
            duplicate_ordinals=True,
        )
    )

    interchange = _derive_from_fresh_crawl(tmp_path, wikiset)

    _assert_matches_source(interchange, wikiset)

    # The unicode/RTL title and the empty page survive byte-for-byte.
    wiki = wikiset.wikis[0]
    unicode_page = next(p for p in wiki.pages if "unicode" in p.label)
    empty_page = next(p for p in wiki.pages if p.body_html == "")
    derived_wiki = interchange.wikis[0]
    assert derived_wiki.pages[unicode_page.uuid].title == unicode_page.title
    assert derived_wiki.pages[empty_page.uuid].content_html == ""
    assert (
        derived_wiki.pages[empty_page.uuid].provenance.note is None
    )  # legitimately empty, not a gap

    # The high-comment-count page's comments all made it across.
    high_comment_page = next(p for p in wiki.pages if len(p.comments) == 60)
    assert len(derived_wiki.pages[high_comment_page.uuid].comments) == 60

    # The deep-nesting chain's parent links reproduce end to end.
    deep_pages = [p for p in wiki.pages if p.label.startswith("deep")]
    assert len(deep_pages) == 8
    for page in deep_pages:
        derived_page = derived_wiki.pages[page.uuid]
        assert derived_page.parent_id == page.parent_uuid


# --- 7.2 extension: nasty-case seed under forced pagination ---------------


def test_roundtrip_nasty_case_seed_survives_pagination(tmp_path):
    """Pagination-awareness: the archive holds
    comments (and everything else) across multiple `?ps=&page=` pages,
    and `derive` -- via `ArchiveIndex.paginate` -- must walk every one
    of them, not just page 1, or the high-comment-count page would
    silently under-count."""
    wikiset = synthesize(
        SynthSeed(
            seed=101,
            wiki_count=1,
            depth=2,
            pages_per_level=2,
            comments_per_page=1,
            versions_per_page=1,
            attachments_per_page=1,
            tags_per_page=1,
            deep_nesting=True,
            deep_nesting_depth=8,
            high_comment_count=True,
            high_comment_count_n=60,
            unicode_rtl=True,
            empty_pages=True,
            duplicate_ordinals=True,
        )
    )

    interchange = _derive_from_fresh_crawl(tmp_path, wikiset, page_size=10)

    _assert_matches_source(interchange, wikiset)

    wiki = wikiset.wikis[0]
    high_comment_page = next(p for p in wiki.pages if len(p.comments) == 60)
    derived_page = interchange.wikis[0].pages[high_comment_page.uuid]
    assert len(derived_page.comments) == 60  # 6 pages of 10, all recovered


def test_roundtrip_exact_multiple_page_count_recovers_every_comment(tmp_path):
    """The nasty case the design calls out explicitly: a comment count
    that's an exact multiple of page_size, so the last full page is
    followed by an empty one -- pagination still terminates correctly
    and recovers everything."""
    wikiset = synthesize(
        SynthSeed(seed=111, wiki_count=1, depth=1, pages_per_level=1, comments_per_page=20)
    )
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]

    interchange = _derive_from_fresh_crawl(tmp_path, wikiset, page_size=10)

    _assert_matches_source(interchange, wikiset)
    assert len(interchange.wikis[0].pages[page.uuid].comments) == 20
