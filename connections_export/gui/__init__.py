"""`gui`: the live console on the real demo pipeline. `hcl-serve` serves a single-page front end,
an SSE
progress endpoint, and a `--demo` mode that runs the genuine crawler
against the in-process fakeserver and streams its real events.
"""

from connections_export.gui.app import make_app
from connections_export.gui.demo import DemoResult, run_demo

__all__ = ["DemoResult", "make_app", "run_demo"]
