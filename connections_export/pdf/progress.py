"""What a PDF render tells the person waiting for it.

A large export takes minutes, and a progress bar that only says "working"
for all of them is indistinguishable from a hang. The renderers report each
step through a `ProgressCallback`; the console turns the reports into a bar
with "pages so far / of about N" and the part being worked on, the CLI into
lines on stderr.

The contract, in order of a render:

- ``preparing``  -- building the document from the archive.
- ``measuring``  -- (parts only) laying each part out once to learn its page
  count, so page numbers and "page N of M" footers can run across the
  whole document. ``pages_total`` is unknown until this finishes.
- ``laying_out`` -- paginating the current part; ``pages_done`` grows as
  pages appear.
- ``printing``   -- writing the current part to PDF (no finer progress
  exists inside this step).
- ``assembling`` -- (parts only) joining the parts, resolving links between
  them, adding bookmarks.
- ``retrying``   -- a part ran out of memory and is being split smaller.
- ``done``.

Every report is complete on its own (no deltas), so a reader that missed
some -- a reconnecting browser, a second tab -- loses nothing by reading
only the latest.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

#: The stages, in the order a render passes through them.
STAGES = (
    "preparing",
    "measuring",
    "laying_out",
    "printing",
    "assembling",
    "retrying",
    "done",
)


@dataclass(frozen=True)
class PdfProgress:
    """One report. Counts are of PDF pages, not archive items."""

    stage: str
    #: The part being worked on, 1-based; 1 of 1 for a render in one piece.
    part: int = 1
    parts: int = 1
    #: Pages finished in the whole document so far.
    pages_done: int = 0
    #: The whole document's page count, once known (after measuring).
    pages_total: int | None = None
    #: One plain sentence, ready to show as is.
    message: str = ""
    #: The file being made, 1-based, when the export is several PDFs.
    file: int = 1
    files: int = 1

    def as_dict(self) -> dict:
        return asdict(self)


ProgressCallback = Callable[[PdfProgress], None]


def ignore(_progress: PdfProgress) -> None:
    """The callback used when nobody is listening."""


class PdfOutOfMemoryError(RuntimeError):
    """The browser ran out of memory, even on the smallest part it could
    make. Carries a message meant for the person, not a traceback."""
