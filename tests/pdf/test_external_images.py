"""External (third-party) images in the PDF.

The archive keeps an external image's URL but never its bytes. The PDF offers
a choice: include them -- fetched at export time and embedded, each one marked
where it sits and listed with its original address on an "External content"
page -- or leave them out, in which case the URL still travels as text. Whether
the person may reproduce a given image is theirs to judge; the document only
has to make every such image recognisable, on paper too.
"""

from __future__ import annotations

import base64
import re

from connections_export.derive.model import (
    DerivedPage,
    DerivedWiki,
    Interchange,
    ResolvedAsset,
)
from connections_export.pdf.external import (
    collect_external_image_urls,
    fetch_external_images,
)
from connections_export.pdf.html import render_html

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 12
LOGO = "https://cdn.example.org/logo.png"
CHART = "https://charts.example.net/q3.png"


def _no_blobs(_digest: str) -> bytes | None:
    return None


def _external(url: str) -> ResolvedAsset:
    return ResolvedAsset(original_href=url, resolved_url=url, present=False, scope="external")


def _interchange(*bodies: tuple[str, str, list[ResolvedAsset]]) -> Interchange:
    pages = {
        page_id: DerivedPage(
            id=page_id, label=page_id, title=f"Title {page_id}", content_html=body, assets=assets
        )
        for page_id, body, assets in bodies
    }
    wiki = DerivedWiki(
        id="w1", label="w1", title="Wiki One", root_page_ids=list(pages), pages=pages
    )
    return Interchange(wikis=[wiki])


def _two_images() -> Interchange:
    return _interchange(
        (
            "p1",
            f'<p>Intro</p><img src="{LOGO}"><img src="{CHART}">',
            [_external(LOGO), _external(CHART)],
        ),
        ("p2", f'<img src="{LOGO}">', [_external(LOGO)]),
    )


# --- left out (the option off) ---------------------------------------------


def test_left_out_the_external_image_keeps_its_url_as_text():
    html = render_html(_two_images(), blob_bytes=_no_blobs)

    assert f"[external image not included: {LOGO}]" in html
    assert f"[external image not included: {CHART}]" in html
    assert f'src="{LOGO}"' not in html  # the browser never fetches it


def test_left_out_there_is_no_external_content_page():
    html = render_html(_two_images(), blob_bytes=_no_blobs)

    assert 'id="external-content"' not in html


def test_a_deployment_image_that_was_not_captured_now_carries_its_url():
    body = '<img src="cid:gone.png">'
    asset = ResolvedAsset(
        original_href="cid:gone.png",
        resolved_url="https://fake/gone.png",
        present=False,
        scope="same",
    )
    html = render_html(_interchange(("p1", body, [asset])), blob_bytes=_no_blobs)

    assert "[image not captured: https://fake/gone.png]" in html


# --- included (the default) -------------------------------------------------


def test_included_the_image_is_embedded_never_hotlinked():
    html = render_html(
        _two_images(), blob_bytes=_no_blobs, external_images={LOGO: PNG_BYTES, CHART: PNG_BYTES}
    )

    uri = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"
    assert uri in html
    assert f'src="{LOGO}"' not in html
    assert f'src="{CHART}"' not in html


def test_each_included_image_is_marked_where_it_sits_and_numbered_once():
    html = render_html(
        _two_images(), blob_bytes=_no_blobs, external_images={LOGO: PNG_BYTES, CHART: PNG_BYTES}
    )

    # Numbered in document order; the logo keeps E1 wherever it appears again.
    captions = re.findall(r'class="hcl-external-caption">([^<]*)<', html)
    assert captions == [
        "External image E1 · cdn.example.org",
        "External image E2 · charts.example.net",
        "External image E1 · cdn.example.org",
    ]


def test_the_external_content_page_lists_every_source_with_its_full_url():
    html = render_html(
        _two_images(), blob_bytes=_no_blobs, external_images={LOGO: PNG_BYTES, CHART: None}
    )

    page = html[html.index('id="external-content"') :]
    assert "External content" in page
    assert f'href="{LOGO}"' in page and f">{LOGO}<" in page
    assert f'href="{CHART}"' in page
    assert "included" in page
    assert "could not be retrieved" in page
    # The note: third-party, rights stay with the owners, not assessed here.
    assert "third-party" in page.lower()
    assert "not assess" in page.lower()


def test_the_external_content_page_is_in_the_table_of_contents():
    html = render_html(_two_images(), blob_bytes=_no_blobs, external_images={LOGO: PNG_BYTES})

    toc = html[html.index('id="toc"') : html.index("</nav>")]
    assert 'href="#external-content"' in toc


def test_an_image_that_could_not_be_retrieved_is_still_marked_with_its_url():
    html = render_html(_two_images(), blob_bytes=_no_blobs, external_images={LOGO: PNG_BYTES})

    assert f"[external image E2 could not be retrieved: {CHART}]" in html


def test_bare_tiles_mark_images_but_carry_no_reference_page():
    html = render_html(
        _two_images(), blob_bytes=_no_blobs, external_images={LOGO: PNG_BYTES}, chrome=False
    )

    assert "External image E1" in html
    assert 'id="external-content"' not in html


def test_no_external_images_means_no_reference_page_even_when_included():
    html = render_html(
        _interchange(("p1", "<p>plain</p>", [])), blob_bytes=_no_blobs, external_images={}
    )

    assert 'id="external-content"' not in html


# --- collecting and fetching ------------------------------------------------


def test_collect_finds_every_external_image_once_in_order():
    assert collect_external_image_urls(_two_images()) == [LOGO, CHART]


def test_collect_ignores_deployment_assets():
    same = ResolvedAsset(
        original_href="/x.png", resolved_url="https://fake/x.png", present=False, scope="same"
    )
    assert collect_external_image_urls(_interchange(("p1", "", [same]))) == []


def test_fetch_keeps_images_and_drops_what_is_not_an_image():
    served = {LOGO: PNG_BYTES, CHART: b"<html>a login page</html>"}

    fetched = fetch_external_images([LOGO, CHART], get=served.get)

    assert fetched == {LOGO: PNG_BYTES, CHART: None}


def test_fetch_records_a_failure_as_not_retrieved():
    def get(url: str) -> bytes | None:
        raise OSError("unreachable")

    assert fetch_external_images([LOGO], get=get) == {LOGO: None}


def test_browser_fidelity_marks_and_lists_them_too():
    from connections_export.pdf.browser_fidelity import render_html_browser

    html = render_html_browser(_two_images(), _no_blobs, external_images={LOGO: PNG_BYTES})

    assert "External image E1 · cdn.example.org" in html
    assert 'id="external-content"' in html
    assert f'src="{LOGO}"' not in html


def test_the_default_fetch_gets_an_image_and_sends_no_credentials(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from connections_export.pdf import external
    from connections_export.pdf.external import _default_get

    # The server below is on loopback, which the real fetch refuses
    # (`test_external_ssrf.py`); here it stands in for a public host.
    monkeypatch.setattr(external, "address_is_public", lambda _address: True)

    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(dict(self.headers))
            if self.path == "/a.png":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(PNG_BYTES)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert _default_get(f"{base}/a.png") == PNG_BYTES
        assert _default_get(f"{base}/missing.png") is None
    finally:
        server.shutdown()
        server.server_close()
    for headers in requests:
        assert "Cookie" not in headers and "Authorization" not in headers


# --- small images in running text: a light mark, not a figure frame --------

ICON = "https://icons.example.com/check.png"


def _png(width: int, height: int) -> bytes:
    import struct

    return (
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    )


def _icon_page(img: str) -> Interchange:
    return _interchange(("p1", f"<p>Done {img} and moving on.</p>", [_external(ICON)]))


def test_a_small_icon_in_text_gets_a_superscript_mark_not_a_frame():
    html = render_html(
        _icon_page(f'<img src="{ICON}">'),
        blob_bytes=_no_blobs,
        external_images={ICON: _png(16, 16)},
    )

    assert 'class="hcl-external-ref">E1</sup>' in html
    assert 'class="hcl-external-caption"' not in html
    assert 'class="hcl-external-image"' not in html
    # The sentence around it survives intact.
    assert "and moving on." in html


def test_the_size_the_page_gives_the_image_decides_over_the_file():
    big_file_shown_small = render_html(
        _icon_page(f'<img src="{ICON}" width="20" height="20">'),
        blob_bytes=_no_blobs,
        external_images={ICON: _png(512, 512)},
    )
    small_file_shown_big = render_html(
        _icon_page(f'<img src="{ICON}" style="width: 300px">'),
        blob_bytes=_no_blobs,
        external_images={ICON: _png(16, 16)},
    )

    assert 'class="hcl-external-ref"' in big_file_shown_small
    assert 'class="hcl-external-caption"' in small_file_shown_big


def test_an_image_of_unknown_size_is_treated_as_a_figure():
    html = render_html(
        _icon_page(f'<img src="{ICON}">'),
        blob_bytes=_no_blobs,
        external_images={ICON: PNG_BYTES[:8]},
    )

    assert 'class="hcl-external-caption"' in html


def test_a_small_icon_is_still_listed_with_its_address():
    html = render_html(
        _icon_page(f'<img src="{ICON}">'),
        blob_bytes=_no_blobs,
        external_images={ICON: _png(16, 16)},
    )

    page = html[html.index('id="external-content"') :]
    assert f'href="{ICON}"' in page
    assert "superscript" in page  # the note says how the small ones are marked


def test_a_small_icon_that_could_not_be_retrieved_keeps_the_sentence_short():
    html = render_html(
        _icon_page(f'<img src="{ICON}" width="16" height="16">'),
        blob_bytes=_no_blobs,
        external_images={ICON: None},
    )

    body = html[: html.index('id="external-content"')]
    assert "[E1 not retrieved]" in body
    assert ICON not in body


def test_the_small_image_threshold_is_the_callers_to_set():
    icon = _icon_page(f'<img src="{ICON}">')

    never_small = render_html(
        icon, blob_bytes=_no_blobs, external_images={ICON: _png(16, 16)}, small_image_px=0
    )
    roomier = render_html(
        icon, blob_bytes=_no_blobs, external_images={ICON: _png(64, 64)}, small_image_px=100
    )

    assert 'class="hcl-external-caption"' in never_small
    assert 'class="hcl-external-ref"' in roomier
