"""The FastAPI app routes the documented endpoints, under a
configurable auth root, with correct shapes/status, category defaults,
and pagination -- driven in-process via `httpx.ASGITransport` (no real
socket), per the testability section.
"""

from xml.etree import ElementTree as ET

from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.synth import SynthSeed, synthesize

from .conftest import get

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "td": "urn:ibm.com/td",
}


def _app_and_wiki(auth_root="basic", **seed_kwargs):
    seed = SynthSeed(seed=41, wiki_count=1, depth=2, pages_per_level=2, comments_per_page=3)
    for k, v in seed_kwargs.items():
        setattr(seed, k, v)
    wikiset = synthesize(seed)
    app = make_app(wikiset, auth_root=auth_root)
    return app, wikiset.wikis[0]


def test_wikis_feed_endpoint():
    app, _ = _app_and_wiki()
    r = get(app, "/wikis/basic/api/wikis/feed")
    assert r.status_code == 200
    assert "atom+xml" in r.headers["content-type"]
    root = ET.fromstring(r.content)
    assert root.tag == "{http://www.w3.org/2005/Atom}feed"


def test_wiki_pages_feed_endpoint():
    app, wiki = _app_and_wiki()
    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/feed")
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert len(root.findall("atom:entry", NS)) == len(wiki.pages)


def test_page_entry_endpoint():
    app, wiki = _app_and_wiki()
    page = wiki.top_level_pages()[0]
    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/entry")
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert root.find("td:label", NS).text == page.label
    assert root.find("td:versionLabel", NS).text == str(page.version_label)


def test_navigation_entry_endpoint():
    app, wiki = _app_and_wiki()
    page = wiki.top_level_pages()[0]
    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/navigation/{page.label}/entry")
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert root.find("td:uuid", NS).text == page.uuid


def test_nav_feed_endpoint_is_json():
    app, wiki = _app_and_wiki()
    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/nav/feed", params={"tree": "true"})
    assert r.status_code == 200
    assert "json" in r.headers["content-type"]
    data = r.json()
    # real 8.0 shape: flat items with `parElem`, no synthetic `tree` root
    assert data["items"] and all("parElem" in item for item in data["items"])
    assert all(item.get("id") != "tree" for item in data["items"])


def test_media_endpoint_serves_body_html():
    app, wiki = _app_and_wiki()
    page = wiki.top_level_pages()[0]
    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/media")
    assert r.status_code == 200
    assert r.text == page.body_html


def test_auth_root_is_configurable():
    app, wiki = _app_and_wiki(auth_root="basic/anonymous")
    r = get(app, "/wikis/basic/anonymous/api/wikis/feed")
    assert r.status_code == 200
    # The un-configured root does not exist on this app instance.
    r2 = get(app, "/wikis/basic/api/wikis/feed")
    assert r2.status_code == 404


def test_artifacts_feed_default_category_is_comments():
    app, wiki = _app_and_wiki()
    page = wiki.top_level_pages()[0]
    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/feed")
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert len(root.findall("atom:entry", NS)) == len(page.comments)


def test_artifacts_feed_category_version():
    app, wiki = _app_and_wiki()
    page = wiki.top_level_pages()[0]
    r = get(
        app,
        f"/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/feed",
        params={"category": "version"},
    )
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert len(root.findall("atom:entry", NS)) == len(page.versions)
    for entry in root.findall("atom:entry", NS):
        assert entry.find("td:versionLabel", NS) is not None


def test_pagination_ps_and_page_slice_the_feed():
    app, wiki = _app_and_wiki(depth=1, pages_per_level=5)
    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/feed", params={"ps": 2, "page": 1})
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert len(root.findall("atom:entry", NS)) == 2


def test_legacy_pagesize_param_accepted():
    app, wiki = _app_and_wiki(depth=1, pages_per_level=5)
    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/feed", params={"pageSize": 3, "page": 1})
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert len(root.findall("atom:entry", NS)) == 3


# --- 2.1 default_page_size ---


def test_default_page_size_applies_when_ps_is_omitted():
    seed = SynthSeed(seed=41, wiki_count=1, depth=1, pages_per_level=5)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    app = make_app(wikiset, default_page_size=2)

    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/feed")

    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert len(root.findall("atom:entry", NS)) == 2
    rels = {link.get("rel") for link in root.findall("atom:link", NS)}
    assert "next" in rels


def test_default_page_size_none_preserves_current_behavior():
    seed = SynthSeed(seed=41, wiki_count=1, depth=1, pages_per_level=5)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    app = make_app(wikiset)  # default_page_size defaults to None

    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/feed")

    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert len(root.findall("atom:entry", NS)) == 5
    rels = {link.get("rel") for link in root.findall("atom:link", NS)}
    assert "next" not in rels


def test_explicit_ps_overrides_default_page_size():
    seed = SynthSeed(seed=41, wiki_count=1, depth=1, pages_per_level=5)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    app = make_app(wikiset, default_page_size=2)

    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/feed", params={"ps": 5})

    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert len(root.findall("atom:entry", NS)) == 5


def test_default_page_size_applies_to_wikis_feed():
    seed = SynthSeed(seed=41, wiki_count=5, depth=1, pages_per_level=1)
    wikiset = synthesize(seed)
    app = make_app(wikiset, default_page_size=2)

    r = get(app, "/wikis/basic/api/wikis/feed")

    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert len(root.findall("atom:entry", NS)) == 2
    rels = {link.get("rel") for link in root.findall("atom:link", NS)}
    assert "next" in rels


def test_default_page_size_applies_to_artifacts_feed():
    seed = SynthSeed(seed=41, wiki_count=1, depth=1, pages_per_level=1, comments_per_page=5)
    wikiset = synthesize(seed)
    wiki = wikiset.wikis[0]
    page = wiki.top_level_pages()[0]
    app = make_app(wikiset, default_page_size=2)

    r = get(app, f"/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/feed")

    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert len(root.findall("atom:entry", NS)) == 2
    rels = {link.get("rel") for link in root.findall("atom:link", NS)}
    assert "next" in rels


def test_same_host_and_cross_app_image_bytes_served():
    app, wiki = _app_and_wiki()
    page = wiki.top_level_pages()[0]
    r = get(
        app,
        f"/wikis/basic/api/wiki/{wiki.label}/page/{page.label}/media/img/diagram.png",
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 0

    r2 = get(app, "/files/basic/api/library/lib1/document/doc1/media/logo.png")
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("image/")
    assert len(r2.content) > 0


def test_unknown_wiki_is_404():
    app, _ = _app_and_wiki()
    r = get(app, "/wikis/basic/api/wiki/nope/feed")
    assert r.status_code == 404
