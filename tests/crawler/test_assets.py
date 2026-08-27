"""`assets.scan` (body-HTML image extraction) and
`assets.classify` (the same-deployment/external host boundary).
"""

from connections_export.crawler import assets

# --- 2.1 scan ---


def test_scan_extracts_same_host_and_cross_app_img_src():
    # Mirrors the fake's own body HTML shape:
    # a same-host wiki media image and a cross-app Files image.
    body = """
    <div class="lotusWiki wikiPage">
      <p>Body of p0 in wiki0.</p>
      <img src="/wikis/basic/api/wiki/wiki0/page/p0/media/img/diagram.png" alt="diagram" />
      <img src="/files/basic/api/library/lib1/document/doc1/media/logo.png" alt="logo" />
      <a href="https://example.com">external link</a>
    </div>
    """

    found = assets.scan(body)

    assert "/wikis/basic/api/wiki/wiki0/page/p0/media/img/diagram.png" in found
    assert "/files/basic/api/library/lib1/document/doc1/media/logo.png" in found


def test_scan_ignores_non_image_links():
    body = '<div><a href="https://example.com">external link</a></div>'

    found = assets.scan(body)

    assert found == []


def test_scan_empty_body_yields_no_assets():
    assert assets.scan("") == []
    assert assets.scan(b"") == []


def test_scan_accepts_bytes_body():
    body = b'<img src="/img/a.png" />'

    assert assets.scan(body) == ["/img/a.png"]


def test_scan_malformed_html_does_not_raise():
    assert assets.scan("<img src='/a.png'") == ["/a.png"] or isinstance(
        assets.scan("<not><valid"), list
    )


# --- 2.2 classify ---


def test_classify_base_host_is_same():
    result = assets.classify(
        "/wikis/basic/api/wiki/wiki0/page/p0/media/img/diagram.png",
        base_url="https://fake",
        hcl_hosts=[],
    )

    assert result == "same"


def test_classify_relative_url_resolves_against_base_url():
    result = assets.classify("relative/path.png", base_url="https://fake/some/dir/", hcl_hosts=[])

    assert result == "same"


def test_classify_configured_hcl_hosts_entry_is_same():
    # "host" -- like "fake" -- is an allow-listed, TLD-less placeholder
    # (tests/test_hygiene.py); stands in for a Files subdomain outside
    # the base host but still within the deployment.
    result = assets.classify("https://host/logo.png", base_url="https://fake", hcl_hosts=["host"])

    assert result == "same"


def test_classify_foreign_host_is_external():
    result = assets.classify("https://example.com/logo.png", base_url="https://fake", hcl_hosts=[])

    assert result == "external"


def test_classify_is_case_insensitive_on_host():
    result = assets.classify("https://FAKE/logo.png", base_url="https://fake", hcl_hosts=[])

    assert result == "same"


def test_resolve_joins_relative_url_against_base():
    resolved = assets.resolve("img/a.png", base_url="https://fake/wikis/basic/")

    assert resolved == "https://fake/wikis/basic/img/a.png"


def test_resolve_leaves_absolute_url_unchanged():
    resolved = assets.resolve("https://example.com/a.png", base_url="https://fake")

    assert resolved == "https://example.com/a.png"


# --- avatar exclusion (privacy: consent covers the source, not a copy) ---


def test_is_avatar_url_matches_profile_photo_endpoints():
    assert assets.is_avatar_url("/profiles/photo.do?userid=abc123")
    assert assets.is_avatar_url("https://fake/profiles/html/photoServlet?key=x")
    assert assets.is_avatar_url("https://fake/connections/opensocial/rest/people/@me/@avatar")
    assert assets.is_avatar_url("/files/app/userphoto/2.png")


def test_is_avatar_url_ignores_ordinary_content_images():
    assert not assets.is_avatar_url("/wikis/basic/api/wiki/w/page/p/media/diagram.png")
    assert not assets.is_avatar_url("https://fake/logo.png")
    assert not assets.is_avatar_url("")


def test_scan_excludes_avatar_images_but_keeps_content_images():
    body = (
        '<p>By <img src="/profiles/photo.do?userid=jdoe" class="author-photo"> Jane</p>'
        '<figure><img src="/wikis/w/page/p/media/diagram.png"></figure>'
    )

    srcs = assets.scan(body)

    assert srcs == ["/wikis/w/page/p/media/diagram.png"]  # avatar dropped, content kept


def test_strip_avatars_removes_only_avatar_imgs():
    body = (
        '<p>Hi <img src="https://fake/profiles/photo.do?userid=jdoe"> there</p>'
        '<img src="/media/chart.png" alt="chart">'
    )

    cleaned = assets.strip_avatars(body)

    assert "photo.do" not in cleaned
    assert "chart.png" in cleaned  # content image untouched
    assert "there" in cleaned  # surrounding text preserved


def test_strip_avatars_leaves_avatar_free_body_untouched():
    body = '<p>No avatars here <img src="/media/a.png"></p>'
    assert assets.strip_avatars(body) == body


def test_strip_avatars_degrades_gracefully_on_junk():
    assert assets.strip_avatars("") == ""
    assert assets.strip_avatars("<<<not html") == "<<<not html"
