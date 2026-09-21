"""A community's Files library travels, and a body link to a file resolves.

The exporters grew wikis -> blogs/forums but never carried the Files
library. The user asked for it "especially for things that are referenced":
a wiki/blog/forum body that links to a document must point at the exported
copy, not the dead deployment URL. Both exporters now write each file's
bytes, resolve `target_file_id` links to them, and list every file (present
-> link, absent -> visible gap).
"""

from __future__ import annotations

from connections_export.derive.model import (
    DerivedBlog,
    DerivedBlogPost,
    DerivedFile,
    DerivedFileLibrary,
    Interchange,
    LinkRef,
    ResolvedAsset,
)
from connections_export.ingest import from_source, write_jekyll_site, write_obsidian_vault

_DOC = "sha256:" + "c" * 64  # a present file's bytes
_GONE = "sha256:" + "d" * 64  # referenced but not captured


def _model() -> Interchange:
    # A blog post that links to a captured file and to an uncaptured one.
    post = DerivedBlogPost(
        id="p1",
        title="Announcement",
        content_html=(
            '<p>See the <a href="/files/plan">plan</a> and the '
            '<a href="/files/missing">old draft</a>.</p>'
        ),
        links=[
            LinkRef(original_href="/files/plan", scope="in_export", target_file_id="f-plan"),
            LinkRef(original_href="/files/missing", scope="in_export", target_file_id="f-gone"),
        ],
    )
    blog = DerivedBlog(id="b", title="News", post_ids=["p1"], posts={"p1": post})
    library = DerivedFileLibrary(
        id="lib",
        title="Team Documents",
        file_ids=["f-plan", "f-gone"],
        files={
            "f-plan": DerivedFile(
                id="f-plan",
                name="Q3 Plan.pdf",
                content_type="application/pdf",
                asset=ResolvedAsset(
                    original_href="/files/plan",
                    resolved_url="https://fake/files/plan",
                    blob_hash=_DOC,
                    present=True,
                    scope="same",
                ),
            ),
            "f-gone": DerivedFile(
                id="f-gone",
                name="Old Draft.docx",
                asset=ResolvedAsset(
                    original_href="/files/missing",
                    resolved_url="https://fake/files/missing",
                    blob_hash=_GONE,
                    present=False,
                    scope="same",
                ),
            ),
        },
    )
    return Interchange(blogs=[blog], file_libraries=[library])


def _blobs():
    data = {_DOC: b"%PDF-1.4 the real plan"}
    return lambda h: data.get(h)


class _Source:
    def get_model(self):
        return _model()

    def get_blob(self, blob_hash):
        data = _blobs()(blob_hash)
        return (data, "application/pdf") if data is not None else None


# --- Obsidian ----------------------------------------------------------------


def test_obsidian_writes_the_file_bytes_and_lists_them(tmp_path):
    stats = write_obsidian_vault(_model(), _blobs(), tmp_path)

    assert stats.libraries == 1 and stats.files == 2
    written = list((tmp_path / "attachments").glob("*"))
    assert any(f.read_bytes() == b"%PDF-1.4 the real plan" for f in written)

    index = (tmp_path / "Files.md").read_text(encoding="utf-8")
    assert "Team Documents" in index
    assert "[not captured: Old Draft.docx]" in index  # the absent one, a visible gap
    assert (tmp_path / "README.md").read_text(encoding="utf-8").count("[[Files]]") == 1


def test_obsidian_resolves_a_referenced_file_link(tmp_path):
    write_obsidian_vault(_model(), _blobs(), tmp_path)
    body = (tmp_path / "News" / "Announcement.md").read_text(encoding="utf-8")

    # The captured file becomes an Obsidian link to the stored file...
    assert "[[Q3 Plan|plan]]" in body or "[[Q3 Plan.pdf|plan]]" in body
    # ...and the /files/plan deployment URL is gone from the link.
    assert "](/files/plan)" not in body


# --- Jekyll ------------------------------------------------------------------


def test_jekyll_writes_the_file_and_resolves_the_link(tmp_path):
    stats = write_jekyll_site(_model(), _blobs(), tmp_path)

    assert stats.libraries == 1 and stats.files == 2
    files_dir = tmp_path / "assets" / "files"
    assert files_dir.is_dir()
    assert any(f.read_bytes() == b"%PDF-1.4 the real plan" for f in files_dir.glob("*"))

    listing = (tmp_path / "files.md").read_text(encoding="utf-8")
    assert "/assets/files/" in listing
    assert "[not captured: Old Draft.docx]" in listing

    post = next((tmp_path / "_posts").glob("*.md")).read_text(encoding="utf-8")
    assert "/assets/files/" in post and "relative_url" in post
    assert "](/files/plan)" not in post


# --- via a ModelSource-shaped source (the real --archive path) ---------------


def test_from_source_writes_files_through_get_blob(tmp_path):
    stats = from_source(_Source(), tmp_path / "vault")
    assert stats.files == 2
    assert any(
        f.read_bytes() == b"%PDF-1.4 the real plan"
        for f in (tmp_path / "vault" / "attachments").glob("*")
    )
