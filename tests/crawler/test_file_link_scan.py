"""Recognising a link to a Files document in a body.

The stored HTML of a wiki page
carrying file links contained two of them; the Files widget uses a third.

An export has to stand on its own, and away from the deployment a
link is not a slow path to the content but nothing at all -- so a linked
document has to be captured like an embedded image, not merely recorded.
"""

from __future__ import annotations

from connections_export.crawler.assets import scan_file_links

MEDIA = "/files/form/anonymous/api/library/LIB-1/document/DOC-1/media/spec.pdf"
MEDIA_BASIC = "/files/basic/anonymous/api/library/LIB-2/document/DOC-2/media/notes.docx"
DETAIL = "/files/app/file/DOC-3"
WIDGET = "/communities/service/html/communityview?communityUuid=C-1#fullpageWidgetId=W&file=DOC-4"


def _body(*hrefs: str) -> bytes:
    links = "".join(f'<a href="{href}">a file</a>' for href in hrefs)
    return f"<html><body><p>text</p>{links}</body></html>".encode()


def test_a_direct_media_link_is_found_under_either_auth_root():
    """The body used `form` where the feed's enclosure uses `basic`; neither
    can be hard-coded."""
    assert scan_file_links(_body(MEDIA)) == [MEDIA]
    assert scan_file_links(_body(MEDIA_BASIC)) == [MEDIA_BASIC]


def test_the_detail_link_a_person_pastes_is_found():
    assert scan_file_links(_body(DETAIL)) == [DETAIL]


def test_the_widget_link_is_found_although_its_id_is_in_the_fragment():
    """A URL normaliser strips fragments as a matter of routine, and this id
    lives in one. The failure would be silent: the link stays, the bytes
    never arrive."""
    assert scan_file_links(_body(WIDGET)) == [WIDGET]


def test_ordinary_links_are_not_files():
    """This must not become a web crawler. Anything that is not recognisably
    a Files document is left exactly as it is today: recorded, not fetched."""
    body = _body(
        "https://example.com/a-page",
        "/wikis/basic/api/wiki/eng-handbook/page/onboarding/entry",
        "/blogs/blog0/entry/hello",
        "mailto:someone@example.com",
    )

    assert scan_file_links(body) == []


def test_every_file_link_in_a_body_is_found_in_document_order():
    assert scan_file_links(_body(DETAIL, MEDIA, WIDGET)) == [DETAIL, MEDIA, WIDGET]


def test_an_empty_or_unparseable_body_is_no_links():
    assert scan_file_links(b"") == []
    assert scan_file_links(b"<<<not html") == []
