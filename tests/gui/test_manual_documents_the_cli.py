"""The manual's command-line section names every command there is.

A reference page written once and never checked is a page that quietly starts
lying: a command added later is missing from it, and the omission looks
exactly like a command that does not exist. The CLI's own dispatch table is
the authority, so the manual is checked against that rather than against a
second list kept by hand.

Every command is documented, with no exceptions: the table holds only
commands a user can do something with. Deliberately directional all the same
-- the manual may say more than the table does (`connections-export` with no
arguments, for one, which is not a command).
"""

from __future__ import annotations

import pytest

from connections_export.cli import _SUBCOMMANDS
from tests.gui._served_assets import served_console_html

HTML = served_console_html()


def test_the_manual_has_a_command_line_section():
    assert 'id="man-cli"' in HTML
    assert '<a href="#man-cli">' in HTML, "not reachable from the manual's own contents"


@pytest.mark.parametrize("command", sorted(_SUBCOMMANDS))
def test_every_command_is_documented(command):
    assert f"<code>{command}</code>" in HTML, (
        f"`connections-export {command}` exists and the manual does not mention it"
    )


@pytest.mark.parametrize(
    "switch",
    [
        "--into",
        "--author",
        "--delay",
        "--component",
        "--since",
        "--recheck-comments",
        "--output-dir",
    ],
)
def test_the_switches_that_change_what_is_captured_are_documented(switch):
    """These decide what ends up in the archive. Someone running this
    unattended has no screen to read them off."""
    assert f"<code>{switch}</code>" in HTML, f"{switch} changes the capture and is undocumented"
