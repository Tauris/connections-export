"""The demo's first wiki page carries a showcase (fakeserver.prototype) that
proves, end to end, that an inline SVG, a clickable SVG area, an in-export wiki
link, and an external link all survive the crawl -> derive -> render_html
pipeline. Pure (no browser): asserts the rendered HTML; the paged PDF render is
covered by the browser-gated test in test_paged.py."""

from __future__ import annotations

import re
import tempfile

from connections_export.gui.demo import run_demo
from connections_export.gui.model_source import ModelSource
from connections_export.pdf.html import render_html


def _demo_wiki_html() -> str:
    d = tempfile.mkdtemp()
    res = run_demo(lambda _e: None, seed=0, delay=0, archive_dir=d, app_filter="wiki")
    src = ModelSource.from_archive(res.archive_dir)
    return render_html(src.get_model(), blob_bytes=lambda dg: (src.get_blob(dg) or (None,))[0])


def test_inline_svg_survives_to_render():
    html = _demo_wiki_html()
    assert "<svg" in html
    assert "links-showcase" in html  # the showcase block itself


def test_internal_links_become_in_document_anchors():
    html = _demo_wiki_html()
    start = html.find("links-showcase")
    assert start != -1
    segment = html[start : start + 1600]
    hrefs = re.findall(r'<a [^>]*href="([^"]+)"', segment)
    # Both the SVG clickable area and the prose link resolve to the same
    # sibling page's in-document anchor (in_export), not a live HCL URL.
    anchors = [h for h in hrefs if h.startswith("#p-")]
    assert len(anchors) >= 2, f"expected in-export anchors, got {hrefs}"


def test_external_link_kept_and_shown_visibly():
    html = _demo_wiki_html()
    assert "https://example.com/architecture-spec" in html
    # External/deployment links are shown with their URL visible so they survive
    # on paper (the hcl-link-url span).
    assert "hcl-link-url" in html
