"""A community's own picture, kept in the archive.

It belongs to the community as much as its files do, and it is what someone
will reach for when putting a mark on every page of an exported PDF. Held as
an ordinary captured asset, so it survives in the archive rather than being
fetched from a system that may not answer later.

The endpoint that serves it is not documented by HCL and is not yet verified
against the deployment -- see the change-detection notes
§4b. So discovery reads the community document for ANY link that looks like an
image, rather than hard-coding a URL shape we would only be guessing at. When
the probe comes back the guess is replaced by a fact, and nothing else moves.
"""

from __future__ import annotations

from connections_export.adapters.communities import logo_href


def _entry(links: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<entry xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:snx="http://www.ibm.com/xmlns/prod/sn">'
        "<id>urn:lsid:ibm.com:communities:community-1</id>"
        "<title>Platform Engineering</title>"
        f"{links}</entry>"
    ).encode()


def test_a_link_typed_as_an_image_is_the_picture():
    body = _entry('<link rel="alternate" type="image/jpeg" href="/comm/logo.jpg"/>')

    assert logo_href(body) == "/comm/logo.jpg"


def test_a_link_whose_rel_names_it_is_the_picture():
    """Some deployments type it `application/octet-stream` and say what it is
    in the `rel` instead."""
    body = _entry('<link rel="http://www.ibm.com/xmlns/prod/sn/logo" href="/comm/logo"/>')

    assert logo_href(body) == "/comm/logo"


def test_the_alternate_web_page_is_not_mistaken_for_a_picture():
    """A community's `rel="alternate"` link is usually its HTML page. Fetching
    that as an image would put a web page in the header."""
    body = _entry('<link rel="alternate" type="text/html" href="/communities/service/html/x"/>')

    assert logo_href(body) is None


def test_the_self_link_is_not_a_picture():
    body = _entry('<link rel="self" href="/communities/service/atom/community/instance?x=1"/>')

    assert logo_href(body) is None


def test_a_community_with_no_picture_yields_nothing():
    assert logo_href(_entry("")) is None


def test_an_unreadable_document_yields_nothing_rather_than_raising():
    """Discovery runs against a live deployment where any document may be a
    login page. A missing picture must never fail the capture around it."""
    assert logo_href(b"<html><body>login</body></html>") is None
    assert logo_href(b"") is None


def test_the_first_image_wins_when_there_are_several():
    """Deterministic: two runs of the same deployment must archive the same
    bytes under the same name."""
    body = _entry(
        '<link rel="thumbnail" type="image/png" href="/first.png"/>'
        '<link rel="alternate" type="image/png" href="/second.png"/>'
    )

    assert logo_href(body) == "/first.png"
