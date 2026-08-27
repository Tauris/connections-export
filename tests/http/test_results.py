"""Tests for connections_export.http.results: Fetched / FetchFailure / FailureKind.

`Fetched` must duck-type what `archive.Archive.write_response` consumes
(url/method/status/headers/content) without `connections_export.http` importing
anything from `connections_export.archive` — that import only happens here, in
the test, to verify compatibility by attribute, not by inheritance.
"""

import tempfile
from pathlib import Path

from connections_export.http.results import FailureKind, Fetched, FetchFailure

# --- 2.1 Fetched duck-types archive.write_response's `response` arg ---


def test_fetched_has_the_attributes_write_response_needs():
    fetched = Fetched(
        url="https://host/wikis/basic/api/wikis/feed",
        method="GET",
        status=200,
        headers={"Content-Type": "application/atom+xml"},
        content=b"<feed>hello</feed>",
    )

    assert fetched.url == "https://host/wikis/basic/api/wikis/feed"
    assert fetched.method == "GET"
    assert fetched.status == 200
    assert fetched.headers == {"Content-Type": "application/atom+xml"}
    assert fetched.content == b"<feed>hello</feed>"


def test_fetched_is_accepted_by_archive_write_response():
    # Deliberately imported only inside the test: http must not depend on
    # archive, but a Fetched must be usable wherever archive expects its
    # minimal response shape.
    from connections_export.archive.store import Archive

    fetched = Fetched(
        url="https://host/x",
        method="GET",
        status=200,
        headers={"Content-Type": "text/plain"},
        content=b"hello",
    )

    with tempfile.TemporaryDirectory() as tmp:
        archive = Archive.open(Path(tmp))
        record = archive.write_response(fetched, fetched_at="2026-07-20T18:00:00Z")

    assert record.status == 200
    assert record.url == "https://host/x"


def test_http_package_source_never_imports_archive():
    # Static check: no file under connections_export/http/ may *import*
    # connections_export.archive, so the package stays decoupled from storage.
    # (Docstrings are allowed to mention it in prose, as this module does.)
    import connections_export.http as http_pkg

    package_dir = Path(http_pkg.__file__).parent
    for path in package_dir.glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(
                ("import connections_export.archive", "from connections_export.archive")
            ):
                raise AssertionError(f"{path} imports connections_export.archive: {stripped!r}")


# --- 2.2 FailureKind enum; FetchFailure carries kind + error (+status) ---


def test_failure_kind_has_transport_timeout_http_error():
    assert FailureKind.transport == "transport"
    assert FailureKind.timeout == "timeout"
    assert FailureKind.http_error == "http_error"


def test_fetch_failure_carries_kind_and_error():
    failure = FetchFailure(
        url="https://host/unreachable",
        method="GET",
        kind=FailureKind.transport,
        error="connection refused",
    )

    assert failure.url == "https://host/unreachable"
    assert failure.method == "GET"
    assert failure.kind == FailureKind.transport
    assert failure.error == "connection refused"
    assert failure.status is None


def test_fetch_failure_carries_status_for_http_error_kind():
    failure = FetchFailure(
        url="https://host/x",
        method="GET",
        kind=FailureKind.http_error,
        error="exhausted retries on 503",
        status=503,
    )

    assert failure.kind == FailureKind.http_error
    assert failure.status == 503
