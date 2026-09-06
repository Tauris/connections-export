"""A log label does not turn an id into an address.

`shortLabel` formats URLs, and `new URL(value, location.href)` resolves
anything that is not one against the console's own origin -- so an
entity id passed to it becomes `http://127.0.0.1:8000/<id>`. That was
harmless while only the path was displayed and the invented host was
discarded unseen. Once the label began naming a host that is not the
deployment being read, every pruned item started reporting that it had
been fetched from the console itself.

Which is worse than noise: the host is shown precisely so that a request
answered by the wrong machine is noticeable, and a label that fabricates
one spends that signal on something that never happened.
"""

from __future__ import annotations

from tests.gui._served_assets import served_console_js


def _code_of(function: str) -> str:
    """A function's code, without its comments -- which discuss `new URL`
    and `location.href` and would otherwise answer for it."""
    js = served_console_js()
    start = js.index(f"function {function}")
    body = js[start : js.index("\n  }", start)]
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("//"))


def test_the_label_leaves_a_non_url_alone():
    label = _code_of("shortLabel")

    # The guard, not its spelling: something must return the value untouched
    # before anything resolves it against a base.
    guard = label[: label.index("const u = new URL")]

    assert "return String(url)" in guard, (
        "nothing returns a non-URL unchanged, so anything that is not an "
        "address is resolved against the console's own origin and acquires "
        "its host"
    )
    assert "location.href" not in label, (
        "the label still resolves against the console's own origin, which is "
        "where the invented host came from"
    )


def test_a_pruned_item_is_labelled_as_an_id_not_an_address():
    """`Pruned.id` is an entity id. Formatting it as a URL is what produced
    `127.0.0.1` beside every pruned item."""
    js = served_console_js()
    prune = js[js.index('logRow("PRUNE"') :][:200]

    assert "shortLabel(evt.id)" not in prune, "a pruned item's id is still being formatted as a URL"


def test_a_filtered_run_says_what_the_reading_is_for():
    """An author-filtered run must read every thread to find out whether you
    are in it, so it fetches steadily while the kept tree stands still. With
    nothing accounting for the difference the run reads as stalled at exactly
    the moment it is working hardest."""
    from tests.gui._served_assets import served_console_html

    html = served_console_html()
    js = served_console_js()

    assert 'id="kpi-pruned"' in html, "no live counter for what was read and not kept"
    assert 'id="k-pruned"' in html
    pruned = js[js.index("function livePruned") :][:600]
    assert "kpi-pruned" in pruned, "the counter is never revealed while the run is going"
