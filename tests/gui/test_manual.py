"""The manual describes what the tool does now.

A manual is the one document people read instead of the code, so a claim it
keeps making after the behaviour changed is worse than a gap: they act on it.
Two of these existed -- linked Files documents are captured now and the manual
still said they never were, and the Obsidian exporter needs no extra in the
executable, where most non-developers meet it.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_html

HTML = served_console_html()
MANUAL = HTML[HTML.index('id="man-intro"') : HTML.index('id="panel-settings"')]


def test_the_exporters_need_no_extra():
    """`markdownify` is a base dependency now, so a plain install runs both
    exporters — the manual must not send people to an opt-in extra."""
    assert "no extra is required" in MANUAL
    assert "connections-export[obsidian]" not in MANUAL
    assert "connections-export[jekyll]" not in MANUAL


def test_the_manual_no_longer_says_a_linked_file_is_left_behind():
    """It is fetched now -- across communities, which is the case that
    motivated it."""
    assert (
        "an <code>&lt;a href&gt;</code> pointing at a document in Files is kept as a link"
        not in MANUAL
    )
    assert "A <strong>link to a document in Files</strong> is captured too" in MANUAL


def test_the_manual_states_the_two_limits_on_capturing_a_linked_file():
    """Both are things a reader would otherwise be surprised by: only a link
    naming the bytes is fetched, and it is fetched as them."""
    assert "names the document's <strong>bytes</strong>" in MANUAL
    assert "with your own credentials" in MANUAL


def test_the_manual_explains_multi_community_archives():
    assert 'id="man-communities"' in MANUAL
    assert "Several communities in one archive" in MANUAL
    assert 'href="#man-communities"' in HTML  # and is reachable from the contents


def test_it_says_why_one_archive_rather_than_several():
    """The reason is the whole design: cross-links resolve only inside one
    export."""
    assert "also in this export" in MANUAL
    assert "two archives that" in MANUAL


def test_it_covers_sub_communities_without_making_them_the_point():
    assert "sub-communities" in MANUAL
    assert "convenience, not the feature" in MANUAL


def test_it_says_what_the_user_can_see_and_what_it_costs():
    assert "requires membership to read" in MANUAL
    assert "five times the work" in MANUAL


def test_it_describes_reading_a_multi_community_archive():
    assert "under the community it came" in MANUAL
    assert "rename" in MANUAL


def test_the_manual_explains_dropping_an_archive_to_read_it():
    """The Archives screen lists a folder; an archive you were handed is not
    in it, and being told to file it first is being asked to do filing before
    reading."""
    assert 'id="man-open"' in MANUAL
    assert "drop it anywhere on the console" in MANUAL
    assert 'href="#man-open"' in HTML


def test_it_distinguishes_the_three_ways_one_arrives():
    assert "read <em>where it" in MANUAL  # a folder or zip on disk
    assert "no path at all" in MANUAL  # OneDrive/SharePoint
    assert "sign-in page" in MANUAL  # a link


def test_it_says_a_dropped_deployment_url_still_captures():
    assert "still starts a capture" in MANUAL


def test_the_manual_does_not_claim_a_blog_update_is_cheap_and_complete():
    """Blogs are the app where a comment moves nothing, so every captured
    post's comments are re-read on every update -- the opposite of the cheap
    case the manual grouped them into."""
    assert "Comments need a second question" in MANUAL
    assert "one request per blog" in MANUAL


def test_the_manual_says_which_apps_catch_a_comment_for_free():
    """Wikis and forums do, by the item's own date moving. Getting this
    backwards would send someone to an option they do not need, and leave
    them thinking blogs were covered when the code is what covers them."""
    assert "a comment moves the page's own date" in MANUAL
    assert "a reply moves its topic" in MANUAL
    assert "asked which <em>comments</em> changed as well" in MANUAL


def test_the_recheck_option_is_not_described_as_the_thing_that_closes_the_gap():
    assert "the option that closes it" not in MANUAL
    assert "for a deployment that behaves differently" in MANUAL


def test_the_manual_explains_the_external_images_choice():
    """Whether to reproduce a third-party image is the person's call; the
    manual must say the choice exists, what the PDF does either way, and that
    the tool does not judge the rights."""
    assert 'id="man-external-images"' in MANUAL
    assert "Include external images" in MANUAL
    assert "External content" in MANUAL
    assert "--no-external-images" in MANUAL
    assert "does not assess who holds the rights" in MANUAL
    assert "small image in running text" in MANUAL
    assert "Small external images" in MANUAL
    assert "pdf_small_image_px" in MANUAL


def test_the_manual_explains_how_page_content_is_written():
    """Each exporter asks how to write a page: the manual must name all four
    choices, which is whose default, that the mixed one checks rather than
    guesses, that raw is not cleaned and is the publisher's responsibility,
    and what a Hugo site needs before its HTML shows."""
    assert 'id="man-export-html"' in MANUAL
    for flag in ("--html markdown", "--html mixed", "--html html", "--html raw"):
        assert flag in MANUAL
    for label in ("Markdown with HTML where needed", "HTML as captured"):
        assert label in MANUAL
    assert "The default for <strong>Obsidian</strong>" in MANUAL
    assert "The default for <strong>Jekyll</strong> and <strong>Hugo</strong>" in MANUAL
    assert "shows <em>exactly</em> what the original did" in MANUAL
    assert "<strong>not cleaned</strong>" in MANUAL and "your responsibility" in MANUAL
    assert "markup.goldmark.renderer.unsafe = true" in MANUAL


def test_the_manual_puts_hugo_beside_obsidian_and_jekyll():
    assert 'id="man-export-hugo"' in MANUAL
    assert "connections-export ingest --format hugo" in MANUAL
    assert (
        "Obsidian, Jekyll and Hugo are <strong>independent, third-party applications</strong>"
        in (MANUAL)
    )
    assert "<strong>not an endorsement</strong>" in MANUAL
    assert "Export Hugo content" in MANUAL
