"""Turning one cutoff into whatever a feed will accept.

Three apps, three answers
(the published API reference, `hcl-wikis-api.md`):

* **blogs** take `since` as RFC 3339,
* **forums** take `since` as **epoch milliseconds**,
* **wikis** honour neither, and must be filtered client-side,
* **search** has no `since` at all, but has `sortKey=date`, which is enough:
  page newest-first and stop at the cutoff.

The split is not cosmetic. A date sent in the wrong format is either rejected
or -- far worse -- ignored, and an update that silently receives the whole feed
back looks exactly like one that worked. So conversion lives here, once, and
refuses what it cannot parse rather than dropping it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from connections_export.adapters.profiles import AppProfile


def _colons_in_the_time(text: str) -> str:
    """`2026-08-25T08-34-29Z` -> `2026-08-25T08:34:29Z`; anything else, itself."""
    date, sep, time = text.partition("T")
    return f"{date}{sep}{time.replace('-', ':')}" if sep else text


def parse_cutoff(value: str) -> datetime:
    """A provenance timestamp -> an aware `datetime`.

    Accepts the shapes the archive actually writes: a `Z` suffix, an explicit
    offset, or a naive stamp (read as UTC, which is what every timestamp this
    project writes means).
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("empty cutoff")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # Archives written before the run stamp and the run id were separated
        # hold `2026-08-25T08-34-29Z` -- dashes in the TIME, because one string
        # was doing both jobs and a colon is not legal in a Windows filename.
        # Those archives exist; refusing to read them would leave them unable
        # to update, which is the bug this shape caused in the first place.
        try:
            parsed = datetime.fromisoformat(_colons_in_the_time(text).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"cannot read {value!r} as a date") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def as_rfc3339(value: str) -> str:
    """The blog entries feed's format."""
    return parse_cutoff(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{parse_cutoff(value).astimezone(UTC).microsecond // 1000:03d}Z"
    )


def as_epoch_millis(value: str) -> str:
    """The forum topics feed's format.

    Milliseconds, not seconds: a seconds value is read as 1970 and returns the
    entire forum -- an update that appears to work while re-downloading
    everything.
    """
    return str(int(parse_cutoff(value).timestamp() * 1000))


def with_margin(value: str, *, seconds: int = 60) -> str:
    """The cutoff, moved back by a safety margin, as RFC 3339 with `Z`.

    Two reasons for the margin, and both are about not losing an item rather
    than about tidiness: small clock differences between this machine and the
    deployment (the server evaluates `since` against ITS clock, while the
    anchor is ours), and edits in flight around the boundary, where timestamp
    rounding differs per app.

    The margin can only re-check a minute's worth of items that turn out
    unchanged. It can never skip one.
    """
    moved = parse_cutoff(value).timestamp() - seconds
    return datetime.fromtimestamp(moved, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def encode_for(profile: AppProfile, value: str) -> str:
    """Encode a cutoff the way `profile`'s app expects to receive it.

    Blogs takes RFC-3339 and Forums takes epoch milliseconds, under the same
    parameter name. `AppProfile` has recorded that divergence since it was
    written, and its own docstring argues it must be data "so a single constant
    would not silently clamp or mis-encode one app or the other" -- and then
    four call sites chose by hand and never read the field. Only the fake
    server consulted `AppProfile` at all.

    Raises rather than passing through for an app whose feeds take no cutoff.
    A feed that ignores `since` returns everything, which a caller would read
    as "everything changed"; or returns nothing, which reads as "nothing
    changed" for content that did move. Both are worse than an error.
    """
    if profile.since_encoding == "rfc3339":
        return as_rfc3339(value)
    if profile.since_encoding == "epoch_ms":
        return as_epoch_millis(value)
    raise ValueError(
        f"{profile.name} cannot be filtered by date (since_encoding={profile.since_encoding!r})"
    )
