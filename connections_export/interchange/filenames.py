"""Turn Connections file names into filesystem names, without losing any.

A library is a flat namespace with almost no rules; a filesystem is neither.
Materialising files under their own names -- which is the whole point, since a
package of hex digests is not an archive anyone can use -- means every one of
these has to be survivable:

  * two files genuinely called `Report.pdf` in one folder
  * `Report.pdf` and `report.pdf`, which are two files in Connections and ONE
    on Windows and macOS
  * `é` composed vs decomposed: two names in Connections, one on macOS, which
    stores NFD
  * `a/b`, `x:y`, `q?`, and the rest of what Windows forbids
  * `CON`, `PRN`, `AUX`, `NUL`, `COM1`..`LPT9` -- reserved device names, still,
    and still unopenable with any extension
  * trailing dots and spaces, which Windows silently strips, turning `foo.` and
    `foo` into a collision that was not there before
  * names long enough to breach MAX_PATH once a folder path is in front
  * a name that sanitises away to nothing

The rule that matters most is not in that list: **disambiguation must not depend
on ordering**. A counter (`Report (1)`, `Report (2)`) assigns names by whatever
order the feed happened to return, so two exports of an unchanged library can
swap them -- and a diff between packages then shows changes that did not happen.
The suffix here is derived from the file's own id, so a given file gets the same
name whenever it is exported, whatever came before it.

The original name is always recorded in the manifest. Nothing here is
reversible by inspection, and it should never need to be guessed at.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

#: Windows forbids these outright; `/` and NUL are impossible on POSIX too.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: Reserved even with an extension: `CON.txt` is as unopenable as `CON`.
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

#: Leaves room for a folder path in front of it before MAX_PATH bites.
MAX_STEM = 120


def _split_extension(name: str) -> tuple[str, str]:
    """`("Report", ".pdf")`. A leading dot is part of the stem, not an
    extension: `.gitignore` is a name, not an empty name with a suffix."""
    if "." not in name[1:]:
        return name, ""
    stem, _, suffix = name.rpartition(".")
    # An empty suffix means a TRAILING DOT, not an extension. Treating `report.`
    # as stem `report` plus extension `.` put the dot back after the stem had
    # been cleaned, and Windows would then strip it -- recreating the very
    # collision the cleaning exists to prevent.
    if not stem or not suffix or len(suffix) > 12:
        return name, ""
    return stem, f".{suffix}"


#: Why a written name differs from the one Connections holds. Recorded per file
#: so a consumer can tell a rename happened -- and, with `original`, undo it on
#: a target system that can represent what a filesystem could not.
REASON_FORBIDDEN = "forbidden-characters"
REASON_RESERVED = "reserved-device-name"
REASON_TRAILING = "trailing-dot-or-space"
REASON_TRUNCATED = "truncated"
REASON_EMPTY = "empty-after-cleaning"
REASON_COLLISION = "collision"


@dataclass(frozen=True)
class PlannedName:
    """What one file is written as, and everything needed to undo it."""

    file_id: str
    #: Exactly as Connections holds it. The authority for any reconstruction.
    original: str
    #: What is written to disk, unique within its directory.
    name: str
    #: Empty when `name` is simply `original`. Non-empty means a target system
    #: that can represent the original is entitled to restore it.
    reasons: tuple[str, ...] = ()


def sanitize(name: str) -> str:
    """One name, made safe to write. Not yet unique -- see `allocate`."""
    return _sanitize_with_reasons(name)[0]


def _sanitize_with_reasons(name: str) -> tuple[str, tuple[str, ...]]:
    """`sanitize`, also reporting what had to be changed and why."""
    reasons: list[str] = []
    name = unicodedata.normalize("NFC", (name or "").strip())
    stem, suffix = _split_extension(name)

    cleaned_stem = _FORBIDDEN.sub("_", stem)
    cleaned_suffix = _FORBIDDEN.sub("_", suffix)
    if (cleaned_stem, cleaned_suffix) != (stem, suffix):
        reasons.append(REASON_FORBIDDEN)
    stem, suffix = cleaned_stem, cleaned_suffix

    # Windows strips these silently, which would fabricate a collision.
    stripped = stem.rstrip(". ")
    if stripped != stem:
        reasons.append(REASON_TRAILING)
    stem = stripped

    if stem.lower() in _RESERVED:
        stem = f"{stem}_"
        reasons.append(REASON_RESERVED)
    if len(stem) > MAX_STEM:
        stem = stem[:MAX_STEM].rstrip(". ")
        reasons.append(REASON_TRUNCATED)
    if not stem:
        stem = "unnamed"
        reasons.append(REASON_EMPTY)
    return f"{stem}{suffix}", tuple(reasons)


def collision_key(name: str) -> str:
    """What counts as "the same name" to a filesystem we do not control.

    Casefolded and NFC-normalised, because Windows and macOS will treat
    `Report.pdf`/`report.pdf` and composed/decomposed `é` as one file even
    though Connections holds two.
    """
    return unicodedata.normalize("NFC", name).casefold()


def _disambiguator(file_id: str) -> str:
    """A short, stable tag for a file, derived from its identity.

    Deliberately not a counter. A counter numbers files by the order they
    arrived, so an unchanged library can produce different names on a second
    export and a diff shows changes that never happened.
    """
    return hashlib.sha256(file_id.encode("utf-8")).hexdigest()[:6]


def plan(entries: list[tuple[str, str]]) -> list[PlannedName]:
    """`[(file_id, original_name)]` -> what to write, and why it differs.

    This is the form the manifest should record. `allocate` is the same thing
    reduced to a name lookup, for callers that only need to write bytes.

    The reasons are what make a package reconstructible rather than merely
    usable. Copying `files/` out is enough for most purposes; a target system
    that *can* hold what a filesystem could not -- two files differing only in
    case, a name with a colon, one file in three folders -- needs to know that a
    compromise was made and what the original was, or it inherits our
    limitations permanently.
    """
    cleaned = {file_id: _sanitize_with_reasons(name) for file_id, name in entries}
    originals = dict(entries)

    counts: dict[str, int] = {}
    for name, _ in cleaned.values():
        counts[collision_key(name)] = counts.get(collision_key(name), 0) + 1

    planned: dict[str, PlannedName] = {}
    for file_id, (name, reasons) in cleaned.items():
        if counts[collision_key(name)] > 1:
            stem, suffix = _split_extension(name)
            name = f"{stem} ({_disambiguator(file_id)}){suffix}"
            reasons = (*reasons, REASON_COLLISION)
        planned[file_id] = PlannedName(file_id, originals[file_id], name, reasons)

    # A suffixed name could, absurdly, still collide. Settle it deterministically
    # rather than write two files to one path.
    seen: dict[str, str] = {}
    for file_id in sorted(planned):
        entry = planned[file_id]
        if collision_key(entry.name) in seen:
            stem, suffix = _split_extension(entry.name)
            planned[file_id] = PlannedName(
                file_id,
                entry.original,
                f"{stem} ({_disambiguator(file_id + '!')}){suffix}",
                entry.reasons
                if REASON_COLLISION in entry.reasons
                else (*entry.reasons, REASON_COLLISION),
            )
        seen[collision_key(planned[file_id].name)] = file_id
    return [planned[file_id] for file_id, _ in entries]


def allocate(entries: list[tuple[str, str]]) -> dict[str, str]:
    """`[(file_id, original_name)]` -> `{file_id: unique safe filename}`.

    Names that do not collide keep their sanitised form. Every member of a
    colliding set is suffixed -- including the first -- so which file keeps the
    plain name never depends on ordering, and adding a colliding file later does
    not rename the one already there.
    """
    return {entry.file_id: entry.name for entry in plan(entries)}
