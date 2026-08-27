"""The `pdf` capability: render
the reconstructed wiki (the interchange model) to a portable PDF via
print-ready HTML + Chromium.

Public surface: `render_html` (the pure, tested core), `html_to_pdf`
(the thin, browser-gated Chromium wrapper), and `render_pdf` (the two
composed).
"""

from connections_export.pdf.browser import (
    CHROMIUM_AVAILABLE,
    browser_unavailable_detail,
    html_to_pdf,
    render_pdf,
)
from connections_export.pdf.browser_fidelity import render_html_browser, render_pdf_browser
from connections_export.pdf.html import DEFAULT_GENERATED_AT, render_html
from connections_export.pdf.paged import (
    PAGED_AVAILABLE,
    html_to_pdf_paged,
    render_pdf_paged,
)

__all__ = [
    "render_html",
    "render_html_browser",
    "html_to_pdf",
    "html_to_pdf_paged",
    "render_pdf",
    "render_pdf_paged",
    "render_pdf_browser",
    "CHROMIUM_AVAILABLE",
    "PAGED_AVAILABLE",
    "browser_unavailable_detail",
    "DEFAULT_GENERATED_AT",
]
