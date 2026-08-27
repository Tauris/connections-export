"""Body links resolved to an absolute URL then classified
three ways -- in_export (resolves to an exported page), hcl_deployment
(points at the HCL system but isn't an exported page), external (the
public web). An absolute href to the deployment host must NOT be
treated as external merely for being absolute. The page matcher
itself is documented as heuristic."""

from connections_export.derive.links import build_page_lookup, resolve_body_links, resolve_link

BASE_URL = "https://fake"
PAGE_URL = "https://fake/wikis/basic/api/wiki/wiki0/page/p0/media"
P0_ENTRY = "https://fake/wikis/basic/api/wiki/wiki0/page/p0/entry"
P1_ENTRY = "https://fake/wikis/basic/api/wiki/wiki0/page/p1/entry"


def _lookup():
    return build_page_lookup(
        [
            ("p0-uuid", P0_ENTRY, "p0"),
            ("p1-uuid", P1_ENTRY, "p1"),
        ]
    )


def _resolve(href, **overrides):
    kwargs = dict(
        page_url=PAGE_URL,
        self_page_id="p0-uuid",
        base_url=BASE_URL,
        hcl_hosts=(),
        page_lookup=_lookup(),
    )
    kwargs.update(overrides)
    return resolve_link(href, **kwargs)


# --- (a) relative href to an exported page -> in_export -----------------


def test_relative_href_to_exported_page_resolves_in_export():
    link = _resolve("../p1/entry")

    assert link.scope == "in_export"
    assert link.target_page_id == "p1-uuid"
    assert link.resolved_url == P1_ENTRY
    assert link.original_href == "../p1/entry"


# --- (b) absolute URL to the deployment host resolving to a page --------


def test_absolute_url_to_deployment_host_resolving_to_a_page_is_in_export():
    # A full absolute URL, not a relative one -- must not be
    # misclassified as external just because it's absolute.
    link = _resolve(P1_ENTRY)

    assert link.scope == "in_export"
    assert link.target_page_id == "p1-uuid"
    assert link.resolved_url == P1_ENTRY


def test_absolute_url_by_path_heuristic_matches_a_non_entry_suffix():
    # Points at the page's /media (body) URL, not its /entry URL --
    # the wiki/page-label path heuristic still finds it.
    other_form = "https://fake/wikis/basic/api/wiki/wiki0/page/p1/media"

    link = _resolve(other_form)

    assert link.scope == "in_export"
    assert link.target_page_id == "p1-uuid"


# --- (c) absolute URL to deployment host NOT resolving to a page --------


def test_absolute_url_to_deployment_host_not_an_exported_page_is_hcl_deployment():
    files_doc = "https://fake/files/basic/api/library/lib1/document/doc1"

    link = _resolve(files_doc)

    assert link.scope == "hcl_deployment"
    assert link.target_page_id is None
    assert link.resolved_url == files_doc
    assert link.original_href == files_doc  # kept clickable, verbatim


def test_unexported_wiki_page_on_deployment_host_is_hcl_deployment():
    other_wiki_page = "https://fake/wikis/basic/api/wiki/wiki9/page/px/entry"

    link = _resolve(other_wiki_page)

    assert link.scope == "hcl_deployment"
    assert link.target_page_id is None


# --- (d) absolute URL to a genuinely external host -> external ----------


def test_absolute_url_to_external_host_is_external():
    link = _resolve("https://example.com/somewhere")

    assert link.scope == "external"
    assert link.target_page_id is None
    assert link.original_href == "https://example.com/somewhere"
    assert link.resolved_url == "https://example.com/somewhere"


# --- hcl_hosts extends the deployment boundary ---------------------------


def test_hcl_hosts_entry_is_deployment_scoped_not_external():
    files_on_other_host = "https://host/files/basic/api/library/lib1/document/doc1"

    link = _resolve(files_on_other_host, hcl_hosts=("host",))

    assert link.scope == "hcl_deployment"


# --- pure fragment resolves to the current page --------------------------


def test_pure_fragment_resolves_to_the_current_page():
    link = _resolve("#section-2")

    assert link.scope == "in_export"
    assert link.target_page_id == "p0-uuid"


# --- self link (absolute, to the page it appears on) ---------------------


def test_self_link_resolves_to_its_own_page_id():
    link = _resolve(P0_ENTRY)

    assert link.scope == "in_export"
    assert link.target_page_id == "p0-uuid"


# --- bare label / uuid heuristics -----------------------------------------


def test_bare_label_heuristic_matches_by_raw_href_text():
    # "p1" resolves (via urljoin against the page URL) onto the
    # deployment host but not onto any known page's *path* -- the raw
    # href is still checked against the label/uuid table directly.
    link = _resolve("p1")

    assert link.scope == "in_export"
    assert link.target_page_id == "p1-uuid"


# --- scanning + batch resolution ------------------------------------------


def test_resolve_body_links_scans_every_anchor_in_document_order():
    body = f'<div><a href="{P1_ENTRY}">see p1</a><a href="https://example.com">external</a></div>'

    links = resolve_body_links(
        body,
        page_url=PAGE_URL,
        self_page_id="p0-uuid",
        base_url=BASE_URL,
        hcl_hosts=(),
        page_lookup=_lookup(),
    )

    assert [link.scope for link in links] == ["in_export", "external"]
    assert links[0].target_page_id == "p1-uuid"


def test_resolve_body_links_on_empty_body_is_empty_list():
    assert (
        resolve_body_links(
            "",
            page_url=PAGE_URL,
            self_page_id="p0-uuid",
            base_url=BASE_URL,
            hcl_hosts=(),
            page_lookup=_lookup(),
        )
        == []
    )
