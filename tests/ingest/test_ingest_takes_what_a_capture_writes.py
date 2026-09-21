"""`ingest` takes what a capture actually produces, and lays all of it out.

The first user report: "the obsidian did not work as some json file was
missing". They were right, and it was not their mistake. `ingest` required
`--package`, the manual said every capture produces a package, and nothing
wrote one -- the package writer existed and no command called it. The
console writes an ARCHIVE (`manifest.jsonl`, `blobs/`), which has no
`interchange.json`, so the documented command failed for everyone on the
first try with a traceback naming a file they had never heard of.

They wanted a blog in Markdown. Had the file been there, the vault would
have been empty: the writer laid out wikis only. Both halves are pinned
here, end to end from a real capture, through the command a person types.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from connections_export import cli
from connections_export.gui.demo import run_demo


@pytest.fixture(scope="module")
def capture(tmp_path_factory) -> Path:
    """A real archive, exactly as the console writes it -- not a package."""
    archive = tmp_path_factory.mktemp("capture") / "archive"
    run_demo((lambda _e: None), archive_dir=archive, delay=0)
    assert (archive / "manifest.jsonl").is_file()
    assert not (archive / "interchange.json").exists()
    return archive


# --- the reported failure -------------------------------------------------


def test_ingest_accepts_the_directory_a_capture_wrote(capture, tmp_path, capsys):
    """The command as a person would type it after a capture. No package
    step, no json file to know about."""
    code = cli.ingest_main(["--archive", str(capture), "--output", str(tmp_path / "vault")])

    assert code == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "from the archive" in out
    assert (tmp_path / "vault" / "README.md").is_file()


def test_package_flag_pointed_at_an_archive_still_works(capture, tmp_path):
    """The manual said `--package`; the directory they had was an archive.
    Either flag with either kind: the path says what it is."""
    code = cli.ingest_main(["--package", str(capture), "--output", str(tmp_path / "vault")])

    assert code == 0


def test_a_directory_that_is_neither_says_so_in_words(tmp_path, capsys):
    """Not a traceback naming `interchange.json`: what was found, what is
    accepted, and where the accepted thing comes from."""
    nothing = tmp_path / "nothing"
    nothing.mkdir()

    code = cli.ingest_main(["--archive", str(nothing), "--output", str(tmp_path / "vault")])

    assert code == 1
    err = capsys.readouterr().err
    assert "neither a package" in err and "nor an archive" in err
    assert "manifest.jsonl" in err
    assert "Traceback" not in err


def test_ingest_reads_a_zipped_archive_too(capture, tmp_path):
    import shutil

    zipped = shutil.make_archive(str(tmp_path / "capture"), "zip", capture)

    code = cli.ingest_main(["--archive", zipped, "--output", str(tmp_path / "vault")])

    assert code == 0
    assert (tmp_path / "vault" / "README.md").is_file()


# --- what they wanted: a blog in Markdown ------------------------------------


def test_blog_posts_become_notes(capture, tmp_path):
    """A folder per blog, a note per post, in feed order, with comments."""
    from connections_export.gui.model_source import ModelSource
    from connections_export.ingest import from_source

    source = ModelSource.from_archive(capture)
    model = source.get_model()
    assert model.blogs, "the demo captures blogs; the fixture has none"

    stats = from_source(source, tmp_path / "vault")

    assert stats.blogs == len(model.blogs)
    assert stats.posts == sum(len(b.posts) for b in model.blogs)
    assert stats.posts > 0
    blog = model.blogs[0]
    first = blog.posts[blog.post_ids[0]]
    notes = list((tmp_path / "vault").rglob("*.md"))
    matching = [n for n in notes if n.stem.startswith(first.title[:20])]
    assert matching, f"no note for the first post {first.title!r}"
    body = matching[0].read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert "kind: post" in body
    assert f"# {first.title}" in body
    if first.comments:
        assert "## Comments" in body


def test_forum_topics_become_notes_with_their_reply_tree(capture, tmp_path):
    """A note per topic; replies beneath it as nested headings, each reply's
    body converted like the topic's rather than flattened to one line."""
    from connections_export.gui.model_source import ModelSource
    from connections_export.ingest import from_source

    source = ModelSource.from_archive(capture)
    model = source.get_model()
    assert model.forums, "the demo captures forums; the fixture has none"

    stats = from_source(source, tmp_path / "vault")

    assert stats.forums == len(model.forums)
    assert stats.topics == sum(len(f.topics) for f in model.forums)
    threaded = next(t for f in model.forums for t in f.topics.values() if t.replies and t.reply_ids)
    notes = [
        n
        for n in (tmp_path / "vault").rglob("*.md")
        if "kind: topic" in n.read_text(encoding="utf-8")
    ]
    note = next(n for n in notes if f"# {threaded.title}" in n.read_text(encoding="utf-8"))
    body = note.read_text(encoding="utf-8")
    assert "## Replies" in body
    first_reply = threaded.replies[threaded.reply_ids[0]]
    assert (first_reply.author or "Unknown") in body
    # A nested reply sits one heading level deeper than its parent.
    nested = next((r for r in threaded.replies.values() if r.child_ids), None)
    if nested is not None:
        assert "#### " in body


def test_a_wiki_only_capture_reads_as_before(tmp_path):
    """Nothing about the wiki layout moved: the same notes, in the same
    folders, with the same frontmatter -- and no `kind:` key, which a page
    never carried."""
    from connections_export.ingest import from_source

    class _WikiOnly:
        def __init__(self, model):
            self._model = model

        def get_model(self):
            return self._model

        def get_blob(self, blob_hash):
            return None

    archive = tmp_path / "archive"
    result = run_demo((lambda _e: None), archive_dir=archive, delay=0)
    model = result.interchange.model_copy(update={"blogs": [], "forums": []})

    stats = from_source(_WikiOnly(model), tmp_path / "vault")

    assert stats.wikis == 3 and stats.pages == 20
    assert stats.blogs == stats.posts == stats.forums == stats.topics == 0
    onboarding = tmp_path / "vault" / "Engineering Handbook" / "Onboarding.md"
    assert onboarding.is_file()
    assert "kind:" not in onboarding.read_text(encoding="utf-8").split("---\n")[1]


def test_the_readme_indexes_every_container(capture, tmp_path):
    from connections_export.gui.model_source import ModelSource
    from connections_export.ingest import from_source

    from_source(ModelSource.from_archive(capture), tmp_path / "vault")

    readme = (tmp_path / "vault" / "README.md").read_text(encoding="utf-8")
    assert "## Wikis" in readme and "## Blogs" in readme and "## Forums" in readme


# --- and the package the manual promised -----------------------------------


def test_package_writes_the_documented_package(capture, tmp_path, capsys):
    """`connections-export package` produces what the manual has always said
    a capture produces. Then `ingest --package` reads it -- the path the
    user was told to take, now with a first step that exists."""
    package = tmp_path / "package"

    code = cli.package_main(["--archive", str(capture), "--output", str(package)])

    assert code == 0, capsys.readouterr().err
    for name in ("interchange.json", "manifest.json", "provenance.json", "INTERCHANGE.md"):
        assert (package / name).is_file(), name
    assert (package / "blobs").is_dir()

    code = cli.ingest_main(["--package", str(package), "--output", str(tmp_path / "vault")])
    assert code == 0
    assert "from the package" in capsys.readouterr().out


def test_package_refuses_to_package_a_package(tmp_path, capture, capsys):
    package = tmp_path / "package"
    assert cli.package_main(["--archive", str(capture), "--output", str(package)]) == 0

    code = cli.package_main(["--archive", str(package), "--output", str(tmp_path / "again")])

    assert code == 1
    assert "already a package" in capsys.readouterr().err
