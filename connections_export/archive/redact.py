"""Header redaction.

A *denylist* of known-sensitive header names (credentials/session), whose
values are replaced while every other header passes through unchanged —
the archive needs benign headers like Content-Type and ETag intact
(content_type is even read from them). This is deliberately NOT the
sanitizer's deny-by-default allowlist: that model governs response
*bodies*, where unknown content must be assumed sensitive; here, unknown
*headers* are assumed benign and only the named secrets are removed.

Called from inside the archive writer only (`store.py`), so there is no
write path that can bypass it.
"""

from collections.abc import Iterable, Mapping

REDACTED_PLACEHOLDER = "<redacted>"

DEFAULT_DENY_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "proxy-authorization",
    }
)


def redact_headers(headers: Mapping[str, str], deny: Iterable[str] | None = None) -> dict[str, str]:
    """Return a copy of `headers` with denied header values replaced.

    Header presence is preserved (the key stays, with its original
    casing); only the value is replaced, and only for names matching the
    deny set case-insensitively. `headers` is never mutated in place.
    """
    deny_lower = {name.lower() for name in (deny if deny is not None else DEFAULT_DENY_HEADERS)}
    return {
        name: REDACTED_PLACEHOLDER if name.lower() in deny_lower else value
        for name, value in headers.items()
    }
