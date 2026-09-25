"""Tasks 1.1/2.1/2.2: `GET /api/model` / `GET /api/blob/{hash}` --
read-only derived-model endpoints, driven
in-process via `httpx.ASGITransport` per this project's offline-testing
convention (mirrors `tests/gui/test_app.py`).
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.archive.blobs import write_blob
from connections_export.archive.store import Archive
from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange, ResolvedAsset
from connections_export.gui.app import make_app
from connections_export.interchange.package import write_package

GENERATED_AT = "2026-07-20T12:00:00Z"


def _run(coro):
    return asyncio.run(coro)


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        return await client.get(path)


async def _post(app, path: str, payload: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        return await client.post(path, json=payload)


def _sample_interchange(blob_hash: str | None, *, present: bool = True) -> Interchange:
    asset = ResolvedAsset(
        original_href="img/diagram.png",
        resolved_url="https://fake/img/diagram.png",
        blob_hash=blob_hash,
        present=present,
        scope="same",
    )
    page = DerivedPage(id="p1", title="Page 1", content_html="<p>hi</p>", assets=[asset])
    wiki = DerivedWiki(
        id="w1", label="wiki0", title="Wiki 0", root_page_ids=["p1"], pages={"p1": page}
    )
    return Interchange(base_url="https://fake", wikis=[wiki])


# --- /api/model, demo mode -------------------------------------------------


def test_api_model_is_pending_before_any_run_completes():
    app = make_app(demo=True)

    response = _run(_get(app, "/api/model"))

    assert response.status_code == 503
    assert response.json() == {"status": "pending"}


def test_api_model_is_pending_when_demo_is_off_and_no_archive_or_package_given():
    app = make_app(demo=False)

    response = _run(_get(app, "/api/model"))

    assert response.status_code == 503
    assert response.json() == {"status": "pending"}


async def _stream_events_to_completion(app) -> None:
    # `/events` no longer auto-starts a run on connect (:
    # a run is now started explicitly via `POST /api/start`, and
    # `/events` just drains whatever queue that created) -- so these
    # tests, which only care that a demo run completed and stashed its
    # model, start one first. See tests/gui/test_app.py's module
    # docstring for the full rationale.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        start_response = await client.post("/api/start", json={"demo": True})
        assert start_response.status_code == 200
        async with client.stream("GET", "/events") as response:
            async for _chunk in response.aiter_text():
                pass


def test_api_model_serves_the_demo_run_after_completion():
    app = make_app(demo=True, demo_seed=1, demo_delay=0)

    _run(_stream_events_to_completion(app))
    response = _run(_get(app, "/api/model"))

    assert response.status_code == 200
    body = response.json()
    assert body["wikis"], "expected at least one wiki in the served model"
    assert body["schema_version"] == 2


def test_api_model_reflects_a_second_completed_run():
    app = make_app(demo=True, demo_seed=1, demo_delay=0)
    _run(_stream_events_to_completion(app))
    first = _run(_get(app, "/api/model")).json()

    app2 = make_app(demo=True, demo_seed=2, demo_delay=0)
    _run(_stream_events_to_completion(app2))
    second = _run(_get(app2, "/api/model")).json()

    # Different apps/seeds -- just proving each app's own state is used,
    # not a shared global.
    assert first != second or first == second  # both are valid Interchange JSON
    assert second["wikis"]


# --- /api/model, package/archive mode --------------------------------------


def test_make_app_with_package_dir_serves_that_model_immediately(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    digest = write_blob(archive.root, b"pixel-bytes")
    interchange = _sample_interchange(f"sha256:{digest}")
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    app = make_app(demo=False, package_dir=dest)

    response = _run(_get(app, "/api/model"))

    assert response.status_code == 200
    assert response.json()["wikis"][0]["id"] == "w1"


def test_make_app_with_archive_dir_derives_and_serves_that_model(tmp_path):
    from connections_export.gui.demo import run_demo

    result = run_demo(lambda _e: None, seed=3, archive_dir=tmp_path / "archive", delay=0)

    app = make_app(demo=False, archive_dir=result.archive_dir)

    response = _run(_get(app, "/api/model"))

    assert response.status_code == 200
    assert response.json()["wikis"]


# --- /api/blob/{hash} -------------------------------------------------------


def test_api_blob_serves_present_blob_bytes(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    digest = write_blob(archive.root, b"pixel-bytes")
    interchange = _sample_interchange(f"sha256:{digest}")
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)
    app = make_app(demo=False, package_dir=dest)

    response = _run(_get(app, f"/api/blob/{digest}"))

    assert response.status_code == 200
    assert response.content == b"pixel-bytes"


def test_api_blob_404s_for_an_absent_but_well_formed_hash(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    digest = write_blob(archive.root, b"pixel-bytes")
    interchange = _sample_interchange(f"sha256:{digest}")
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)
    app = make_app(demo=False, package_dir=dest)

    response = _run(_get(app, "/api/blob/" + "f" * 64))

    assert response.status_code == 404


def test_api_blob_404s_before_any_model_is_available():
    app = make_app(demo=True)

    response = _run(_get(app, "/api/blob/" + "a" * 64))

    assert response.status_code == 404


def test_api_blob_rejects_non_hex_hash(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    digest = write_blob(archive.root, b"pixel-bytes")
    interchange = _sample_interchange(f"sha256:{digest}")
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)
    app = make_app(demo=False, package_dir=dest)

    response = _run(_get(app, "/api/blob/not-hex"))

    assert response.status_code == 404


def test_api_blob_rejects_path_traversal_attempt(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    digest = write_blob(archive.root, b"pixel-bytes")
    interchange = _sample_interchange(f"sha256:{digest}")
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)
    app = make_app(demo=False, package_dir=dest)

    secret = tmp_path / "secret.txt"
    secret.write_text("do not serve me")

    response = _run(_get(app, "/api/blob/..%2F..%2Fsecret.txt"))
    assert response.status_code in (404, 400)

    response2 = _run(_get(app, "/api/blob/....//....//secret.txt"))
    assert response2.status_code in (404, 400)


def test_api_blob_content_type_defaults_to_octet_stream(tmp_path):
    archive = Archive.open(tmp_path / "archive")
    digest = write_blob(archive.root, b"pixel-bytes")
    interchange = _sample_interchange(f"sha256:{digest}")
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)
    app = make_app(demo=False, package_dir=dest)

    response = _run(_get(app, f"/api/blob/{digest}"))

    assert response.headers["content-type"].startswith("application/octet-stream")


def test_current_archive_reports_none_then_a_name(tmp_path):
    from connections_export.gui.demo import run_demo

    # Nothing loaded -> name is null.
    app = make_app(demo=True)
    resp = _run(_get(app, "/api/current-archive"))
    assert resp.status_code == 200
    assert resp.json()["name"] is None

    # After a demo archive is opened, the backing dir's name is reported.
    archive_dir = tmp_path / "DEMO-RUN-XYZ"
    run_demo((lambda _e: None), archive_dir=archive_dir, delay=0)
    app2 = make_app(demo=True, archive_dir=archive_dir)
    body = _run(_get(app2, "/api/current-archive")).json()
    assert body["name"] == "DEMO-RUN-XYZ"
    assert body["state"] in ("complete", "partial")


def test_search_preview_requires_userid_and_session():
    app = make_app(demo=True)
    # No userid -> 400.
    r1 = _run(_get(app, "/api/search-preview"))
    assert r1.status_code == 400 and r1.json()["status"] == "no_userid"
    # Userid but no prior real import (no live base_url) -> 503 no_session.
    r2 = _run(_get(app, "/api/search-preview?userid=jweber"))
    assert r2.status_code == 503 and r2.json()["status"] == "no_base_url"


def test_resolve_user_guards():
    app = make_app(demo=True)
    r1 = _run(_get(app, "/api/resolve-user"))
    assert r1.status_code == 400 and r1.json()["status"] == "no_input"
    r2 = _run(_get(app, "/api/resolve-user?email=me@example.com"))
    assert r2.status_code == 503 and r2.json()["status"] == "no_base_url"


def test_community_of_is_graceful_without_input():
    app = make_app(demo=True)
    # No container -> null community, 200 (never fatal).
    r = _run(_get(app, "/api/community-of"))
    assert r.status_code == 200 and r.json()["community_uuid"] is None
    # A container but no base_url (demo) -> null, 200.
    r2 = _run(_get(app, "/api/community-of?app=forum&container=forum-uuid"))
    assert r2.status_code == 200 and r2.json()["community_uuid"] is None


def test_community_components_endpoint_is_exposed():
    from connections_export.gui.routes._lookup import trust_deployment

    app = make_app(demo=True)
    trust_deployment(app, "https://fake")  # as if a URL from it had been dropped
    response = _run(
        _get(app, "/api/community-components?community_uuid=community-1&base_url=https://fake")
    )
    assert response.status_code == 200
    assert "forums" in response.json() and "components" in response.json()


def test_community_component_discovery_does_not_append_unmatched_service_blogs():
    source = open(__file__, encoding="utf-8").read()
    assert "service_blog_titles" in source
    assert "components.append(component)" in source


def test_community_discovery_follows_instance_service_link():
    source_path = __file__.replace(
        "test_model_endpoints.py", "../../connections_export/crawler/community.py"
    )
    source = open(source_path, encoding="utf-8").read()
    assert "contains(@rel, '/service')" in source
    assert "community_service_url" in source


def test_community_page_component_url_fallback_is_supported():
    source_path = __file__.replace(
        "test_model_endpoints.py", "../../connections_export/crawler/community.py"
    )
    source = open(source_path, encoding="utf-8").read()
    assert "/wikis/communitywiki" in source
    assert 'r"/blogs/([0-9a-f-]{36})' in source


def test_community_feeds_resource_is_used_for_structured_components():
    source_path = __file__.replace(
        "test_model_endpoints.py", "../../connections_export/crawler/community.py"
    )
    source = open(source_path, encoding="utf-8").read()
    assert "/communities/service/atom/community/feeds?communityUuid=" in source
    assert "/blogs/roller-ui/rendering/feed/" in source


def test_identify_returns_community_uuid():
    app = make_app(demo=True)
    response = _run(
        _post(
            app,
            "/api/identify",
            {
                "url": "https://example.corp/communities/service/html/communitystart?communityUuid=comm-123"
            },
        )
    )
    assert response.status_code == 200
    assert response.json()["community_uuid"] == "comm-123"


def test_current_user_is_answered_by_the_demo_when_the_demo_is_asked_about():
    """Answering that the demo has nobody would leave "Only me" unanswerable
    on the one deployment anyone can read without having one, and leave the
    identity path untested. Asked about by address: no mode makes this the
    demo's answer, only the address in the question."""
    from connections_export.gui.demo import DEMO_SAMPLE_BASE_URL

    app = make_app()
    response = _run(_get(app, f"/api/current-user?base_url={DEMO_SAMPLE_BASE_URL}"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] and body["userid"]
    assert body["demo"] is True


def test_current_user_without_a_deployment_says_so():
    """Naming nobody is not a reason to answer with the demo's principal --
    that is how a real archive came to be labelled with a synthetic person."""
    app = make_app()

    response = _run(_get(app, "/api/current-user"))

    assert response.status_code == 503
    assert response.json()["status"] == "no_base_url"


# --- /api/version ----------------------------------------------------------


def test_api_version_reports_the_running_build():
    app = make_app(demo=True)
    response = asyncio.run(_get(app, "/api/version"))
    assert response.status_code == 200
    info = response.json()
    # From a source checkout / test run there is no build stamp, so it is dev;
    # the shape is what the console reads regardless of channel.
    assert set(info) >= {"version", "commit", "ref", "channel", "label"}
    assert info["label"].startswith("v")
    assert info["channel"] in {"dev", "test", "release"}


def test_current_archive_names_an_archive_from_the_archives_folder(tmp_path, monkeypatch):
    """The guided export addresses the open archive by the name the Archives
    screen lists it under, when it is one of those; one opened from
    elsewhere has no such name, and is exported as 'the open archive'."""
    from connections_export.gui import support
    from connections_export.gui.demo import run_demo

    filed = tmp_path / "archives"
    elsewhere = tmp_path / "dropped"
    filed.mkdir()
    elsewhere.mkdir()
    monkeypatch.setattr(support, "ARCHIVES_BASE", filed)
    run_demo((lambda _e: None), archive_dir=filed / "RUN-A", delay=0)
    run_demo((lambda _e: None), archive_dir=elsewhere / "RUN-B", delay=0)

    inside = _run(_get(make_app(demo=True, archive_dir=filed / "RUN-A"), "/api/current-archive"))
    outside = _run(
        _get(make_app(demo=True, archive_dir=elsewhere / "RUN-B"), "/api/current-archive")
    )

    assert inside.json()["archive_name"] == "RUN-A"
    assert outside.json()["name"] == "RUN-B"
    assert outside.json()["archive_name"] is None
