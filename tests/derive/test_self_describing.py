"""The multi-host self-describing round
trip. Proves provenance flows
crawl -> archive -> derive without any out-of-band config:

    crawl(config.hcl_hosts=["files.example.corp"]) -> archive
    -> derive(archive)  # NO hcl_hosts kwarg
    -> a body asset/link on the second host classifies as
       same-deployment / hcl_deployment, not external.

Placeholder hosts only (`fake`, `files.example.corp`) so
`tests/test_hygiene.py` stays green.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import crawl
from connections_export.derive.assemble import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.model import FakePage, FakeWiki, WikiSet
from connections_export.http.client import HttpClient, RetryPolicy
from tests.crawler.conftest import OverrideTransport, SyncASGIBridge, make_client, static_response

BASE_URL = "https://fake"
SECOND_HOST = "files.example.corp"


def _config(tmp_path: Path, **overrides) -> Config:
    return Config(
        base_url=BASE_URL,
        output_dir=tmp_path / "archive",
        hcl_hosts=[SECOND_HOST],
        **overrides,
    )


def _wikiset_with_body(body_html: str) -> WikiSet:
    page = FakePage(
        uuid="page-uuid-1",
        label="p0",
        title="Page Zero",
        parent_uuid=None,
        ordinal=0,
        body_html=body_html,
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        version_label=1,
    )
    wiki = FakeWiki(
        uuid="wiki-uuid-1",
        label="wiki0",
        title="Wiki Zero",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
        pages=[page],
    )
    return WikiSet(wikis=[wiki])


def test_cross_app_link_recovered_as_hcl_deployment_from_archive_alone(tmp_path):
    """A body `<a href>` pointing at the deployment's second host
    (e.g. Files, on a separate subdomain) classifies `hcl_deployment`
    -- not `external` -- when derived from the archive alone, because
    the crawler recorded `hcl_hosts` as run provenance and `derive`
    recovers it from there."""
    files_link = f"https://{SECOND_HOST}/files/basic/api/library/lib1/document/doc1"
    body = f'<div><a href="{files_link}">a files doc</a></div>'
    wikiset = _wikiset_with_body(body)
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    result = crawl(config=_config(tmp_path), client=client, archive=archive)
    assert result.ok is True

    # The crucial call: no hcl_hosts kwarg. Everything derive needs to
    # classify the cross-app link comes from the archive itself.
    interchange = derive(archive)

    assert interchange.hcl_hosts == [SECOND_HOST]
    page = interchange.wikis[0].pages["page-uuid-1"]
    matching_links = [link for link in page.links if link.resolved_url == files_link]
    assert len(matching_links) == 1
    assert matching_links[0].scope == "hcl_deployment"


def test_cross_app_asset_fetched_by_crawler_and_resolved_present_from_archive_alone(tmp_path):
    """The stronger form of the same proof, through an asset: the
    crawler itself only fetched the cross-host image because
    `config.hcl_hosts` said it was same-deployment; `derive`, reading
    only the archive afterwards, reaches the identical same-deployment
    conclusion and finds the blob it fetched -- present=True, not a
    completeness defect."""
    asset_url = f"https://{SECOND_HOST}/files/basic/api/library/lib1/document/doc1/media"
    body = f'<div><img src="{asset_url}"/></div>'
    wikiset = _wikiset_with_body(body)
    app = make_app(wikiset)
    transport = OverrideTransport(
        SyncASGIBridge(app),
        overrides=[
            (
                lambda request: request.url.host == SECOND_HOST,
                static_response(200, b"file bytes", content_type="application/octet-stream"),
            ),
        ],
    )
    client = HttpClient(
        transport=transport, retry_policy=RetryPolicy(max_attempts=1), sleep=lambda _s: None
    )
    archive = Archive.open(tmp_path / "archive")

    result = crawl(config=_config(tmp_path), client=client, archive=archive)
    assert result.ok is True
    assert asset_url in archive.seen_urls()

    interchange = derive(archive)  # no hcl_hosts kwarg

    page = interchange.wikis[0].pages["page-uuid-1"]
    matching_assets = [asset for asset in page.assets if asset.resolved_url == asset_url]
    assert len(matching_assets) == 1
    assert matching_assets[0].scope == "same"
    assert matching_assets[0].present is True


def test_without_hcl_hosts_configured_the_same_body_would_misclassify_as_external(tmp_path):
    """Control: with no `hcl_hosts` on the crawl (today's baseline),
    the same cross-app link classifies as `external` -- proving the
    fix above is real, not a tautology of how `derive` always
    classifies that host."""
    files_link = f"https://{SECOND_HOST}/files/basic/api/library/lib1/document/doc1"
    body = f'<div><a href="{files_link}">a files doc</a></div>'
    wikiset = _wikiset_with_body(body)
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")

    config = Config(base_url=BASE_URL, output_dir=tmp_path / "archive")  # no hcl_hosts
    result = crawl(config=config, client=client, archive=archive)
    assert result.ok is True

    interchange = derive(archive)

    assert interchange.hcl_hosts == []
    page = interchange.wikis[0].pages["page-uuid-1"]
    matching_links = [link for link in page.links if link.resolved_url == files_link]
    assert len(matching_links) == 1
    assert matching_links[0].scope == "external"
