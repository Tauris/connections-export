"""The demo dataset (`build_prototype_wikiset`) reproduces the design
prototype's exact content (static/console.html). Locks the wikis, page
tree, per-page comment counts, comment texts/authors, images, and
attachments so the demo can never silently drift back to generic data.
"""

import html

from connections_export.fakeserver.prototype import (
    _AUTHORS,
    build_prototype_wikiset,
    genthumb_svg,
)


def _wiki(wikiset, label):
    return next(w for w in wikiset.wikis if w.label == label)


def test_exact_wikis_and_titles():
    ws = build_prototype_wikiset()
    assert [(w.label, w.title) for w in ws.wikis] == [
        ("eng-handbook", "Engineering Handbook"),
        ("product-wiki", "Product Wiki"),
        ("ops-kb", "Operations KB"),
    ]


def test_exact_page_titles_and_tree():
    ws = build_prototype_wikiset()
    eng = _wiki(ws, "eng-handbook")
    assert [p.title for p in eng.pages] == [
        "Onboarding",
        "Dev Environment",
        "Coding Standards",
        "Code Review",
        "Release Process",
        "Hotfix Runbook",
        "On-Call Guide",
        "Incident Retro Template",
    ]
    # "Onboarding" is the only root; "Code Review" nests under a depth-1 page.
    roots = eng.top_level_pages()
    assert [p.title for p in roots] == ["Onboarding"]
    onboarding = roots[0]
    code_review = eng.page_by_label("code-review")
    assert code_review.parent_uuid is not None
    assert code_review.parent_uuid != onboarding.uuid  # a grandchild (depth 2)


def test_exact_comment_counts_match_the_prototype():
    ws = build_prototype_wikiset()
    counts = {p.title: len(p.comments) for p in _wiki(ws, "eng-handbook").pages}
    assert counts == {
        "Onboarding": 12,
        "Dev Environment": 5,
        "Coding Standards": 23,
        "Code Review": 8,
        "Release Process": 14,
        "Hotfix Runbook": 3,
        "On-Call Guide": 31,
        "Incident Retro Template": 0,
    }


def test_comment_authors_are_the_prototype_pool_and_texts_are_written_per_page():
    """Comment text used to come from one ten-line pool shared with every
    other page and every blog post, so a page collected remarks about things
    it does not contain and repeated them every ten comments."""
    from connections_export.fakeserver.content import page_comment

    ws = build_prototype_wikiset()
    onboarding = _wiki(ws, "eng-handbook").page_by_label("onboarding")
    for i, comment in enumerate(onboarding.comments):
        assert comment.author in _AUTHORS
        assert html.escape(page_comment("Onboarding", i)[0]) in comment.content_html
    # 12 comments on this page, and no two of them are the same line.
    bodies = [c.content_html for c in onboarding.comments]
    assert len(set(bodies)) == len(bodies)


def test_pages_carry_body_prose_and_images_per_genassets():
    ws = build_prototype_wikiset()
    eng = _wiki(ws, "eng-handbook")
    for page in eng.pages:
        assert "authors-note" in page.body_html
        assert "wikiPage" in page.body_html
    # genAssets: gi % 4 == 3 -> no image. "Coding Standards" is gi=3.
    assert "<img" not in eng.page_by_label("coding-standards").body_html
    assert "<img" in eng.page_by_label("onboarding").body_html


def test_page_body_dark_theme_is_screen_only_so_pdfs_stay_light():
    """The reader shows the body dark (on screen); a printed PDF must stay
    light. So the dark background/text live only inside `@media screen` —
    never unconditionally, where Chromium's print media would pick them up."""
    ws = build_prototype_wikiset()
    body = _wiki(ws, "eng-handbook").page_by_label("onboarding").body_html

    assert "@media screen" in body
    style = body[body.index("<style>") : body.index("</style>")]
    outside_media = style[: style.index("@media screen")]
    # the dark page background + near-white text must not apply in print
    assert "#12141a" not in outside_media
    assert "#e7e9ee" not in outside_media


def test_pages_carry_distinctive_believable_prose_not_generic_filler():
    """Every page's body must read as its own topic, not the old boilerplate
    that made all 20 pages identical."""
    ws = build_prototype_wikiset()
    eng = _wiki(ws, "eng-handbook")
    ops = _wiki(ws, "ops-kb")

    onboarding = eng.page_by_label("onboarding").body_html
    dr = ops.page_by_label("dr-playbook").body_html

    # topic-true prose is present...
    assert "Welcome aboard" in onboarding
    assert "failover" in dr
    # ...and the generic filler is gone from every page.
    for wiki in ws.wikis:
        for page in wiki.pages:
            assert "migrated with its full revision history" not in page.body_html

    # two different pages no longer share the same body prose.
    coding = eng.page_by_label("coding-standards").body_html
    assert onboarding != coding


def test_page_bodies_are_deterministic_byte_for_byte():
    a = build_prototype_wikiset()
    b = build_prototype_wikiset()
    for wa, wb in zip(a.wikis, b.wikis, strict=True):
        for pa, pb in zip(wa.pages, wb.pages, strict=True):
            assert pa.body_html == pb.body_html


def test_genthumb_is_a_colourful_gradient_svg():
    svg = genthumb_svg(0)
    assert svg.startswith(b"<svg")
    assert b"linearGradient" in svg
    assert b"#0ea5a0" in svg  # palette 0


def test_deterministic():
    a = build_prototype_wikiset()
    b = build_prototype_wikiset()
    assert [(w.label, [p.title for p in w.pages]) for w in a.wikis] == [
        (w.label, [p.title for p in w.pages]) for w in b.wikis
    ]
    # Same uuids too (seeded id factory).
    assert a.wikis[0].pages[0].uuid == b.wikis[0].pages[0].uuid
