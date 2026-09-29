"""Fail if a macOS executable quietly requires a newer macOS than it should.

Every native part of a Mac executable -- the bootloader, Python, each
compiled extension and library -- declares the oldest macOS it runs on.
Nothing is compiled on the Mac runners, so those declarations come from the
ready-built packages; a dependency that starts shipping wheels only for a new
macOS makes the whole executable require it, and nobody notices until someone
on an older Mac cannot start it. (The Linux executable had exactly that
failure with glibc.) This reads the declarations out of the finished
executable, reports the highest, and fails over a limit.

    python tools/check_macos_minimum.py dist/connections-export --max 11.0

Reads Mach-O headers directly, so it runs -- and is tested -- anywhere.
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections.abc import Iterable
from pathlib import Path

_THIN_64 = 0xFEEDFACF
_THIN_32 = 0xFEEDFACE
_FAT = 0xCAFEBABE
_FAT_64 = 0xCAFEBABF
_LC_VERSION_MIN_MACOSX = 0x24
_LC_BUILD_VERSION = 0x32
_PLATFORM_MACOS = 1

Version = tuple[int, int, int]


def _decode(value: int) -> Version:
    return (value >> 16, (value >> 8) & 0xFF, value & 0xFF)


def _thin_minimum(data: bytes, offset: int = 0) -> Version | None:
    if len(data) < offset + 28:
        return None
    (magic,) = struct.unpack_from("<I", data, offset)
    if magic not in (_THIN_64, _THIN_32):
        return None
    ncmds, _sizeofcmds = struct.unpack_from("<II", data, offset + 16)
    cursor = offset + (32 if magic == _THIN_64 else 28)
    found: Version | None = None
    for _ in range(ncmds):
        if len(data) < cursor + 8:
            break
        cmd, cmdsize = struct.unpack_from("<II", data, cursor)
        if cmd == _LC_BUILD_VERSION and len(data) >= cursor + 16:
            platform, minos = struct.unpack_from("<II", data, cursor + 8)
            if platform == _PLATFORM_MACOS:
                found = _decode(minos)
        elif cmd == _LC_VERSION_MIN_MACOSX and len(data) >= cursor + 12:
            (version,) = struct.unpack_from("<I", data, cursor + 8)
            found = _decode(version)
        if cmdsize < 8:
            break
        cursor += cmdsize
    return found


def macho_minimum(data: bytes) -> Version | None:
    """The macOS version `data` requires at least, or None if it is not a
    Mach-O binary (or declares none). A universal binary counts its highest
    slice: the executable needs whatever its most demanding part needs."""
    if len(data) < 8:
        return None
    (magic,) = struct.unpack_from(">I", data, 0)
    if magic in (_FAT, _FAT_64):
        (count,) = struct.unpack_from(">I", data, 4)
        entry = 32 if magic == _FAT_64 else 20
        minimums = []
        for index in range(count):
            base = 8 + index * entry
            if magic == _FAT_64:
                offset = struct.unpack_from(">Q", data, base + 8)[0]
            else:
                offset = struct.unpack_from(">I", data, base + 8)[0]
            found = _thin_minimum(data, offset)
            if found:
                minimums.append(found)
        return max(minimums) if minimums else None
    return _thin_minimum(data)


def over_limit(
    parts: Iterable[tuple[str, bytes]],
    limit: Version,
    allow: dict[str, Version] | None = None,
) -> tuple[list[tuple[str, Version]], int]:
    """The parts that require more than their limit, and how many were
    binaries. `allow` gives named parts a limit of their own -- a known,
    documented exception, held to exactly that version."""
    allow = allow or {}
    over: list[tuple[str, Version]] = []
    checked = 0
    for name, data in parts:
        found = macho_minimum(data)
        if found is None:
            continue
        checked += 1
        if found > allow.get(name, limit):
            over.append((name, found))
    return over, checked


def _executable_parts(exe: Path) -> Iterable[tuple[str, bytes]]:
    """The executable's own bootloader, then every file packed into it."""
    from PyInstaller.archive.readers import CArchiveReader

    yield exe.name, exe.read_bytes()
    archive = CArchiveReader(str(exe))
    for name in archive.toc:
        data = archive.extract(name)
        if isinstance(data, bytes):
            yield name, data


def _parse_version(text: str) -> Version:
    parts = [int(part) for part in text.split(".")]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("executable", type=Path)
    parser.add_argument("--max", default="11.0", help="the newest macOS it may require")
    parser.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="PART=VERSION",
        help="a named part allowed up to its own version (repeatable)",
    )
    args = parser.parse_args(argv)
    limit = _parse_version(args.max)
    allow = {
        name: _parse_version(version)
        for name, _, version in (item.partition("=") for item in args.allow)
    }

    parts = list(_executable_parts(args.executable))
    highest = max(
        ((name, found) for name, data in parts if (found := macho_minimum(data))),
        key=lambda item: item[1],
        default=None,
    )
    over, checked = over_limit(parts, limit, allow)
    for name, version in allow.items():
        print(f"allowed: {name} up to macOS {'.'.join(map(str, version))}")
    shown = ".".join(map(str, limit))
    if highest:
        print(
            f"{checked} native part(s); the most demanding needs macOS "
            f"{'.'.join(map(str, highest[1]))} ({highest[0]}); limit {shown}"
        )
    if over:
        print(f"these require a newer macOS than {shown}:", file=sys.stderr)
        for name, found in over:
            print(f"  {name}: macOS {'.'.join(map(str, found))}", file=sys.stderr)
        return 1
    if not checked:
        print("no Mach-O binaries found -- is this a macOS executable?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
