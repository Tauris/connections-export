"""Rendering a set of PDFs, each a document of its own, linked to each other.

`pdf.split.propose` decides which files to make; this makes them. Each file is
rendered on its own -- own cover (saying which file of how many it is, and
naming the others), own contents, own page numbers and bookmarks -- through the
parts renderer, so a large file in a set is still laid out in parts.

A link to an item printed in another file of the set is written as a
placeholder while the files are made, because where the item landed is known
only once its file exists. Afterwards each placeholder becomes a link that
opens THAT file at THAT page (PDF `GoToR`), naming the file by its name in the
same folder. Desktop viewers follow these when the files sit together;
browsers' built-in viewers ignore them, so the link text still says what it
points at.
"""

from __future__ import annotations

import dataclasses
import zipfile
from pathlib import Path
from urllib.parse import unquote

from connections_export.derive.model import Interchange
from connections_export.pdf.html import (
    DEFAULT_GENERATED_AT,
    REMOTE_TARGET,
    BlobBytes,
    link_anchors,
)
from connections_export.pdf.parts import LINK_PREFIX
from connections_export.pdf.progress import PdfProgress, ProgressCallback, ignore
from connections_export.pdf.split import FilePlan, file_of_item, select_for_file

_PX_TO_PT = 0.75


def _set_note(index: int, files: list[FilePlan]) -> str:
    others = ", ".join(f.name for n, f in enumerate(files) if n != index)
    note = f"File {index + 1} of {len(files)} of this export."
    return f"{note} The others: {others}." if others else note


def render_pdf_set(
    interchange: Interchange,
    blob_bytes: BlobBytes,
    files: list[FilePlan],
    out_dir: Path,
    *,
    progress: ProgressCallback | None = None,
    generated_at: str = DEFAULT_GENERATED_AT,
    **options,
) -> list[Path]:
    """Make each file of `files` in `out_dir` and link them to each other.
    `options` are `render_pdf_paged`'s (comments, external images, marks,
    part size, ...). Returns the files' paths, in order."""
    from connections_export.pdf.paged import SetMember, render_pdf_paged  # noqa: PLC0415

    report = progress or ignore
    out_dir.mkdir(parents=True, exist_ok=True)
    anchors = link_anchors(interchange)
    where = file_of_item(files)
    total = len(files)
    paths: list[Path] = []
    positions: list[dict] = []
    for index, plan in enumerate(files):
        subset = select_for_file(interchange, plan.selections)
        own = link_anchors(subset)
        remote = {
            item: f"{REMOTE_TARGET}{other}:{anchors[item]}"
            for item, other in where.items()
            if other != index and item in anchors and item not in own
        }
        member = SetMember(
            remote=remote,
            set_note=_set_note(index, files),
            extra_targets=set(own.values()),
        )

        def relay(report_one: PdfProgress, n: int = index, name: str = plan.name) -> None:
            stage = report_one.stage
            if stage == "done" and n + 1 < total:
                stage = "assembling"
            report(
                dataclasses.replace(
                    report_one,
                    stage=stage,
                    file=n + 1,
                    files=total,
                    message=f"File {n + 1} of {total} ({name}): {report_one.message}",
                )
            )

        path = out_dir / plan.name
        render_pdf_paged(
            subset,
            blob_bytes,
            generated_at=generated_at,
            progress=relay,
            member=member,
            output=path,
            **options,
        )
        paths.append(path)
        positions.append(member.positions_out)
    report(
        PdfProgress(
            stage="assembling",
            file=total,
            files=total,
            message=f"Linking the {total} files to each other…",
        )
    )
    for path in paths:
        _link_across(path, paths, positions)
    report(
        PdfProgress(
            stage="done",
            file=total,
            files=total,
            message=f"Done: {total} PDF files.",
        )
    )
    return paths


def _link_across(path: Path, paths: list[Path], positions: list[dict]) -> None:
    """Turn the placeholders in `path` that point into other files of the
    set into links that open that file at the target's page -- streaming
    (`pdf.join.rewrite_links`), with pypdf only as the fallback."""
    from connections_export.pdf import join  # noqa: PLC0415

    heights: dict[int, float] = {}

    def height_of(other: int) -> float:
        if other not in heights:
            from pypdf import PdfReader  # noqa: PLC0415

            # Only the first page's box is read; nothing else is loaded.
            heights[other] = float(PdfReader(str(paths[other])).pages[0].mediabox.height)
        return heights[other]

    def rewrite(body: bytes, _pages) -> bytes | None:
        uri = join.link_uri(body)
        if uri is None or not uri.startswith(LINK_PREFIX):
            return None
        name = unquote(uri[len(LINK_PREFIX) :])
        if not name.startswith(REMOTE_TARGET):
            return None
        other_text, _sep, anchor = name[len(REMOTE_TARGET) :].partition(":")
        other = int(other_text) if other_text.isdigit() else -1
        target = positions[other].get(anchor) if 0 <= other < len(positions) else None
        if target is None:
            return b""
        page_index, x, y = target
        top = height_of(other) - y * _PX_TO_PT
        action = b"/A <</S /GoToR /F %s /D [%d /XYZ %.2f %.2f 0] /NewWindow false>>" % (
            join.pdf_string(paths[other].name),
            page_index,
            x * _PX_TO_PT,
            top,
        )
        return join.link_with(body, action)

    staged = path.with_name(path.name + ".linking")
    try:
        join.rewrite_links(path, staged, rewrite)
        staged.replace(path)
        return
    except join.NotSimple:
        staged.unlink(missing_ok=True)
    _link_across_with_pypdf(path, paths, positions)


def _link_across_with_pypdf(path: Path, paths: list[Path], positions: list[dict]) -> None:
    """The general way, for a file the streaming rewrite cannot copy."""
    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415
    from pypdf.generic import (  # noqa: PLC0415
        ArrayObject,
        BooleanObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    marker = LINK_PREFIX
    writer = PdfWriter(clone_from=PdfReader(str(path)))
    heights: dict[int, float] = {}
    changed = False
    for page in writer.pages:
        annots = page.get("/Annots")
        if annots is None:
            continue
        keep = ArrayObject()
        for ref in annots.get_object():
            annot = ref.get_object()
            action = annot.get("/A")
            action = action.get_object() if action is not None else None
            uri = action.get("/URI") if action is not None else None
            if not (isinstance(uri, str) and uri.startswith(marker)):
                keep.append(ref)
                continue
            name = unquote(uri[len(marker) :])
            if not name.startswith(REMOTE_TARGET):
                keep.append(ref)
                continue
            changed = True
            other_text, _sep, anchor = name[len(REMOTE_TARGET) :].partition(":")
            other = int(other_text) if other_text.isdigit() else -1
            target = positions[other].get(anchor) if 0 <= other < len(positions) else None
            if target is None:
                continue  # nowhere to go: plain text is better than a dead link
            page_index, x, y = target
            if other not in heights:
                heights[other] = float(PdfReader(str(paths[other])).pages[0].mediabox.height)
            annot[NameObject("/A")] = DictionaryObject(
                {
                    NameObject("/S"): NameObject("/GoToR"),
                    NameObject("/F"): TextStringObject(paths[other].name),
                    NameObject("/D"): ArrayObject(
                        [
                            NumberObject(page_index),
                            NameObject("/XYZ"),
                            FloatObject(round(x * _PX_TO_PT, 2)),
                            FloatObject(round(heights[other] - y * _PX_TO_PT, 2)),
                            NumberObject(0),
                        ]
                    ),
                    NameObject("/NewWindow"): BooleanObject(False),
                }
            )
            keep.append(ref)
        page[NameObject("/Annots")] = keep
    if changed:
        with path.open("wb") as out:
            writer.write(out)


def zip_set(paths: list[Path], zip_path: Path) -> Path:
    """The files of a set in one ZIP, side by side -- the links between them
    find each other once it is unpacked into one folder."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in paths:
            # PDFs are compressed already; storing them is faster and as small.
            archive.write(path, arcname=path.name)
    return zip_path
