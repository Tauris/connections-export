"""An export can be several PDFs, cut where a reader expects, linked together.

One PDF of thousands of pages is hard to open, mail or upload. The split is
proposed from the model alone -- nothing rendered -- along communities (their
subcommunities included), apps, containers, and only then inside a container.
Each file is a document of its own; a link to something printed in another file
opens that file at that page."""

from __future__ import annotations

import pytest

from connections_export.gui.demo import run_demo
from connections_export.pdf import CHROMIUM_AVAILABLE, PAGED_AVAILABLE, split


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    """The demo: a community, its subcommunity, and wikis outside any
    community, with links across all of it."""
    folder = tmp_path_factory.mktemp("demo")
    result = run_demo(lambda _e: None, delay=0, archive_dir=folder)
    return result.interchange, folder


def _names(model, **limits) -> list[str]:
    return [f.name for f in split.propose(model, split.Limits(**limits))]


def test_without_limits_everything_is_one_file(demo):
    model, _ = demo
    assert len(split.propose(model, split.Limits())) == 1


def test_one_file_per_community_gives_subcommunities_their_own(demo):
    model, _ = demo
    assert _names(model, grouping="community") == [
        "1 - Platform Engineering.pdf",
        "2 - Platform Engineering — Tooling.pdf",
        "3 - Outside any community.pdf",
    ]


def test_a_page_limit_cuts_only_as_deep_as_needed(demo):
    model, _ = demo
    loose = split.propose(model, split.Limits(max_pages=40))
    tight = split.propose(model, split.Limits(max_pages=8))
    assert len(loose) < len(tight)
    # A whole community fits 40 pages and keeps its name.
    assert loose[0].name == "1 - Platform Engineering.pdf"
    # Cut inside a wiki, a file says whose pages they are.
    assert any(f.title.startswith("Engineering Handbook – ") for f in tight)
    # Neighbours that fit together share a file.
    assert any(" and " in f.title for f in tight)


def test_every_item_is_in_exactly_one_file(demo):
    model, _ = demo
    files = split.propose(model, split.Limits(max_pages=8))
    held = [split.select_for_file(model, f.selections) for f in files]
    pages = [p for m in held for w in m.wikis for p in w.pages]
    posts = [p for m in held for b in m.blogs for p in b.post_ids]
    topics = [t for m in held for f in m.forums for t in f.topic_ids]
    assert sorted(pages) == sorted(p for w in model.wikis for p in w.pages)
    assert sorted(posts) == sorted(p for b in model.blogs for p in b.post_ids)
    assert sorted(topics) == sorted(t for f in model.forums for t in f.topic_ids)


def test_a_file_of_some_posts_shows_one_blog_in_its_own_order(demo):
    model, _ = demo
    blog = model.blogs[0]
    wanted = blog.post_ids[1:4]
    picked = split.select_for_file(model, [("post", i) for i in reversed(wanted)])
    assert [b.id for b in picked.blogs] == [blog.id]
    assert picked.blogs[0].post_ids == wanted


def test_a_wiki_piece_is_rerooted_without_its_ancestors(demo):
    model, _ = demo
    wiki = next(w for w in model.wikis if any(p.child_ids for p in w.pages.values()))
    parent = next(p for p in wiki.pages.values() if p.child_ids)
    child = parent.child_ids[0]
    picked = split.select_for_file(model, [("subtree", child)])
    [sliced] = picked.wikis
    assert parent.id not in sliced.pages
    assert sliced.root_page_ids[0] == child


def test_file_names_are_safe_everywhere():
    assert split.safe_filename('Q3: "Plans" / <draft>?') == "Q3 Plans draft"
    assert split.safe_filename("...") == "export"


@pytest.mark.skipif(not (CHROMIUM_AVAILABLE and PAGED_AVAILABLE), reason="needs Chromium")
def test_links_between_files_open_the_target_file_at_its_page(demo, tmp_path):
    from pypdf import PdfReader

    from connections_export.archive.blobs import read_blob
    from connections_export.pdf import sets

    model, folder = demo
    roots = [p.parent for p in folder.rglob("blobs") if p.is_dir()]

    def blob(digest):
        for root in roots:
            try:
                return read_blob(root, digest)
            except Exception:  # noqa: BLE001 - not in this archive
                continue
        return None

    files = split.propose(model, split.Limits(grouping="community"))
    paths = sets.render_pdf_set(model, blob, files, tmp_path)
    assert [p.name for p in paths] == [f.name for f in files]

    readers = {p.name: PdfReader(str(p)) for p in paths}
    # Where each item's bookmark points, per file.
    bookmarks: dict[str, dict[str, int]] = {}
    for name, reader in readers.items():
        marks = {}

        def walk(items, reader=reader, marks=marks):
            for item in items:
                if isinstance(item, list):
                    walk(item)
                else:
                    marks.setdefault(item.title, reader.get_destination_page_number(item))

        walk(reader.outline)
        bookmarks[name] = marks

    remote = []
    for name, reader in readers.items():
        for page in reader.pages:
            for ref in page.get("/Annots") or []:
                action = ref.get_object().get("/A")
                action = action.get_object() if action is not None else None
                if action is not None and action.get("/S") == "/GoToR":
                    remote.append((name, str(action["/F"]), int(action["/D"][0])))
    assert len(remote) > 20
    for source, target, page in remote:
        assert target in readers and target != source
        assert page < len(readers[target].pages)
        # Each lands on a page some item of the target file starts on.
        assert page in set(bookmarks[target].values())
    # And the cover says which file of how many it is.
    cover = readers[paths[1].name].pages[0].extract_text()
    assert "File 2 of 3 of this export" in cover
