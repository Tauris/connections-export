"""Task #25: `GET /api/manual/interchange` -- renders the interchange-
format spec to HTML for the Manual's "Full interchange format
specification" section. Driven in-process via `httpx.ASGITransport`,
mirroring `tests/gui/test_model_endpoints.py`.
"""

from __future__ import annotations

import asyncio

import httpx

from connections_export.archive.blobs import write_blob
from connections_export.archive.store import Archive
from connections_export.derive.model import DerivedPage, DerivedWiki, Interchange
from connections_export.gui import support as gui_support
from connections_export.gui.app import make_app
from connections_export.interchange.package import SPEC_COPY_FILENAME, write_package

GENERATED_AT = "2026-07-20T12:00:00Z"

# A section title that genuinely exists in docs/reference/interchange-format.md
# (§7) -- proof the response is the real spec, not a placeholder.
KNOWN_HEADING = "Reconstructing content in a target wiki"


def _run(coro):
    return asyncio.run(coro)


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        return await client.get(path)


def _minimal_interchange() -> Interchange:
    page = DerivedPage(id="p1", title="Page 1", content_html="<p>hi</p>")
    wiki = DerivedWiki(
        id="w1", label="wiki0", title="Wiki 0", root_page_ids=["p1"], pages={"p1": page}
    )
    return Interchange(base_url="https://fake", wikis=[wiki])


def test_returns_200_with_rendered_html_containing_a_known_heading():
    app = make_app(demo=True)

    response = _run(_get(app, "/api/manual/interchange"))

    assert response.status_code == 200
    body = response.json()
    assert "<h1" in body["html"] or "<h2" in body["html"]
    assert KNOWN_HEADING in body["html"]
    # Fenced code / tables extensions are wired -- the spec has both.
    assert "<table>" in body["html"]
    assert "<pre>" in body["html"] or "<code>" in body["html"]


def test_prefers_the_currently_open_packages_own_bundled_copy(tmp_path):
    # Every package written by `write_package` ships its own INTERCHANGE.md
    # (a verbatim copy of the repo doc at write time). Mutate that on-disk
    # copy after writing so it's distinguishable from the repo fallback,
    # then confirm the endpoint served the *package's* copy.
    archive = Archive.open(tmp_path / "archive")
    write_blob(archive.root, b"pixel-bytes")
    interchange = _minimal_interchange()
    dest = tmp_path / "package"
    write_package(interchange, archive, dest, generated_at=GENERATED_AT)

    marker = "MARKER-FROM-THE-OPEN-PACKAGE-NOT-THE-REPO-DOC"
    (dest / SPEC_COPY_FILENAME).write_text(f"# {marker}\n\nHello.\n", encoding="utf-8")

    app = make_app(package_dir=dest)

    response = _run(_get(app, "/api/manual/interchange"))

    assert response.status_code == 200
    assert marker in response.json()["html"]


def test_404_with_a_clear_message_when_the_spec_is_nowhere_to_be_found(monkeypatch, tmp_path):
    # The resolver lives in `gui.support` now; patch where it reads.
    monkeypatch.setattr(gui_support, "SPEC_DOC_PATH", tmp_path / "nowhere.md")
    app = make_app(demo=True)  # no package/archive open -> package_root is None

    response = _run(_get(app, "/api/manual/interchange"))

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "not_found"
    assert "message" in body and body["message"]


def test_the_shipped_spec_documents_every_field_the_model_actually_has():
    """The spec is force-included into the wheel and copied into every package
    as INTERCHANGE.md, so it is the contract a third-party ingester reads. A
    field the model has and the spec does not is a field nobody outside this
    repo knows exists; a field the spec names and the model dropped is worse,
    because an ingester will look for it.

    Asserted DIRECTIONALLY, from the model to the document. Deriving both sides
    from the code under test would pass vacuously through the exact drift this
    is meant to catch -- and a vacuous guard on a file that ships is worse than
    no guard, because it reads as covered.
    """
    from pathlib import Path

    from connections_export.derive import model as model_module

    spec = Path("docs/reference/interchange-format.md").read_text(encoding="utf-8")

    # Names too generic to look for as bare words, or deliberately internal.
    ignored = {"schema_version", "id", "title", "author"}

    missing: list[str] = []
    for name in dir(model_module):
        cls = getattr(model_module, name)
        if not (isinstance(cls, type) and hasattr(cls, "model_fields")):
            continue
        if not name.startswith(("Derived", "Interchange", "Resolved", "Link", "Provenance")):
            continue
        for field in cls.model_fields:
            if field in ignored:
                continue
            if f"`{field}`" not in spec:
                missing.append(f"{name}.{field}")

    assert not missing, (
        f"the shipped interchange spec does not document these model fields: {sorted(set(missing))}"
    )


def test_the_spec_does_not_still_describe_fields_the_model_dropped():
    """The other direction, for the names this consolidation actually removed.
    An ingester reading the shipped spec would look for these and find nothing.
    """
    from pathlib import Path

    spec = Path("docs/reference/interchange-format.md").read_text(encoding="utf-8")

    # `body_html` / `published` / `updated` survive ONLY inside the v1->v2
    # migration table in section 8, which documents them as removed. Anywhere
    # else is a stale field description.
    body, _, versioning = spec.partition("### Version 2 is a breaking change")

    for gone in ("`body_html`", "`DerivedBlogComment`"):
        assert gone not in body, f"the spec still describes {gone} as a live field"
