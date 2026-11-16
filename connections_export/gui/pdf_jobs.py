"""PDF exports that run in the background, so the console can show progress.

A large export takes minutes. One blocking request shows nothing for all of
them, so the console starts a job, polls it, and downloads the finished file.
The renderer reports each step through a progress callback; the job keeps the
latest report and, when cancelled, makes the next report raise
`PdfExportCancelled` -- which unwinds the render from inside, cleanly.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

#: Finished jobs (and their PDF bytes) are dropped after this long.
JOB_KEEP_SECONDS = 3600.0


class PdfExportCancelled(Exception):
    """Raised from the progress callback once the person cancelled."""


class PdfJobFailed(Exception):
    """A render failed; the message is the one for the person."""

    def __init__(self, detail: str, *, out_of_memory: bool = False) -> None:
        super().__init__(detail)
        self.detail = detail
        self.out_of_memory = out_of_memory


@dataclass
class PdfJob:
    id: str
    #: What is being exported, in words ("Whole archive -- several PDFs").
    title: str = ""
    #: The archive it came from.
    archive: str = ""
    #: Wall-clock start, for "started 14:32" after a reload.
    created_at: float = field(default_factory=time.time)
    started: float = field(default_factory=time.monotonic)
    ended: float | None = None
    state: str = "running"  # running | done | failed | cancelled
    progress: dict | None = None
    detail: str | None = None
    out_of_memory: bool = False
    #: What the export made, on disk (several, for a set of PDFs).
    files: list = field(default_factory=list)
    #: The one file to download: the PDF, or the ZIP of a set.
    download: object = None
    #: The folder the files were written to.
    folder: object = None
    cancel_requested: bool = False
    #: The worker thread has returned. A cancelled job still occupies the
    #: renderer until then, so it still counts as running for the one-at-a-time rule.
    finished: bool = False

    def report(self, progress) -> None:
        """The progress callback handed to the renderer."""
        if self.cancel_requested:
            raise PdfExportCancelled("The PDF export was cancelled.")
        self.progress = progress.as_dict()

    def as_dict(self) -> dict:
        end = self.ended if self.ended is not None else time.monotonic()
        return {
            "id": self.id,
            "title": self.title,
            "archive": self.archive,
            "created_at": self.created_at,
            "state": self.state,
            "progress": self.progress,
            "elapsed_s": round(end - self.started, 1),
            "detail": self.detail,
            "out_of_memory": self.out_of_memory,
            "files": [
                {"name": f.name, "size": f.stat().st_size} for f in self.files if f.is_file()
            ],
            "folder": str(self.folder) if self.folder else None,
        }


class PdfJobs:
    """The jobs of one server. One render runs at a time."""

    def __init__(self) -> None:
        self._jobs: dict[str, PdfJob] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> PdfJob | None:
        return self._jobs.get(job_id)

    def recent(self) -> list[PdfJob]:
        """Every job this server still remembers, newest first."""
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def start(self, work, *, title: str = "", archive: str = "") -> PdfJob | None:
        """Run `work(job)` on a thread, or None when another render is still
        running. `work` writes its files and records them on the job
        (`files`, `download`, `folder`)."""
        with self._lock:
            now = time.monotonic()
            for key in [
                k
                for k, j in self._jobs.items()
                if j.finished and j.ended is not None and now - j.ended > JOB_KEEP_SECONDS
            ]:
                del self._jobs[key]
            if any(not j.finished for j in self._jobs.values()):
                return None
            job = PdfJob(id=uuid.uuid4().hex, title=title, archive=archive)
            self._jobs[job.id] = job

        def run() -> None:
            try:
                work(job)
                job.state = "cancelled" if job.cancel_requested else "done"
            except PdfExportCancelled:
                job.state = "cancelled"
            except Exception as exc:  # noqa: BLE001 - reported to the person below
                job.state = "cancelled" if job.cancel_requested else "failed"
                if job.state == "failed":
                    if isinstance(exc, PdfJobFailed):
                        job.detail = exc.detail
                        job.out_of_memory = exc.out_of_memory
                    else:
                        job.detail = f"PDF rendering failed ({type(exc).__name__}). {exc}".strip()
            finally:
                job.ended = time.monotonic()
                job.finished = True

        threading.Thread(target=run, name=f"pdf-job-{job.id[:6]}", daemon=True).start()
        return job

    def cancel(self, job: PdfJob) -> None:
        if job.finished:
            return
        job.cancel_requested = True
        job.state = "cancelled"
