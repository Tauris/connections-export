"""Fetch result types: the seam to the archive.

`http` deliberately does not import `connections_export.archive` — it stays
decoupled from storage. `Fetched` merely duck-types the minimal shape
`Archive.write_response` needs (url/method/status/headers/content), so
the crawler can pass a `Fetched` straight through without translation.

`FetchFailure` is reserved for when no usable response was received at
all (transport error, timeout, or retries on a 5xx exhausted with no
final body) — never for a received response, however bad its status.
A 4xx/5xx that carried a body is a `Fetched`; the caller classifies it.
"""

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass
class Fetched:
    """A response actually received, regardless of status.

    Duck-types `archive.Archive.write_response`'s `response` argument:
    `.url`, `.method`, `.status`, `.headers`, `.content`.
    """

    url: str
    method: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""


class FailureKind(StrEnum):
    """How a fetch failed to produce a usable response."""

    transport = "transport"  # connection refused/reset, DNS, TLS
    timeout = "timeout"
    http_error = "http_error"  # retries on a 4xx/5xx exhausted with no final body


@dataclass
class FetchFailure:
    """No usable response was received."""

    url: str
    method: str
    kind: FailureKind
    error: str
    status: int | None = None  # present for http_error


FetchResult = Fetched | FetchFailure
