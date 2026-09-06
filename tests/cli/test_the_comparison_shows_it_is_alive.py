"""A long read says it is reading.

`compare-author` crawls a whole forum before it can compare anything, at
a request a second, and it works in a temporary directory it deletes --
so there is no archive growing on disk to watch either. With crawler
events discarded it produced no output at all until the table at the
end, and an hour of silence is indistinguishable from a hang.

That matters more than usual here: this is run by whoever has access to
the deployment, which is not the person who wrote it, and a run killed
at forty minutes teaches nobody anything.
"""

from __future__ import annotations

from connections_export.cli import _comparison_progress


# Named exactly as the real events are: the emitter recognises them by class
# name, so a stand-in called anything else tests nothing.
class Fetched:
    pass


class ForumTopicDerived:
    pass


class Warning:
    pass


def _emit_many(emit, event, count):
    for _ in range(count):
        emit(event)


def test_it_reports_without_a_line_per_request(capsys):
    """A line per request is thousands of lines, which hides progress as
    effectively as printing none."""
    emit = _comparison_progress()

    _emit_many(emit, Fetched(), 500)

    err = capsys.readouterr().err
    assert err, "a long crawl produced no sign of life"
    assert err.count("request(s)") < 20, "one line per request buries the progress"


def test_it_counts_requests_and_items_separately(capsys):
    """The two numbers answer different questions: whether it is moving, and
    whether it is finding anything."""
    emit = _comparison_progress()
    emit(Fetched())
    import time

    time.sleep(2.1)
    emit(ForumTopicDerived())

    err = capsys.readouterr().err
    assert "request(s)" in err and "item(s)" in err


def test_an_unrelated_event_does_not_count_as_progress(capsys):
    """Counting everything would report movement for a run doing nothing but
    logging warnings."""
    emit = _comparison_progress()

    _emit_many(emit, Warning(), 50)

    assert capsys.readouterr().err == ""
