"""Body HTML carries the embedded-asset, link, and CSS
fixtures the design requires -- and never a `<script>`.
"""

from connections_export.fakeserver.synth import SynthSeed, synthesize


def _body():
    seed = SynthSeed(seed=31, wiki_count=1, depth=1, pages_per_level=2)
    wikiset = synthesize(seed)
    page = wikiset.wikis[0].top_level_pages()[0]
    return page.body_html


def test_body_contains_same_host_and_cross_app_images():
    body = _body()
    assert 'src="/wikis/basic/api/wiki/' in body
    assert "/media/img/" in body
    assert 'src="/files/' in body


def test_body_contains_internal_and_external_links():
    body = _body()
    assert '<a href="/wikis/basic/api/wiki/' in body
    assert '<a href="https://example.com"' in body


def test_body_contains_author_style_block_and_style_attribute():
    body = _body()
    assert "<style>" in body
    assert 'style="' in body


def test_body_contains_hcl_platform_classes():
    body = _body()
    assert "lotusWiki" in body


def test_body_never_contains_script_tag():
    seed = SynthSeed(seed=32, wiki_count=1, depth=2, pages_per_level=2, comments_per_page=1)
    wikiset = synthesize(seed)
    for wiki in wikiset.wikis:
        for page in wiki.pages:
            assert "<script" not in page.body_html.lower()
