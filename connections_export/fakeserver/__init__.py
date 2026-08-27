"""fakeserver: an in-memory synthetic HCL Connections Wikis API,
serving our documented understanding of the reference at the real URL
shapes -- offline, deterministic, and fault-injectable.

See `the internal specifications` for the shapes and
the published API reference for what they're transcribed from.
"""

from connections_export.fakeserver.app import make_app
from connections_export.fakeserver.faults import Faults
from connections_export.fakeserver.model import BlogSet, ForumSet, WikiSet
from connections_export.fakeserver.synth import (
    SynthSeed,
    synthesize,
    synthesize_blogs,
    synthesize_forums,
)

__all__ = [
    "BlogSet",
    "Faults",
    "ForumSet",
    "SynthSeed",
    "WikiSet",
    "make_app",
    "synthesize",
    "synthesize_blogs",
    "synthesize_forums",
]
