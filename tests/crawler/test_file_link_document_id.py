"""The document a Files link names.

All three measured shapes carry the document id; only one of them also says
where the bytes are. Pulling the id out is what lets a link be matched against
a document the run already captured -- which resolves the link without
inventing an endpoint nobody has seen.
"""

from __future__ import annotations

from connections_export.crawler.assets import document_id_from_link


def test_the_media_shape_yields_its_document_id():
    href = "/files/form/anonymous/api/library/LIB-1/document/DOC-1/media/spec.pdf"

    assert document_id_from_link(href) == "DOC-1"


def test_the_detail_shape_yields_its_document_id():
    assert document_id_from_link("/files/app/file/DOC-3") == "DOC-3"


def test_the_widget_shape_yields_the_id_from_its_fragment():
    href = "/communities/service/html/communityview?communityUuid=C-1#widgetId=W&file=DOC-4"

    assert document_id_from_link(href) == "DOC-4"


def test_a_media_link_with_an_encoded_name_still_yields_the_id():
    """File names carry spaces and question marks; the id is unaffected."""
    href = (
        "/files/basic/anonymous/api/library/L/document/DOC-5/media/Q1%20Budget%3A%20draft%3F.xlsx"
    )

    assert document_id_from_link(href) == "DOC-5"


def test_something_that_is_not_a_file_link_yields_nothing():
    assert document_id_from_link("/wikis/basic/api/wiki/w/page/p/entry") is None
    assert document_id_from_link("https://example.com/a-page") is None
    assert document_id_from_link("") is None
