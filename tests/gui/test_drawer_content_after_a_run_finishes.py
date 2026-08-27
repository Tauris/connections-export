"""Clicking an ingested page after the run finished shows the page.

the import reported clean, and clicking a
wiki page in the ingest list said "This page's content is still being
ingested" and never showed anything.

Two faults, one on top of the other.

`REAL_MODEL` is a snapshot fetched during the run. The drawer paints from it
and re-fetches every 1.2s *while* `realModelState !== "complete"`. Once the
run completes, the polling stops -- so a page that was not yet in the last
snapshot taken is missing from it for good, and the drawer paints the
still-ingesting hint over a page that finished ingesting minutes ago.

And that hint is a statement about a run that is over. Whatever is true then,
"still being ingested" is not it.
"""

from __future__ import annotations

import re

from tests.gui._served_assets import served_console_js

JS = served_console_js()


def _loader() -> str:
    match = re.search(r"function loadLiveDrawerContent\(.*?\n  \}\n", JS, re.S)
    assert match, "loadLiveDrawerContent not found in console.js"
    return match.group(0)


def test_a_finished_run_still_refreshes_once_before_giving_up():
    """The snapshot that is missing the page is the one taken before the last
    derive. Asking again is the whole fix -- once, not in a loop."""
    body = _loader()
    assert "looked = false" in body, "nothing tracks whether the last look happened"
    assert "looked = true" in body
    assert re.search(r"!looked[\s\S]{0,300}refreshModelSnapshot\(paint\)", body), body


def test_the_two_states_do_not_say_the_same_thing():
    """ "Still being ingested" is a claim about a run in progress. Said after
    the run ends it is simply false, and it was the only thing said."""
    body = _loader()
    assert 'realModelState !== "complete"' in body
    spin = body.index("_spinHint(")
    assert body.index('realModelState !== "complete"') < spin, "the hint is not state-gated"
    assert "This run is finished" in body
    assert "was not captured" in body
