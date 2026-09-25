"""What every exporter writes about a combined export (`derive.combine`).

Only when several archives went in. A single archive's output is exactly what
it always was: `several` turns a one-archive report into `None`, and every
exporter writes nothing extra for `None`.
"""

from __future__ import annotations

from collections.abc import Callable

from connections_export.derive.combine import CombineReport


def several(report: CombineReport | None) -> CombineReport | None:
    """`report` when it combined more than one archive, else `None`."""
    return report if report is not None and len(report.archives) > 1 else None


def combined_section(report: CombineReport, text: Callable[[str], str]) -> list[str]:
    """The README section listing the archives that went in and, for every
    container more than one of them held, which archive's copy won.

    `text` makes one piece of content inert in the exporter's format: an
    archive's name is whatever its owner called it, and a title is its
    author's.
    """
    lines = [
        "## Combined from several archives",
        "",
        f"This export combines {len(report.archives)} archives. Where more than one "
        "held the same wiki, blog, forum, library, Highlights area or item, it appears "
        "once, and the copy from the most recent capture was kept — item by item, so a "
        "wiki holds every page any of the archives captured. Links from one archive to "
        "content another one holds point inside this export.",
        "",
    ]
    for label in report.archives:
        when = report.captured_at.get(label)
        suffix = f" — captured {text(when)}" if when else ""
        lines.append(f"- {text(label)}{suffix}")
    lines += [
        "",
        f"{report.duplicates_total} duplicate(s) merged; "
        f"{report.links_resolved} link(s) between archives now point inside this export.",
    ]
    if report.containers:
        lines += ["", "Held by more than one archive, and whose copy was kept:", ""]
        for container in report.containers:
            lines.append(
                f"- {text(container.kind)} {text(container.title)}: kept from "
                f"{text(container.winner)} (also in "
                + ", ".join(
                    text(label) for label in container.archives if label != container.winner
                )
                + ")"
            )
    if report.collisions:
        lines += [
            "",
            "The same id came from different deployments; these are different things "
            "and were kept apart:",
            "",
        ]
        for collision in report.collisions:
            lines.append(
                f"- {text(collision.kind)} {text(collision.id)} from {text(collision.archive)}, "
                f"kept as {text(collision.renamed_to)}"
            )
    return lines
