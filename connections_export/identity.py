"""Deciding whether two recorded identities are the same person.

An author filter keeps an item when the target matches its author name, its
`author_userid`, or any of its contributors. HCL records the same person
differently in different feeds -- display name here, account id there,
sometimes only as a contributor -- so every comparison goes through one
normalisation.

There were three byte-identical copies of it, in three packages
(`derive.author_filter`, `compare.author_compare`, `crawler.author_plan`), and
`crawler.crawl` reached past all of them into `author_plan`'s private one.
Author matching decides what a filtered export keeps, so three implementations
of it were three chances for a run to quietly keep the wrong set.
"""

from __future__ import annotations


def norm_identity(value: str | None) -> str | None:
    """Stripped and lowercased, or `None` for anything empty or non-string.

    `None` rather than `""` on purpose: an absent identity must never compare
    equal to another absent identity, or every unattributed item would match
    every filter.
    """
    return value.strip().lower() if isinstance(value, str) and value.strip() else None
