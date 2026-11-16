"""How much of a large PDF to lay out at once, from the memory this machine has.

A part size in megabytes is a number nobody can choose well, on a machine
whose free memory changes from one hour to the next. So it is worked out at
the start of each export from the memory available then. Measured: the
browser's peak while laying out a part is about 1.15 GB plus 2.2 times the
part's weight (its HTML plus its images as drawn). The setting exists for the
rare case where someone wants to force a size; empty means automatic.

No third-party package: the operating system is asked directly, and when it
cannot be asked the default size is used.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

#: Browser memory before any content (measured; rounded up).
BROWSER_BASE_BYTES = int(1.15 * 2**30)
#: Browser memory per byte of part weight (measured).
BYTES_PER_WEIGHT = 2.2
#: Share of the available memory an export may plan to use: the rest is for
#: everything else the person has open, and for the measurement being rough.
USABLE_SHARE = 0.6
MIN_PART_MB = 32.0
MAX_PART_MB = 512.0
#: Used when the available memory cannot be read.
FALLBACK_PART_MB = 256.0
#: Below this much available memory even the smallest part is a gamble.
LOW_MEMORY_BYTES = int(1.5 * 2**30)


def available_memory() -> int | None:
    """Bytes of memory available to start new work now, or None if the
    system will not say."""
    try:
        if sys.platform.startswith("linux"):
            return _linux()
        if sys.platform.startswith("win"):
            return _windows()
        if sys.platform == "darwin":
            return _macos()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def _linux() -> int | None:
    with open("/proc/meminfo", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    return None


def _windows() -> int | None:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    return int(status.ullAvailPhys)


def _macos() -> int | None:
    """Free + inactive pages: what macOS hands out without swapping."""
    out = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True).stdout
    page = 4096
    counts: dict[str, int] = {}
    for line in out.splitlines():
        if "page size of" in line:
            page = int(line.split("page size of")[1].split()[0])
        elif ":" in line:
            key, value = line.split(":", 1)
            value = value.strip().rstrip(".")
            if value.isdigit():
                counts[key.strip()] = int(value)
    pages = counts.get("Pages free", 0) + counts.get("Pages inactive", 0)
    return pages * page if pages else None


def automatic_part_mb(available: int | None = None) -> float:
    """The part size this machine can afford now: what fits in a share of the
    available memory after the browser's own, bounded to sensible limits."""
    if available is None:
        available = available_memory()
    if available is None:
        return FALLBACK_PART_MB
    usable = available * USABLE_SHARE - BROWSER_BASE_BYTES
    part = usable / BYTES_PER_WEIGHT / 2**20
    return max(MIN_PART_MB, min(MAX_PART_MB, part))


def free_disk(path: str | os.PathLike) -> int | None:
    """Bytes free on the disk holding `path` (or its nearest existing parent)."""
    import shutil  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None
