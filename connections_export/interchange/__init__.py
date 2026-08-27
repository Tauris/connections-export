"""Public surface of the `interchange` capability: serialize a derived `Interchange` to a portable,
self-
documenting package directory, and read one back.

`write_package`/`load_package`/`open_blob` are the only intended entry
points for callers outside this package; the capability-derivation
internals of `manifest.py` and the file-layout internals of `package.py`
are implementation detail.
"""

from connections_export.interchange.manifest import CapabilityMap, PackageCounts, PackageManifest
from connections_export.interchange.package import load_package, open_blob, write_package

__all__ = [
    "write_package",
    "load_package",
    "open_blob",
    "PackageManifest",
    "CapabilityMap",
    "PackageCounts",
]
