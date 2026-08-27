"""A link to a document the run captured resolves inside the archive.

The point of capturing a linked file is that the link still works after the
archive is read on its own. A captured document therefore has to be reachable
from the body that linked it -- otherwise the bytes are in the archive and
nothing points at them.

Resolution is against what the run captured, deliberately: it needs no lookup
endpoint, and the honest answer for a document from a community nobody
captured is that the reference is unresolved.
"""

from __future__ import annotations

from connections_export.derive.links import resolve_link

BASE = "https://fake"
MEDIA = "/files/basic/anonymous/api/library/LIB-1/document/DOC-1/media/spec.pdf"
DETAIL = "/files/app/file/DOC-1"
OTHER = "/files/app/file/DOC-NOT-CAPTURED"


def _resolve(href, files=None):
    return resolve_link(
        href,
        page_url=f"{BASE}/wikis/page",
        self_page_id="page-1",
        base_url=BASE,
        hcl_hosts=[],
        page_lookup={},
        file_lookup=files or {},
    )


def test_a_link_to_a_captured_document_is_in_export():
    ref = _resolve(DETAIL, files={"DOC-1": "file-1"})

    assert ref.scope == "in_export"
    assert ref.target_file_id == "file-1"


def test_the_media_shape_resolves_to_the_same_document():
    ref = _resolve(MEDIA, files={"DOC-1": "file-1"})

    assert ref.scope == "in_export"
    assert ref.target_file_id == "file-1"


def test_a_document_nobody_captured_stays_an_honest_deployment_link():
    """It keeps its original href and says it is not in the export, rather
    than pretending to resolve."""
    ref = _resolve(OTHER, files={"DOC-1": "file-1"})

    assert ref.scope == "hcl_deployment"
    assert ref.target_file_id is None
    assert ref.original_href == OTHER


def test_an_ordinary_page_link_is_unaffected():
    ref = _resolve("/wikis/basic/api/wiki/w/page/p/entry", files={"DOC-1": "file-1"})

    assert ref.target_file_id is None
