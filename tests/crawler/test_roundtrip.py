"""The round-trip proof of losslessness.

synthesize -> serve -> crawl -> RE-READ the archive's stored blobs and
PARSE them with the adapters (independently of the crawler's own
internals) -> assert structural identity with the source `WikiSet`.
Then the same proof under injected faults: nothing is silently lost --
every fault is an explicit record or warning, never a missing resource.
"""

from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from connections_export.adapters.wikis import (
    artifacts_url,
    nav_feed_url,
    page_entry_url,
    parse_attachments_feed,
    parse_comments_feed,
    parse_nav_feed,
    parse_page_entry,
    parse_versions_feed,
    parse_wikis_feed,
    wikis_feed_url,
)
from connections_export.archive.blobs import read_blob
from connections_export.archive.records import ManifestRecord, Outcome
from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler import assets, events
from connections_export.crawler.crawl import crawl
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.faults import Faults
from connections_export.fakeserver.model import FakeWiki
from connections_export.fakeserver.synth import SynthSeed, synthesize
from connections_export.http.client import HttpClient, RetryPolicy
from tests.archive.conftest import FakeResponse
from tests.crawler.conftest import (
    OverrideTransport,
    SyncASGIBridge,
    fail_n_times_then,
    make_client,
    path_is,
)

BASE_URL = "https://fake"
AUTH_ROOT = "basic"


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(base_url=BASE_URL, output_dir=tmp_path / "archive", **overrides)


# --- independent archive reconstruction -----------------------------
#
# Deliberately does not call into connections_export.crawler.crawl at all: it
# re-reads manifest.jsonl and blobs/ from disk and re-parses them with
# the adapters, exactly as a human auditing the archive after the fact
# would. If this reconstruction matches the source WikiSet, the crawl
# lost nothing.


def _read_manifest(archive: Archive) -> list[ManifestRecord]:
    path = archive.root / "manifest.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(ManifestRecord.model_validate_json(line))
    return records


def _latest_ok_by_url(archive: Archive) -> dict[str, ManifestRecord]:
    by_url: dict[str, ManifestRecord] = {}
    for record in _read_manifest(archive):
        if record.outcome == Outcome.ok:
            by_url[record.url] = record
    return by_url


def _body(archive: Archive, record: ManifestRecord) -> bytes:
    digest = record.body_hash.split(":", 1)[-1]
    return read_blob(archive.root, digest)


def _paginated_url(base_url: str, *, ps: int, page: int) -> str:
    """The same `?ps=N&page=k` URL shape the crawler's own page-walker
    builds (the crawler sends `?ps={config.page_size}&page=
    {n}`), preserving whatever query params `base_url` already
    carries (e.g. `?category=version`). Built independently here with
    plain stdlib `urllib.parse` -- not by importing the crawler's own
    `_add_query` -- so this reconstruction stays a check against the
    *documented* URL convention, not against the crawler's internals.
    """
    split = urlsplit(base_url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query["ps"] = str(ps)
    query["page"] = str(page)
    query["pageSize"] = str(ps)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _read_all_pages(
    by_url: dict[str, ManifestRecord], archive: Archive, base_url: str, *, page_size: int, parser
) -> list:
    """Walk `base_url` page by page exactly as the crawler's page-walker
    does -- reconstruction has to recover every item, not just the
    first page -- stopping at the first page missing from
    the archive or shorter than `page_size`. Returns `[]` if not even
    page 1 was archived (fetch failed, or the resource was never
    fetched)."""
    items: list = []
    page = 1
    while True:
        url = _paginated_url(base_url, ps=page_size, page=page)
        record = by_url.get(url)
        if record is None:
            break
        page_items = parser(_body(archive, record))
        items.extend(page_items)
        if len(page_items) < page_size:
            break
        page += 1
    return items


def _expected_positions(wiki: FakeWiki) -> dict[str, tuple[str | None, int]]:
    """Independently compute each page's expected (parent, sibling
    rank) straight from the source model -- the same
    `children_of`/sort-by-ordinal the fake's own nav-feed serializer
    uses, so this is a faithful "what should the nav feed say" oracle."""
    expected: dict[str, tuple[str | None, int]] = {}
    parents = {p.parent_uuid for p in wiki.pages}
    for parent in parents:
        for idx, sibling in enumerate(wiki.children_of(parent)):
            expected[sibling.uuid] = (parent, idx)
    return expected


def _reconstruct(archive: Archive, *, page_size: int = 500) -> dict[str, dict]:
    """Reconstruct {wiki_label: {page_uuid: {...}}} entirely from the
    archive's stored bytes, parsed with the real adapters.

    `page_size` must match whatever `Config.page_size` the crawl was
    run with (default 500, same as `Config`'s own default) -- the
    crawler's page-walker sends `?ps={page_size}&page={n}` on every
    feed request, even a first page that turns out to be the only one,
    so every feed lookup below walks pages the same way. That is what
    proves reconstruction recovers *every* page, not just the first.
    """
    by_url = _latest_ok_by_url(archive)

    wikis_url = wikis_feed_url(base_url=BASE_URL, auth_root=AUTH_ROOT)
    wikirefs = _read_all_pages(
        by_url, archive, wikis_url, page_size=page_size, parser=parse_wikis_feed
    )

    reconstructed: dict[str, dict] = {}
    for wikiref in wikirefs:
        nav_url = nav_feed_url(base_url=BASE_URL, auth_root=AUTH_ROOT, wiki_label=wikiref.label)
        nav_url = f"{nav_url}?tree=true&acls=true"
        nav_record = by_url.get(nav_url)
        if nav_record is None:
            continue
        nav_tree = parse_nav_feed(_body(archive, nav_record))

        pages: dict[str, dict] = {}
        for node in nav_tree.nodes:
            entry_url = page_entry_url(
                base_url=BASE_URL,
                auth_root=AUTH_ROOT,
                wiki_label=wikiref.label,
                page_label=node.label,
            )
            entry_record = by_url.get(entry_url)
            if entry_record is None:
                continue  # a page that never successfully archived an entry
            try:
                page = parse_page_entry(_body(archive, entry_record))
            except Exception:
                continue  # malformed -- not part of the reconstructed tree

            comments_url = page.replies_url or artifacts_url(
                base_url=BASE_URL,
                auth_root=AUTH_ROOT,
                wiki_label=wikiref.label,
                page_label=node.label,
                category="comment",
            )
            comments = _read_all_pages(
                by_url, archive, comments_url, page_size=page_size, parser=parse_comments_feed
            )

            versions_url = artifacts_url(
                base_url=BASE_URL,
                auth_root=AUTH_ROOT,
                wiki_label=wikiref.label,
                page_label=node.label,
                category="version",
            )
            versions = _read_all_pages(
                by_url, archive, versions_url, page_size=page_size, parser=parse_versions_feed
            )

            attachments_url = artifacts_url(
                base_url=BASE_URL,
                auth_root=AUTH_ROOT,
                wiki_label=wikiref.label,
                page_label=node.label,
                category="attachment",
            )
            attachments = _read_all_pages(
                by_url,
                archive,
                attachments_url,
                page_size=page_size,
                parser=parse_attachments_feed,
            )

            asset_blobs_present = []
            if page.content_src and page.content_src in by_url:
                body_bytes = _body(archive, by_url[page.content_src])
                for href in assets.scan(body_bytes):
                    resolved = assets.resolve(href, base_url=BASE_URL)
                    if assets.classify(resolved, base_url=BASE_URL) == "same":
                        asset_blobs_present.append(resolved in by_url)

            pages[page.uuid] = {
                "label": page.label,
                "title": page.title,
                "parent": node.parent or None,
                "ordinal": node.ordinal,
                "comment_count": len(comments),
                "version_count": len(versions),
                "attachment_count": len(attachments),
                "replies_count": page.replies_count,
                "asset_blobs_present": asset_blobs_present,
            }
        reconstructed[wikiref.label] = pages
    return reconstructed


def _assert_matches_source(reconstructed: dict[str, dict], wikiset) -> None:
    for wiki in wikiset.wikis:
        assert wiki.label in reconstructed, f"wiki {wiki.label} missing from reconstruction"
        pages = reconstructed[wiki.label]
        expected_positions = _expected_positions(wiki)

        for page in wiki.pages:
            assert page.uuid in pages, (
                f"page {page.uuid} ({page.label}) missing from reconstruction"
            )
            got = pages[page.uuid]
            assert got["label"] == page.label
            assert got["title"] == page.title

            expected_parent, expected_ordinal = expected_positions[page.uuid]
            assert got["parent"] == expected_parent
            assert got["ordinal"] == expected_ordinal

            assert got["comment_count"] == len(page.comments)
            assert got["replies_count"] == len(page.comments)
            assert got["version_count"] == len(page.versions)
            assert got["attachment_count"] == len(page.attachments)

            # Every same-deployment body asset actually has a blob.
            assert all(got["asset_blobs_present"]), (
                f"page {page.label} references a same-deployment asset with no archived blob"
            )


# --- 9.1 / 9.2 ---------------------------------------------------------


def test_roundtrip_structural_identity(tmp_path):
    wikiset = synthesize(
        SynthSeed(
            seed=100,
            wiki_count=1,
            depth=2,
            pages_per_level=2,
            comments_per_page=3,
            versions_per_page=2,
            attachments_per_page=2,
            tags_per_page=1,
        )
    )
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(config=_config(tmp_path), client=client, archive=archive)
    assert result.ok is True

    reconstructed = _reconstruct(archive)
    _assert_matches_source(reconstructed, wikiset)


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
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(config=_config(tmp_path), client=client, archive=archive)
    assert result.ok is True

    reconstructed = _reconstruct(archive)
    _assert_matches_source(reconstructed, wikiset)


# --- 5.1 / 5.2: round-trip proof extension -- multi-page feeds ---------
#
# A page with more comments than a small page_size, served paginated by
# the fake -- crawl, then reconstruct from the archive exactly as 9.1/
# 9.2 do above (re-read manifest + blobs, re-parse with the adapters,
# independently of the crawler's own internals). If every comment comes
# back and the manifest shows the page=1,2,... walk, pagination lost
# nothing.
#
# 25 (not the illustrative "30") comments against page_size=10 is
# deliberate: 25 = 10 + 10 + 5, a genuinely short trailing page, so the
# walk is an unambiguous 3 fetches. 30/10 is *also* an exact multiple
# of page_size (see the dedicated exact-multiple test below), and the
# robust OR-based stop condition (the design: continue while *either* a
# next link is present *or* the page came back full) means an exact
# multiple always costs one extra, confirming-empty page fetch -- 30
# would walk pages 1,2,3,4, not 1,2,3.


def test_roundtrip_multi_page_comments_recovers_every_item(tmp_path):
    wikiset = synthesize(
        SynthSeed(
            seed=110,
            wiki_count=1,
            depth=1,
            pages_per_level=1,
            comments_per_page=25,
            versions_per_page=1,
        )
    )
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    app = make_app(wikiset, default_page_size=10)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(config=_config(tmp_path, page_size=10), client=client, archive=archive)
    assert result.ok is True

    reconstructed = _reconstruct(archive, page_size=10)
    _assert_matches_source(reconstructed, wikiset)
    assert reconstructed[wiki.label][page.uuid]["comment_count"] == 25

    # The manifest itself shows the page-by-page walk, independent of
    # the reconstruction helper above.
    comments_fragment = f"/wiki/{wiki.label}/page/{page.label}/feed"
    comment_page_urls = [
        r.url
        for r in _read_manifest(archive)
        if comments_fragment in r.url and "category=" not in r.url and r.outcome == Outcome.ok
    ]
    assert any("ps=10" in u and "page=1" in u for u in comment_page_urls)
    assert any("ps=10" in u and "page=2" in u for u in comment_page_urls)
    assert any("ps=10" in u and "page=3" in u for u in comment_page_urls)
    assert not any("page=4" in u for u in comment_page_urls)


def test_roundtrip_exact_multiple_page_count_recovers_every_item(tmp_path):
    """The nasty case the design calls out explicitly: an item count
    that is an exact multiple of page_size, so the last full page is
    followed by an empty page -- terminates correctly and still
    recovers everything."""
    wikiset = synthesize(
        SynthSeed(seed=111, wiki_count=1, depth=1, pages_per_level=1, comments_per_page=20)
    )
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    app = make_app(wikiset, default_page_size=10)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(config=_config(tmp_path, page_size=10), client=client, archive=archive)
    assert result.ok is True

    reconstructed = _reconstruct(archive, page_size=10)
    _assert_matches_source(reconstructed, wikiset)
    assert reconstructed[wiki.label][page.uuid]["comment_count"] == 20


def test_roundtrip_nasty_case_seed_survives_pagination(tmp_path):
    """5.2: the existing nasty-case seed (deep nesting, unicode, empty
    pages, duplicate ordinals, and a 60-comment page) still round-trips
    completely when comments are forced to paginate."""
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
    app = make_app(wikiset, default_page_size=10)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(config=_config(tmp_path, page_size=10), client=client, archive=archive)
    assert result.ok is True

    reconstructed = _reconstruct(archive, page_size=10)
    _assert_matches_source(reconstructed, wikiset)


# --- 9.3 faults injected ---------------------------------------------


def test_roundtrip_under_injected_faults_nothing_is_silently_lost(tmp_path):
    wikiset = synthesize(
        SynthSeed(seed=102, wiki_count=1, depth=1, pages_per_level=4, comments_per_page=1)
    )
    wiki = wikiset.wikis[0]
    ok_page, retry_page, fail_page, malformed_page = wiki.top_level_pages()

    fail_entry_path = f"/wikis/basic/api/wiki/{wiki.label}/page/{fail_page.label}/entry"
    malformed_entry_path = f"/wikis/basic/api/wiki/{wiki.label}/page/{malformed_page.label}/entry"
    retry_entry_path = f"/wikis/basic/api/wiki/{wiki.label}/page/{retry_page.label}/entry"

    faulty_app = make_app(
        wikiset,
        faults=Faults(status_for={fail_entry_path: 500}, malformed_paths={malformed_entry_path}),
    )
    inner = SyncASGIBridge(faulty_app)
    transport = OverrideTransport(
        inner, overrides=[(path_is(retry_entry_path), fail_n_times_then(1, inner.handle_request))]
    )
    client = HttpClient(
        transport=transport, retry_policy=RetryPolicy(max_attempts=3), sleep=lambda _s: None
    )
    archive = Archive.open(tmp_path / "archive")

    # A truncation-signature feed (the design: item_count == page_size,
    # no next link) -- pre-seeded, since this crawler never paginates
    # and so cannot organically produce the signature against this
    # fixture; archive.report flags it regardless of who wrote it.
    truncated_url = "https://fake/wikis/basic/api/wikis/feed?ps=1&page=9"
    archive.write_response(
        FakeResponse(url=truncated_url, method="GET", status=200, content=b"<feed/>"),
        fetched_at="2026-01-01T00:00:00Z",
        item_count=1,
        page_size=1,
        has_next=False,
    )

    seen_events = []
    result = crawl(
        config=_config(tmp_path), client=client, archive=archive, emit=seen_events.append
    )

    assert result.ok is False

    # 1. Retried-then-successful content is archived intact and the
    # retry was observed.
    retried = [e for e in seen_events if isinstance(e, events.Retried)]
    assert any(retry_entry_path in e.url for e in retried)
    assert any(retry_entry_path in url for url in archive.seen_urls())

    # 2. The hard failure is an explicit record, never a gap.
    failed_events = [e for e in seen_events if isinstance(e, events.Failed)]
    assert any(fail_entry_path in e.url and e.kind == "http_error" for e in failed_events)
    all_urls_ever = {r.url for r in _read_manifest(archive)}
    assert any(fail_entry_path in url for url in all_urls_ever)
    assert not any(fail_entry_path in url for url in archive.seen_urls())  # never counted ok

    # 3. The malformed page: bytes are archived, failure is explicit.
    assert any(malformed_entry_path in e.url and e.kind == "unparsed" for e in failed_events)
    assert any(malformed_entry_path in url for url in archive.seen_urls())

    # 4. Reconstruction: the healthy pages round-trip completely; the
    # fail/malformed pages are correctly absent from the
    # *reconstructed* tree (they never produced a parseable entry)
    # but were never silently dropped -- they show up as explicit
    # failed events/records above.
    reconstructed = _reconstruct(archive)
    pages = reconstructed[wiki.label]
    assert ok_page.uuid in pages
    assert pages[ok_page.uuid]["comment_count"] == len(ok_page.comments)
    assert retry_page.uuid in pages
    assert fail_page.uuid not in pages
    assert malformed_page.uuid not in pages

    # 5. The truncation signature surfaced as an explicit warning too.
    truncation_warnings = [
        e for e in seen_events if isinstance(e, events.Warning) and e.kind == "truncation"
    ]
    assert any(w.ref == truncated_url for w in truncation_warnings)
    assert truncated_url in result.report.possibly_truncated
