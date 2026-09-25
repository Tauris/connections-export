from pathlib import Path

from tests.gui._served_assets import served_console_css, served_console_html, served_console_js

# The console's script lives in console.js, not in console.html; the
# router function lives there now, while DOM/markup assertions stay on the
# served HTML.
HTML = served_console_html()
JS = served_console_js()
CSS = served_console_css()
#: The pdf.js boot shim, read from the package: it is a vendored asset served
#: by name, not one of the three console files.
BOOT = (
    __import__("pathlib").Path(
        __import__("connections_export.gui.support", fromlist=["VENDOR_DIR"]).VENDOR_DIR
    )
    / "pdfjs-boot.mjs"
).read_text(encoding="utf-8")


def test_sidebar_has_all_sections():
    for sect in ["overview", "archives", "reader", "select", "ingest", "manual", "settings"]:
        assert f'data-section="{sect}"' in HTML


def test_sidebar_groups_present():
    assert "Browse" in HTML and "Capture" in HTML and "Help" in HTML


def test_has_router_and_default_overview():
    assert "function showSection(" in JS
    assert 'id="panel-overview"' in HTML


def test_showing_archives_refreshes_archive_list():
    # Showing Archives re-fetches the list so runs finished this session appear and archives
    # removed since page load stop being offered (a stale entry 404s on click
    # -- "I was not able to open the one archive it presented me").
    assert 'id === "archives" && typeof loadRecentArchives === "function"' in JS
    assert "loadRecentArchives()" in JS


def test_open_archive_refreshes_list_on_stale_entry():
    # A 404/not_found on open means the presented archive was removed; the
    # client refreshes the list rather than leaving the dead entry in place.
    assert 'r.status === 404 || j.status === "not_found"' in JS


def test_reader_body_internal_links_are_wired_for_navigation():
    # In-export links inside the sandboxed (script-less) body frame -- in prose
    # AND inside inline SVG -- must be made clickable by the parent so they
    # navigate the reader, not just the PDF ("the SVG link works in the PDF but
    # not in the reader").
    assert "readerNavLinks" in JS
    assert "function readerBody(" in JS
    assert "function navigateToPageId(" in JS
    assert 'f.closest("#r-article")' in JS  # wiring is reader-only, not the drawer


def test_overview_has_hero_and_ctas():
    assert 'id="panel-overview"' in HTML
    assert "Keep what your community built" in HTML
    # "Open the manual" went: it is in the sidebar, on every screen. What is
    # left is the two things you can do -- capture, or read -- plus the demo
    # beside them. `tests/gui/test_overview_actions.py` holds that shape.
    for bid in ["ov-start", "ov-demo", "ov-archives"]:
        assert f'id="{bid}"' in HTML


def test_overview_has_capability_cards_and_select_does_not():
    # The five capability cards (the "why" grid -- including the
    # highlighted "Your own contributions" wide card) moved from
    # Select & Tailor to the Overview, replacing its plainer four-card
    # `.ov-cards` grid. Select stays workflow-focused: URL input,
    # tailoring, size chooser, demo chips, output actions.
    overview_block = HTML.split('id="panel-overview"')[1].split('id="panel-select"')[0]
    assert "why-grid" in overview_block
    assert "Your own contributions, in context" in overview_block

    select_block = HTML.split('id="panel-select"')[1].split('id="panel-ingest"')[0]
    assert "why-grid" not in select_block


def test_ov_card_grid_is_gone():
    # `.ov-cards`/`.ov-card` are superseded by the why-grid cards now
    # shown on the Overview -- the old markup and its class names must
    # not linger anywhere in the served HTML.
    assert "ov-card" not in HTML


def test_sidebar_collapsible_and_run_status_present():
    # The console's script lives in console.js rather than in
    # console.js -- these functions live there now; the markup only carries
    # the DOM hooks they wire up.
    assert "function setSidebarCollapsed(" in JS
    assert "function updateRunStatus(" in JS
    assert 'id="sb-run-status"' in HTML and 'id="sb-collapse"' in HTML


def test_theme_toggle_lives_in_the_persistent_sidebar():
    # Injecting the theme toggle into the reader header (#r-top) from
    # initTheme would make it exist only once you had entered the Reader.
    # It ships as real markup inside #sidebar instead (which is visible
    # on every section) and initTheme wires the existing button instead
    # of creating and inserting a new one.
    import re

    sidebar_block = HTML.split('id="sidebar"')[1].split("</nav>")[0]
    assert 'id="theme-toggle"' in sidebar_block

    # Exactly one #theme-toggle in the whole document -- no reader-injected
    # duplicate left behind.
    assert HTML.count('id="theme-toggle"') == 1

    assert "function initTheme(" in JS
    init = re.search(r"function initTheme\(.*?\n  \}", JS, re.S)
    assert init, "initTheme() not found"
    assert 'getElementById("r-top")' not in init.group(0)
    assert 'getElementById("theme-toggle")' in init.group(0)


def test_sidebar_brand_is_a_clickable_home_link():
    # The sidebar's "export" brand entry is an actionable <button>, not an
    # inert <div>: it routes back to the Overview, same as clicking the
    # 🏠 Overview nav item.
    import re

    brand_tag = re.search(r'<button[^>]*class="sb-brand"[^>]*>', HTML)
    assert brand_tag, "sb-brand is not a <button>"
    assert 'id="sb-brand"' in brand_tag.group(0)

    assert 'getElementById("sb-brand")' in JS
    assert 'showSection("overview")' in JS
    # The click handler wires sb-brand directly to showSection("overview").
    wiring = re.search(r'getElementById\("sb-brand"\)[^\n]*showSection\("overview"\)', JS)
    assert wiring, 'sb-brand is not wired to showSection("overview")'


def test_size_chooser_defaults_to_10_and_start_is_explicit():
    # Ingest never auto-starts -- the Select & Tailor panel offers
    # an explicit All/Preview size chooser (stepper defaulting to 10), and
    # a run only ever begins from a deliberate click on #start-ingest.
    assert 'id="size-count"' in HTML
    assert 'value="10"' in HTML  # stepper default
    assert 'id="start-ingest"' in HTML
    # identify must not auto-start: no beginRun call inside identify handlers.
    # This function lives in console.js, so the search runs there.
    import re

    ident = re.search(r"function applyIdentifyResult\(.*?\n  \}", JS, re.S)
    assert ident and "beginRun(" not in ident.group(0)


def test_demo_chips_markup_present():
    # The Select & Tailor panel carries a hidden
    # demo-mode toggle and a hidden chip container -- #ov-demo (Overview)
    # already clicks #demo-mode to reveal them.
    assert 'id="demo-mode"' in HTML
    assert 'id="demo-chips"' in HTML


def test_demo_chips_hidden_until_chosen():
    # Fix #1: the demo affordance must not take space until the user
    # actually opts into demo mode. #demo-chips ships `hidden` in the
    # markup, and nothing in loadDemoChips (the /api/demo-urls callback,
    # which also runs on every boot to probe for demo mode) may clear that
    # attribute on its own -- only an explicit user action (#demo-mode's
    # own click, or "Try the demo") may open it. In a real deployment the
    # endpoint returns [], so #demo-mode itself also stays hidden and the
    # whole affordance takes zero space.
    import re

    tag = re.search(r'<div[^>]*id="demo-chips"[^>]*>', HTML)
    assert tag and "hidden" in tag.group(0)

    body = re.search(r"function loadDemoChips\(.*?\n  \}", JS, re.S)
    assert body, "loadDemoChips() not found"
    assert '"demo-chips"' not in body.group(0)


def test_demo_chips_fetch_sample_urls_from_the_server():
    # The chips' URLs come from the fakeserver via the server-side
    # helper, not a hardcoded client-side list.
    assert 'fetch("/api/demo-urls")' in JS


def test_demo_mode_toggle_revealed_only_when_urls_present():
    # loadDemoChips (the fetch("/api/demo-urls") callback) is also the
    # probe for "are we in demo mode at all": a non-empty list unhides the
    # #demo-mode toggle button itself -- never the chips container, which
    # stays collapsed until the user opts in (see
    # test_demo_chips_hidden_until_chosen).
    import re

    body = re.search(r"function loadDemoChips\(.*?\n  \}", JS, re.S)
    assert body, "loadDemoChips() not found"
    src = body.group(0)
    assert "urls.length" in src
    assert '"demo-mode"' in src
    assert ".hidden = false" in src


def test_demo_mode_button_toggles_chips_and_is_reused_by_try_the_demo():
    # Clicking #demo-mode opens/closes #demo-chips (lazy-loading the chips
    # via the existing loadDemoChips loader on first open); the
    # Overview's "Try the demo" CTA reuses that same open path rather than
    # a synthetic click that only works once #demo-mode happens to already
    # be unhidden.
    import re

    wire = re.search(r"function wireDemoChips\(.*?\n  \}", JS, re.S)
    assert wire, "wireDemoChips() not found"
    assert "chipsContainer.hidden" in wire.group(0)

    open_fn = re.search(r"function openDemoChips\(.*?\n  \}", JS, re.S)
    assert open_fn and "loadDemoChips()" in open_fn.group(0)

    overview = re.search(r"function wireOverview\(.*?\n  \}", JS, re.S)
    assert overview and "openDemoChips()" in overview.group(0)


def test_open_archive_wiring_present():
    # The Overview's "Open an existing archive" list (#ov-recent)
    # and the Reader's empty-state CTA both route through these two
    # functions -- loadRecentArchives fills the list from GET
    # /api/archives, openArchive POSTs /api/open-archive and enters the
    # real reader.
    assert "function loadRecentArchives(" in JS
    assert "function openArchive(" in JS
    assert '"/api/archives"' in JS
    assert '"/api/open-archive"' in JS


def test_open_archive_resets_reader_state_before_reentering():
    # Final-review finding 1: enterReaderReal short-circuits on a truthy
    # REAL_MODEL, so opening a second archive without clearing it first
    # would just redisplay the first archive's cached model. openArchive
    # must drop that cache (and the current selection / any in-flight
    # poll or live-refresh timers) before it re-enters the reader.
    import re

    body = re.search(r"function openArchive\(.*?\n  \}", JS, re.S)
    assert body, "openArchive() not found"
    src = body.group(0)
    assert "REAL_MODEL = null" in src
    assert "clearReaderSelection()" in src
    assert "stopRealPolling()" in src
    assert "stopLiveRefresh()" in src


def test_live_pdf_has_live_only_hint():
    # The Live Browser PDF button carries a persistent hint --
    # unlike the no_browser message, this renders regardless of Chromium
    # availability, warning that the PDF is live-only and can't be
    # recreated from an archive later.
    assert 'id="live-pdf-hint"' in HTML
    assert "can't be recreated" in HTML or "cannot be recreated" in HTML
    assert "live" in HTML.lower()


def test_export_scope_selector_is_present_and_shared():
    # #6: a scope dropdown next to Export PDF ("Whole archive" vs the open item).
    assert 'id="r-pdf-scope"' in HTML
    assert 'value="all"' in HTML and 'value="item"' in HTML
    # The archive export reads it and can send a scoped request.
    assert "scope=item" in JS
    assert "currentReaderScope" in JS
    # #4: the live path reads the SAME dropdown, not its own ad-hoc rule.
    assert 'sel.value === "item"' in JS


def test_live_pdf_opens_in_the_inline_viewer():
    # #5: the live PDF is shown in an inline viewer overlay (with its own
    # Download), not force-downloaded blindly.
    assert "showPdfViewer" in JS
    assert 'ov.id = "pdf-viewer"' in JS  # overlay is built in JS, not the shell HTML
    assert 'id="pv-frame"' in JS  # the <iframe> that renders the PDF


def test_live_pdf_preview_is_append_only_via_pdfjs():
    # The live preview appends per-entity tiles as pages arrive ("while it's
    # happening"), rendered by PDF.js -- it does NOT re-render the whole doc.
    assert 'id="live-pdf-preview"' in HTML
    assert "function startLivePdfPreview" in JS
    # Vendored PDF.js is loaded self-contained (no CDN) and drives the strip.
    # pdf.js is an ES module from v4 on, so it is imported by a boot shim
    # rather than loaded by a plain script tag, and the worker path is set
    # there -- console.js is a classic script and runs before it.
    assert 'src="vendor/pdfjs-boot.mjs"' in HTML
    assert "vendor/pdf.worker.min.mjs" in BOOT
    assert "pdfjsLib.getDocument" in JS
    # Pages accumulate one entity at a time (bare tiles), each rendered once and
    # appended to a scrollable strip -- so you can watch and abort early.
    assert "scope=tile" in JS and "chrome=0" in JS
    assert "lpSeen" in JS  # each entity rendered once
    assert "stopLivePdfPreview" in JS
    # A Stop button in the viewer aborts the render (the whole point).
    assert 'id="lp-stop"' in JS
    # Reachable from the reader too (export-review), not just the dashboard.
    assert 'id="r-preview"' in HTML


def test_author_sweep_is_described_as_a_plain_capability():
    # The author-selection feature is implemented and part of the tool, so it's
    # described plainly (like every other Overview card) -- no "planned" badge,
    # no release-timeline framing (the project has no prior release).
    # (Config editing, a separate, unbuilt feature, is still legitimately "planned".)
    assert "author sweep — planned" not in HTML
    assert "sweep is planned" not in HTML
    for phrase in ("now available", "shipped", "in, polishing"):
        assert phrase not in HTML
    # The Overview card and the manual both point at the real field.
    assert "Only content authored by" in HTML
    manual = HTML.split('id="panel-manual"')[1]
    assert "Only content authored by" in manual


def test_author_picker_lets_you_pick_the_exact_name():
    # Distinct authors an import saw become clickable chips that re-import
    # filtered to that exact stored name form (no guessing the format).
    assert 'id="author-picker"' in HTML
    assert "function renderAuthorPicker" in JS
    assert "function reimportByAuthor" in JS
    assert "author_filter_summary" in JS  # picker is driven by the summary event


def test_per_type_counts_and_honest_kept_total():
    # Items counted separately by type (live + verdict breakdown).
    assert 'id="type-counts"' in HTML and 'id="v-breakdown"' in HTML
    assert "renderTypeCounts" in JS and "modelTypeCounts" in JS
    assert "TYPE_LABELS" in JS
    # The verdict "Archived" total was misleading with a filter -> relabelled,
    # and a Pruned stat + kept breakdown make what-you-got explicit.
    assert "Archived" not in HTML  # relabelled to "Fetched (ok)"
    assert 'id="v-pruned"' in HTML


def test_email_to_userid_resolution_is_available():
    # Resolve a stable email to the stable snx:userid via Profiles, so the
    # filter matches the uuid, not the volatile display name.
    assert 'id="author-resolve"' in HTML
    assert "function resolveAuthorEmail" in JS
    assert "/api/resolve-user?email=" in JS
    # Result shows name/email/uuid + the queried URL; auth is on-demand (base_url
    # passed), so no prior ingest is needed.
    assert 'id="author-resolve-result"' in HTML
    assert "function renderResolveResult" in JS
    assert "function baseUrlParam" in JS


def test_search_preview_is_available():
    # An option to try the search-based selection (read-only) and see what it
    # would return, to compare with the name filter.
    assert 'id="author-search-preview"' in HTML
    assert "function previewSearchIngest" in JS
    assert "/api/search-preview?userid=" in JS
    assert "superset" in JS.lower()  # honest about author-OR-contributor-OR-member


def test_request_delay_is_configurable():
    # A politeness delay between requests, for slow/rate-limited deployments.
    assert 'id="field-delay"' in HTML
    assert "function requestDelaySeconds" in JS
    assert "min_interval" in JS


def test_search_driven_ingest_is_wired():
    # Ingest seeded from search hits: each forum hit → whole thread, each blog →
    # entries with comments. Server expands seeds; the button starts it.
    assert "function ingestSearchResults" in JS
    assert 'source: "search"' in JS
    assert 'id="sp-ingest"' in JS  # button in the search results overlay


def test_community_scoping_is_wired():
    # Experimental: pin the search to the owning community (discovered from the
    # dragged container) -- the only documented sub-component scoping.
    assert 'id="community-scope"' in HTML
    assert "function discoverCommunity" in JS
    assert "/api/community-of?app=" in JS
    assert "community_uuid=" in JS


def test_community_component_selection_is_wired():
    assert 'id="community-components"' in HTML
    assert "renderCommunityComponents" in JS
    assert "selectedCommunityComponents" in JS
    assert "community_components" in JS
    # The console reads the STREAM, which reports progress; the plain JSON
    # route stays for a person reading it in a browser.
    assert "/api/community-components/stream?community_uuid=" in JS
    assert "forum:" in JS
    assert "data.components" in JS
    assert "Select all" in JS
    assert "Clear all" in JS
    assert 'item.querySelector("input").checked = true' in JS
    assert "Selected community components" in JS
    # The label now names every kind the one field accepts, rather than
    # listing community separately.
    assert "forum, or community URL" in HTML
    assert "community UUID supported" in HTML


def test_community_forums_are_named_and_selectable():
    assert "Forum · " in JS
    assert "forum.title" in JS
    assert "selectedCommunityComponents" in JS
    # `kind:id`; the kind is escaped like the id, since discovery supplies it.
    assert "escapeHtml(component.kind) + ':'" in JS


def test_author_filter_defaults_to_no_filter_and_me_is_explicit():
    """Same intent, restated for the segmented control that replaced the
    free-text field plus its row of API-shaped buttons: Everyone is the
    default and pressed by default, and "Only me" is a deliberate choice."""
    assert 'id="author-mode-everyone" aria-pressed="true"' in HTML
    assert 'id="author-mode-me" aria-pressed="false"' in HTML
    assert 'id="author-mode-person" aria-pressed="false"' in HTML
    assert "resolveCurrentUser();" not in JS


def test_community_hides_single_app_mode_grid_and_reports_forum_discovery():
    assert 'modeGrid.hidden = app === "community"' in JS
    assert ".mode-grid[hidden] { display: none; }" in CSS
    assert "Reading community components" in JS
    assert "No named components were found" in JS
    assert "data.components" in JS
    assert 'item.querySelector("input").checked = true' in JS
    assert ".setup-form .field[hidden] { display: none; }" in CSS
    assert '$("size-count").value = "10"' in JS
    assert 'id="single-component-target"' in HTML
    assert 'targetPanel.hidden = app === "community"' in JS


def test_archive_open_keeps_title_and_shows_progress():
    assert 'id="archive-open-overlay"' in HTML
    assert 'id="archive-open-title"' in HTML
    assert "archive-open-progress" in CSS
    assert "archive-open-title" in JS
    assert "Reading the archive and preparing its model" in HTML


def test_a_note_is_not_counted_as_a_warning():
    """The alerts panel shows notes; the verdict does not count them.

    `placed_not_written` records a difference in the source -- an owner placed
    a rich content area and never typed in it. It has to stay visible, and it
    must not flip a clean run to "needs attention", inflate the Warnings tile,
    or badge a tree node with a severity it does not have.
    """
    assert 'placed_not_written: "note"' in JS
    assert 'placed_not_written: "Placed, never written in"' in JS
    assert 'if (kind === "note") notes.push' in JS
    assert "else warnings.push" in JS
    # The verdict still counts warnings, and notes are not among them.
    assert "const warnCount = warnings.length" in JS
    assert ".alert.note" in CSS


def test_live_topic_count_uses_derived_threads_not_topic_fetches():
    # Topics are counted from the event that announces a derived thread, never
    # from topic *fetches*. No fetch kind is counted anywhere, so the rule
    # needs no exception -- excluding one kind from a tally of all the others
    # would be the same rule stated as a special case.
    assert "kindCounts.topic = (kindCounts.topic || 0) + 1" in JS
    assert "evt.reply_count || 0" in JS
    assert "kindCounts[countKind]" not in JS, "a fetch-kind tally is back"


def test_live_topic_preview_carries_author_and_date():
    assert "author: evt.author" in JS
    assert "published: evt.published" in JS
    assert 'it.kind === "topic"' in JS


def test_live_wiki_and_blog_preview_carry_author_and_date():
    assert "author: evt.author" in JS
    assert "modified: evt.modified" in JS
    assert "published: evt.published" in JS
    assert 'section(it.kind === "page" ? "Page"' in JS


def test_ingest_breakdown_separates_topics_replies_and_assets():
    assert 'topic: "topics"' in JS
    assert 'reply: "replies"' in JS
    assert 'asset: "assets"' in JS
    # Attachments still count as assets. They are now deduplicated by URL:
    # the same asset fetched from two pages is one asset in the archive, which
    # is what the verdict counts.
    assert 'evt.kind === "asset" || evt.kind === "attachments"' in JS
    assert "assetUrls.add(evt.url)" in JS


def test_community_preview_limit_is_per_component():
    # The run bar splits the words: a "First N" segment, then "per
    # component" as the unit beside the stepper it qualifies.
    assert "per component" in HTML
    assert 'id="size-count"' in HTML
    run_module = Path(__file__).parents[2] / "connections_export" / "gui" / "routes" / "run.py"
    source = run_module.read_text(encoding="utf-8")
    assert "max_pages=max_entries" in source
    assert "max_posts=max_entries" in source
    assert "max_topics=max_entries" in source


def test_community_forum_crawl_is_started_before_slower_components():
    # Forums are the fastest component to produce visible results, so a
    # community capture starts them first and the tree fills early instead of
    # waiting out a wiki.
    #
    # The dispatch is shared with the CLI, so the ordering is data on the
    # registry rather than a hand-written branch in the console -- asserted
    # here as the property it
    # always was, rather than as a string that happened to encode it.
    from connections_export import apps

    order = [spec.kind for spec in sorted(apps.APPS, key=lambda s: s.crawl_order)]
    assert order[0] == "forum", f"forums no longer run first: {order}"
    assert order.index("forum") < order.index("wiki")
    assert order.index("wiki") < order.index("files")

    assert "topic_id=" in JS
    assert "/api/current-user" in JS
    assert 'id="author-me"' in HTML
    assert 'aria-pressed="false"' in HTML
    assert "author-selected" in CSS
    assert "resolveCurrentUser(true)" in JS
    assert "renderResolveResult(user, user)" in JS
    assert "payload.detail" in JS
    assert '"/api/current-user" + query' in JS
    # The deployment being asked about travels with the question. It is the
    # demo's own address when the demo is what is in view, because the server
    # decides which deployment to read from the address and nothing else.
    assert '"?base_url=" + encodeURIComponent(base || (demoInView()' in JS


def test_current_archive_is_shown_in_ingest_and_reader():
    # You should always be able to see which archive you're working with.
    assert 'id="ingest-archive"' in HTML  # badge in the ingest topbar
    assert "function refreshCurrentArchive" in JS
    assert "/api/current-archive" in JS
    assert "r-source" in JS  # reader header also updated


def test_failed_kpi_is_inspectable():
    # The user's "18 failed" was a dead number with no way to see what it was.
    # Failures accumulate and the Failed KPI opens a list of them.
    assert "function showFailures" in JS
    assert "failures.push" in JS
    assert "markFailKpiOpenable" in JS
    # Reset clears them so a fresh run doesn't inherit a stale list.
    assert "failures = []" in JS


def test_manual_panel_has_the_three_required_sections():
    # The Manual carries three sections: an intro, the archive/interchange
    # format (grounded in docs/reference/interchange-format.md), and
    # export/ingest (grounded in connections_export/ingest/ and the `ingest`
    # CLI subcommand).
    manual_block = HTML.split('id="panel-manual"')[1].split('id="panel-settings"')[0]
    assert "Coming soon" not in manual_block

    assert 'id="man-intro"' in manual_block
    assert "Select" in manual_block and "Ingest" in manual_block and "Reader" in manual_block

    assert 'id="man-format"' in manual_block
    assert "interchange.json" in manual_block
    assert "blobs" in manual_block.lower()
    assert "provenance" in manual_block.lower()

    assert 'id="man-export"' in manual_block
    assert "Obsidian" in manual_block
    assert "connections-export ingest --format obsidian" in manual_block
    assert "new exporter" in manual_block.lower()


def test_manual_has_the_css_fidelity_svg_diagram():
    # Task #22: a self-contained inline SVG explaining that system CSS only
    # ever combines with a page while the HCL system is online -- now
    # (ingest, author CSS only) vs. later (Reader/Portable PDF/re-home,
    # still no system CSS) vs. the one live-only exact path (Browser PDF).
    manual_block = HTML.split('id="panel-manual"')[1].split('id="panel-settings"')[0]
    assert "<svg" in manual_block

    # The "now vs. later vs. live" boxes the diagram lays out.
    for label in ["HCL system", "Archive", "Reader", "Portable PDF", "Re-home", "Live Browser PDF"]:
        assert label in manual_block

    # The fidelity vocabulary the reader/badges already use elsewhere.
    assert "author CSS" in manual_block
    assert "system CSS" in manual_block
    assert "approximated" in manual_block.lower()
    assert "live only" in manual_block.lower()


def test_manual_has_the_full_interchange_spec_expander():
    # Task #25: a lazy-loading expander that fetches GET
    # /api/manual/interchange and injects the rendered spec HTML -- never
    # inlined into console.html itself (kept out of the served page until
    # the user actually asks for it).
    manual_block = HTML.split('id="panel-manual"')[1].split('id="panel-settings"')[0]
    assert 'id="man-format-spec"' in manual_block
    assert "Full interchange format specification" in manual_block
    assert 'id="man-spec-toggle"' in manual_block
    assert 'id="man-spec-container"' in manual_block

    # The JS side: fetches the endpoint, guards file://, lazy-loads once.
    assert "/api/manual/interchange" in JS
    assert 'location.protocol === "file:"' in JS


def test_demo_chips_are_identify_only_never_a_run_trigger():
    # fillAndIdentify (the chips' click handler) exists and calls
    # identifyUrl -- and neither it nor the chip-wiring functions ever
    # call beginRun. #run-demo stays the only run trigger in demo mode.
    import re

    fill = re.search(r"function fillAndIdentify\(.*?\n  \}", JS, re.S)
    assert fill and "identifyUrl(" in fill.group(0) and "beginRun(" not in fill.group(0)

    render_chips = re.search(r"function renderDemoChips\(.*?\n  \}", JS, re.S)
    assert render_chips and "beginRun(" not in render_chips.group(0)

    wire_chips = re.search(r"function wireDemoChips\(.*?\n  \}", JS, re.S)
    assert wire_chips and "beginRun(" not in wire_chips.group(0)


def test_bulk_delete_client_streams_progress_instead_of_awaiting_one_json_body():
    """With 1200+ archives a blocking `r.json` shows nothing until every
    directory is gone. The client reads the NDJSON stream and reports
    progress as it goes."""
    assert "function deleteDemoArchives(" in JS
    assert "getReader()" in JS
    assert '"deleted"' in JS and '"done"' in JS
    assert "Deleting… " in JS


def test_bulk_delete_client_does_not_send_an_ignored_archive_name():
    """The handler never read `name`; sending a dummy one hid the fact that
    the shared request model required it."""
    bulk = JS.split("deleteDemoArchives", 1)[1]
    assert "JSON.stringify({ confirmation })" in bulk
    assert '"demo-archives"' not in JS


def test_archives_panel_offers_bulk_selection_with_a_count_confirmation():
    """Typing each archive's full name does not scale to the pile repeated
    attempts produce; a batch is confirmed by typing how many are selected."""
    assert 'id="archive-toolbar"' in HTML
    assert 'id="select-superseded"' in HTML
    assert 'id="delete-selected"' in HTML
    assert "function deleteArchives(" in JS
    assert "/api/delete-archives" in JS
    assert 'Type " + names.length + " to confirm' in JS


def test_bulk_selection_supports_shift_click_ranges():
    assert "event.shiftKey" in JS
    assert "lastCheckedIndex" in JS


def test_select_superseded_covers_every_listed_archive_not_only_rendered_rows():
    """With hundreds of archives only a page of rows renders; the helper must
    still reach the rest, which is the entire reason it exists."""
    assert "archivesOnPage.filter((a) => a.is_superseded)" in JS


def test_archive_rows_reuse_the_summary_already_returned_by_the_listing():
    """One request per row was a round trip per archive; /api/archives
    already carries each persisted summary."""
    assert "a.summary ? Promise.resolve(a.summary)" in JS


def test_native_controls_are_told_which_theme_they_are_in():
    """Checkboxes, selects and default scrollbars are painted by the browser
    and ignore custom properties entirely. Without `color-scheme` they stay
    light in dark mode -- the archive-list checkboxes being the symptom."""
    assert "color-scheme: dark;" in CSS
    assert "color-scheme: light;" in CSS
    # Every theme state must declare one, or the toggle leaves them stranded.
    assert CSS.count("color-scheme:") >= 4
    assert "accent-color:" in CSS


def test_the_console_says_who_it_is_acting_as():
    """Nothing in the shell stated the signed-in identity, so "me" existed
    only as a button label buried inside a form.

    It no longer says "Demo data -- not signed in": nobody signs in to this
    console, and it said "demo" whenever the SERVER was started with --demo,
    including while a real URL was being imported. The chip now carries a
    fact -- a resolved user, or demo data actually in view -- or it is hidden.
    `tests/gui/test_identity_chip.py` holds that rule."""
    assert 'id="sb-identity"' in HTML
    assert "Signed in as" in JS
    assert "Demo data" in JS


def test_only_me_does_not_silently_capture_everything_without_a_principal():
    """The demo has no authenticated user. Falling back quietly under an
    "Only me" label would capture everything and call it yours."""
    assert "No signed-in user to filter by" in JS


def test_the_id_shaped_controls_are_behind_an_advanced_disclosure():
    assert 'details class="advanced"' in HTML
    assert "<summary>Advanced</summary>" in HTML


def test_the_idle_ingest_screen_does_not_claim_to_be_crawling():
    """The pill was hard-coded `live`/"Crawling" in markup, so an idle screen
    showed a running crawl next to "Not started yet"."""
    assert (
        'id="statuspill"><span class="dot"></span><span id="statustext">Not started</span>' in HTML
    )
    assert '<div class="status-pill live" id="statuspill">' not in HTML


def test_the_url_field_is_the_only_way_in_and_a_real_field():
    """The input once lived inside a dashed drop rectangle, so the primary way
    in looked like decoration. Then the two sat side by side -- two
    affordances for one verb, one of them dead space whenever nobody was
    dragging. Now there is one control, and the whole page is the drop
    target."""
    assert 'id="identify-url"' in HTML
    assert 'for="setup-url-input"' in HTML
    assert 'id="dropzone"' not in HTML, "the dedicated drop rectangle is back"
    assert "drag a link or browser tab" in HTML, "the drop route must still be taught"


def test_step_two_has_an_empty_state_not_a_floating_glyph():
    assert 'class="tailor-guide empty-state"' in HTML
    assert "Nothing selected yet" in HTML


def test_destructive_actions_use_a_real_dialog_not_native_prompt():
    """`prompt` asked the user to transcribe a 40-character archive slug
    into unstyled OS chrome, with no view of what was about to be lost."""
    import re

    assert 'id="modal-scrim"' in HTML
    assert "function showDialog(" in JS
    # No real call sites left -- only the comments explaining the replacement.
    calls = [
        line
        for line in JS.splitlines()
        if re.search(r"(?<![\w.])(alert|prompt)\(", line) and not line.strip().startswith("//")
    ]
    assert not calls, f"native dialogs still in use: {calls[:3]}"


def test_a_delete_dialog_shows_what_is_at_stake():
    assert "This permanently deletes the selected archives" in JS
    assert "captured items. This cannot be undone." in JS
    assert "Real captures are not touched" in JS


def test_the_delete_button_is_not_permanently_shouting_on_every_row():
    """A red Delete on all 200 rows makes the most destructive action the most
    visible thing in the list."""
    assert ".recent-archive-row .archive-delete { opacity: 0;" in CSS
    assert ".recent-archive-row:focus-within .archive-delete" in CSS
    assert "@media (hover: none)" in CSS


def test_a_long_archive_list_can_be_filtered():
    assert 'id="archive-filter"' in HTML
    assert "function applyArchiveFilter(" in JS


def test_setup_is_one_column_because_the_task_is_linear():
    """It was pinned to 760px with vast gutters, then split into two columns.
    Both were wrong for the same reason: the task is strictly linear -- what,
    how much of it, go -- and the left column had no second act, because after
    identify the source is one line of facts rather than a card. Any 2-column
    split re-created the imbalance it was meant to fix."""
    assert ".setup-wrap { max-width: 760px" not in CSS
    assert "grid-template-columns: minmax(360px, 420px) minmax(0, 1fr)" not in CSS
    assert ".setup-grid { display: flex; flex-direction: column;" in CSS


def test_the_setup_header_does_not_eat_the_screen():
    """A centred logo, title and full tagline cost ~200px on a working
    screen and repeated what the Overview already says."""
    assert ".setup-head { display: flex;" in CSS
    assert ".setup-head { text-align: center;" not in CSS


def test_spacing_and_radius_follow_the_scales():
    """18 font sizes, 28 spacing values and 10 radii were the measurable form
    of "no design system". The scales exist as tokens; the stylesheet has to
    actually use them or they are decoration."""
    import re

    radii = set(re.findall(r"border-radius:\s*(\d+)px", CSS))
    assert radii <= {"999"}, f"hard-coded radii outside the scale: {sorted(radii)}"

    off_scale = set()
    for m in re.finditer(r"\b(padding|margin|gap|row-gap|column-gap)[a-z-]*:([^;{}]+);", CSS):
        if "(" in m.group(2):
            continue  # calc/clamp/color-mix arithmetic is not rhythm
        off_scale.update(int(v) for v in re.findall(r"\b(\d+)px\b", m.group(2)))
    # 1-3px are hairlines and optical insets, deliberately not on the scale.
    assert not {v for v in off_scale if v > 3}, f"off-scale spacing: {sorted(off_scale)}"


def test_dashed_borders_mean_only_drop_here():
    """Dashed was also on status chips, empty states and badges, which made
    finished UI read as unfinished.

    Enforced by MEANING rather than by counting to one: there are two drop
    affordances now -- the capture screen's veil and the Archives zone -- and
    a count would have made the second one a rule violation for doing exactly
    what the rule is for.
    """
    import re

    drop_selectors = ("drop-armed", "archive-drop", "dropzone")
    # Comments stripped first: one rule explains in prose that it is "solid,
    # not dashed", and matching that would report the rule for stating it.
    without_comments = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    offenders = []
    for block in re.findall(r"([^{}]+)\{([^{}]*)\}", without_comments):
        selector, body = block[0].strip(), block[1]
        if "dashed" not in body:
            continue
        if not any(name in selector for name in drop_selectors):
            offenders.append(selector)
    assert not offenders, f"dashed used outside a drop affordance: {offenders}"


def test_each_surface_has_one_name():
    """The same surface was "Ingest" in the nav, "Export — Live Console" in
    its own header, and the reader offered "Back to console" — a place that
    exists nowhere in the navigation."""
    assert "Export — Live Console" not in HTML
    assert "Back to console" not in HTML
    assert "Back to Ingest" in HTML


def test_the_ui_does_not_speak_in_config_identifiers():
    """Option VALUES are the config contract and must not change; the LABELS
    are what a person reads, and they were the raw identifiers.

    Checked as labels rather than as text anywhere in the file. The manual's
    command-line reference names `--auth-mode paste_token`, because that is
    the value you type -- and a rule that forbade the word outright would make
    the manual wrong in order to keep the picker right.
    """
    import re

    assert '<option value="sspi">Windows sign-in (SSPI)</option>' in HTML
    assert '<option value="paste_token">Paste a token</option>' in HTML

    labels = {label.strip() for label in re.findall(r"<option[^>]*>([^<]*)</option>", HTML)}
    # `kerberos` is deliberately absent from this list. The rule is that nobody
    # is shown a token they would never say out loud -- and people do say
    # Kerberos; it is the name of the thing, which happens to coincide with the
    # identifier. "paste_token" is nobody's word for anything.
    for identifier in ("sspi", "basic", "paste_token", "frontier", "backoff", "rolling"):
        assert identifier not in {label.lower() for label in labels}, (
            f"an identifier is being shown to users as a label: {identifier}"
        )


def test_timestamps_are_readable_with_the_exact_value_on_hover():
    assert "function whenText(" in JS
    assert "toLocaleDateString" in JS


def test_reader_header_gives_the_long_things_their_own_row():
    """Title, archive name, search, a badge, a checkbox, a select and four
    buttons shared one flex row: the long items wrapped and took the width
    from search."""
    assert 'class="r-ident"' in HTML
    assert 'class="r-actions"' in HTML
    assert ".r-actions .r-search { flex: 1 1 auto;" in CSS


def test_export_settings_live_in_the_export_menu():
    """The scope select and the comments checkbox each spent a slot in the
    header on something that only matters at the moment of exporting."""
    assert 'id="r-export-menu"' in HTML
    for control in ('id="r-pdf-scope"', 'id="r-pdf-comments"', 'id="r-preview"', 'id="r-live-pdf"'):
        assert control in HTML
    menu = HTML.split('id="r-export-menu"', 1)[1].split("</details>", 1)[0]
    for control in ("r-pdf-scope", "r-pdf-comments", "r-preview", "r-pdf", "r-live-pdf"):
        assert control in menu, f"{control} should sit inside the export menu"


def test_nothing_in_the_reader_header_claims_to_be_the_primary_action():
    """Export PDF was the only dark-green control in the reader. The point of
    the reader is reading."""
    header = HTML.split('id="r-top"', 1)[1].split('<div class="r-body">', 1)[0]
    assert "btn primary" not in header


def test_the_way_out_of_a_long_hierarchy_stays_in_view():
    """Collapsing a section meant scrolling back up past everything in it."""
    assert ".r-nav .r-component-section { position: sticky;" in CSS
    assert ".r-nav .r-container-section { position: sticky;" in CSS


def test_identity_is_refreshed_when_a_deployment_becomes_known():
    """It resolved once at boot, but the server only learns which deployment
    it is talking to when a real run starts -- so the shell could only ever
    say "not signed in", including while reading a real capture."""
    assert JS.count("loadShellIdentity()") >= 3


def test_start_is_held_while_a_url_is_still_being_analysed():
    """Reading a community's components takes real time against a real
    deployment; a run started meanwhile captures only what had arrived."""
    assert 'id="analysis-note"' in HTML
    assert "function setAnalysing(" in JS
    assert 'setAnalysing(true, "Identifying this URL")' in JS


def test_scrolling_a_long_list_says_which_month_you_are_in():
    """Orientation is continuous and passive: the month markers ride along
    with the list and stick under the container header, so the answer is on
    screen while scrolling rather than something to go and ask for."""
    assert "function addReaderDateMarkers(" in JS
    assert "addReaderDateMarkers(group, blogEntries)" in JS
    assert "addReaderDateMarkers(group, forumEntries)" in JS
    assert ".r-datemark { position: sticky;" in CSS


def test_narrowing_by_date_is_opt_in_behind_a_filter_button():
    """Narrowing is occasional and deliberate, so it does not take a block of
    the nav from every blog and forum permanently."""
    assert 'wrap.className = "r-daterange";\n    wrap.hidden = true;' in JS
    assert 'btn.className = "rd-toggle"' in JS
    assert "#i-filter" in JS or "i-filter" in HTML
    # Toggling the filter must not collapse the container it sits on.
    assert "event.stopPropagation();  // never collapse the container" in JS


def test_the_date_range_is_not_offered_where_it_cannot_help():
    """A handful of entries is already legible, and entries that all landed
    on one day have no range to move through."""
    assert "DATE_FILTER_MIN_ENTRIES" in JS
    assert "if (hi - lo < DAY) return;" in JS


def test_a_failed_lookup_leads_with_what_happened_not_the_endpoint():
    """A failure was rendered as a diagnostic dump — a "queried" row with the
    raw endpoint and a "status" row carrying whatever the transport said. That
    is how "SspiAuth requires the 'requests-negotiate-sspi' package" ended up
    in front of someone who had pressed a button labelled "Only me"."""
    assert "rows.push('<div class=\"rr-msg\">'" in JS
    assert '<details class="rr-tech"><summary>Technical detail</summary>' in JS
    assert '<span class="rr-k">status</span>' not in JS


def test_export_can_choose_components_and_wiki_sections():
    """ "Whole archive" or "the item open in the reader" were the only
    options, so exporting two of five forums meant exporting all five."""
    assert '<option value="choose">Choose components…</option>' in HTML
    assert 'id="pdf-pick-tree"' in HTML
    assert "function renderPdfPicker(" in JS
    assert "function pdfIncludeParams(" in JS
    # A wiki page selects the page AND everything under it -- picking a
    # section should not mean picking its children one at a time.
    assert '"subtree:" + pid' in JS


def test_choosing_nothing_to_export_is_refused_rather_than_exporting_all():
    assert 'notify("Tick at least one component to export."' in JS


def test_a_typed_deployment_address_is_chosen_through_a_post():
    """Lookups sign in only to deployments the user chose, and only a POST
    can choose one -- so the setup screen's typed address has to be posted,
    or every lookup against it is refused."""
    assert '"/api/choose-deployment"' in JS
    assert "chooseDeployment(" in JS


def test_the_update_screen_offers_to_choose_the_archives_host():
    """An archive from a deployment not yet chosen must not dead-end: the
    screen names the host and offers to use it."""
    assert "data.untrusted_host" in JS
    assert 'id="ledger-trust-host"' in JS


def test_a_pdf_render_shows_its_progress_where_it_can_be_seen():
    """Export PDF closes its menu, which hid the button's "Rendering…" for the
    whole render: a busy machine and nothing on screen. The toolbar button
    carries the progress and the time so far, and the end is announced."""
    assert 'querySelector("#r-export-menu > summary")' in JS
    assert '"⏳ Rendering PDF… "' in JS
    assert "clearInterval(ticker)" in JS
    assert '"PDF ready"' in JS
