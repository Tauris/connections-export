""": `derive` works from a wiki-scoped archive that
never fetched the wikis feed (`crawl(..., wiki_labels=[...])`, the
setup-GUI's single-wiki import). Root discovery + wiki enumeration fall
back to the archived per-wiki nav feeds. Builds real archives with the
genuine crawler against the in-process fakeserver.
"""

from pathlib import Path

from connections_export.archive.store import Archive
from connections_export.config import Config
from connections_export.crawler.crawl import crawl
from connections_export.derive import derive
from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize
from tests.crawler.conftest import make_client


def _config(tmp_path: Path) -> Config:
    return Config(base_url="https://fake", output_dir=tmp_path / "archive")


def _synth(seed: int):
    return synthesize(
        SynthSeed(
            seed=seed,
            wiki_count=3,
            depth=2,
            pages_per_level=2,
            comments_per_page=2,
            versions_per_page=1,
            attachments_per_page=1,
            human_names=True,
        )
    )


def _scoped_archive(tmp_path: Path, seed: int, labels: list[str]) -> Archive:
    wikiset = _synth(seed)
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")
    crawl(config=_config(tmp_path), client=client, archive=archive, wiki_labels=labels)
    return archive


def test_scoped_archive_derives_the_named_wiki_with_content(tmp_path):
    wikiset = _synth(1)
    target = wikiset.wikis[0]
    archive = _scoped_archive(tmp_path, 1, [target.label])

    # Sanity: this archive genuinely has no wikis feed (the gap's cause).
    assert not any("/wikis/basic/api/wikis/feed" in url for url in archive.seen_urls())

    model = derive(archive)

    assert len(model.wikis) == 1
    wiki = model.wikis[0]
    assert wiki.label == target.label
    assert wiki.pages, "the scoped wiki's pages must be derived"
    # Real per-page content came through (body + threaded comments).
    a_page = next(p for p in wiki.pages.values() if p.content_html)
    assert "wikiPage" in a_page.content_html
    assert any(p.comments for p in wiki.pages.values()), "comments should derive too"


def test_scoped_multi_label_archive_derives_all_named(tmp_path):
    wikiset = _synth(2)
    first, second, _third = wikiset.wikis
    archive = _scoped_archive(tmp_path, 2, [first.label, second.label])

    model = derive(archive)
    derived_labels = {w.label for w in model.wikis}
    assert derived_labels == {first.label, second.label}


def test_unscoped_archive_still_derives_via_the_wikis_feed(tmp_path):
    """Regression: with the wikis feed present, enumeration uses it
    exactly as before (every wiki, real titles)."""
    wikiset = _synth(3)
    app = make_app(wikiset)
    client = make_client(app)
    archive = Archive.open(tmp_path / "archive")
    crawl(config=_config(tmp_path), client=client, archive=archive)  # unscoped

    assert any("/wikis/basic/api/wikis/feed" in url for url in archive.seen_urls())

    model = derive(archive)
    assert {w.label for w in model.wikis} == {w.label for w in wikiset.wikis}
    # The wikis feed carries real titles (not label-as-title fallback).
    assert {w.title for w in model.wikis} == {w.title for w in wikiset.wikis}
