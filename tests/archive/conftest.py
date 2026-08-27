"""Shared test fixtures for the archive capability tests.

`FakeResponse` is the minimal stand-in for a real HTTP response used
throughout these tests: the archive is tested with zero network and no
HTTP library. It only carries what the archive actually needs.
"""

from dataclasses import dataclass, field


@dataclass
class FakeResponse:
    url: str
    method: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
