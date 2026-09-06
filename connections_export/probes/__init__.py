"""Questions about a deployment that cannot be answered offline.

Everything else in this project is built and tested against the fake server,
which serves our documented UNDERSTANDING of Connections. That works until the
question IS whether the understanding is right -- and a few are, each one
sitting in `docs/reference/the open questions` or the architecture document's
"Open" list, blocking a decision that has to be made conservatively until
somebody with a real deployment spends two minutes on it.

A probe asks exactly one such question, reads nothing else, writes nothing at
all, and answers with a verdict the doc can be updated from.
"""

from connections_export.probes.files_since import (
    FilesSinceVerdict,
    interpret_files_since,
    plan_files_since_probe,
    probe_files_since,
)
from connections_export.probes.search_reach import (
    SearchReachVerdict,
    probe_search_reach,
)

__all__ = [
    "FilesSinceVerdict",
    "SearchReachVerdict",
    "interpret_files_since",
    "plan_files_since_probe",
    "probe_files_since",
    "probe_search_reach",
]
