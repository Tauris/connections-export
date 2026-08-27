"""The browser's own startup noise is not our output.

Edge prints component-loader errors to stderr as it starts:

    ERROR:...oga_utils.cc:106] Edge LLM: Error getting component directory
    ERROR:...edge_presandbox_init.cc:19] Edge LLM Pre sandbox error:...

They are about an on-device model feature that has nothing to do with
rendering a PDF, the export works perfectly with them present, and they are
printed by a program we launched rather than by us. A user reading their
terminal cannot know any of that -- they see ERROR twice and reasonably
conclude the export failed.
"""

from __future__ import annotations

from connections_export.pdf.browser import _launch_args


def test_the_browser_is_told_to_keep_its_errors_to_itself():
    """`--log-level=3` is fatal-only: Chromium's own INFO/WARNING/ERROR
    chatter stops, while anything that actually kills the browser still
    reaches us."""
    assert "--log-level=3" in _launch_args()


def test_the_feature_that_produces_the_noise_is_switched_off():
    """Quieting the log hides the symptom. This removes the cause: nothing
    about rendering a page needs an on-device language model."""
    assert any("OptimizationGuideOnDeviceModel" in arg for arg in _launch_args())


def test_the_sandbox_flag_is_kept():
    """It is what lets the browser start at all in a container, and losing it
    while tidying the noise would trade a cosmetic problem for a real one."""
    assert "--no-sandbox" in _launch_args()
