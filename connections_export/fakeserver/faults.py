"""Deterministic fault injection: forced status codes, truncated
bodies, timeouts, malformed XML, wrong Content-Type, and probabilistic
5xx -- all seeded, never `random`-without-a-seed.

Faults are matched by exact request path (`request.url.path`, no query
string), per the "Forcing a server error on one URL... while
other paths are unaffected."
"""

import random
from dataclasses import dataclass, field


@dataclass
class Faults:
    status_for: dict[str, int] = field(default_factory=dict)
    truncate_paths: set[str] = field(default_factory=set)
    timeout_paths: set[str] = field(default_factory=set)
    malformed_paths: set[str] = field(default_factory=set)
    wrong_content_type: dict[str, str] = field(default_factory=dict)
    fail_probability: float = 0.0
    # Not in the illustrative sketch, but required to make
    # `fail_probability` reproducible under a fixed seed -- probabilistic
    # faults are reproducible -- independent of the dataset's own `SynthSeed`.
    seed: int = 0

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def forced_status(self, path: str) -> int | None:
        """The forced status for `path`, from either the exact-path
        table or (if none configured there) a probabilistic roll.
        Consumes exactly one RNG draw per call so a fixed seed replays
        identically."""
        if path in self.status_for:
            return self.status_for[path]
        if self.fail_probability > 0 and self._rng.random() < self.fail_probability:
            return 500
        return None
