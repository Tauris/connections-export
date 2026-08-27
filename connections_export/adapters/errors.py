"""`AdapterError`: the single typed exception the adapters package
raises across its public boundary.

the design, "Resilience": malformed XML, a missing required element, or
a wrong root element must never let a raw lxml (or json) exception
escape the adapter. project.md's principle 1 -- the archive stores raw
bytes and never parses them -- is why this is safe to be non-fatal: a
crawler that catches `AdapterError` and records the entity "unparsed"
has lost nothing recoverable; the bytes are already on disk, and a fix
here only costs a re-derive, not a re-crawl.
"""

from __future__ import annotations


class AdapterError(Exception):
    """Raised for malformed input, an unexpected root element, or a
    missing required field that the adapter cannot make sense of.

    `detail` carries a short diagnostic -- the offending fragment, the
    underlying parser's message, or similar -- so a crawl log records
    something actionable rather than a bare message.
    """

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        if self.detail:
            return f"{self.message} ({self.detail})"
        return self.message
