"""Write the licence bundle into the package, so a build carries it.

    uv run python tools/write_license_bundle.py [--into DIR]

Run by `tools/build_binary.py` before PyInstaller so the bundle is inside the
executable, and usable on its own in a source checkout -- `connections-export
licenses` needs a bundle to read, and a checkout has never built one.

The bundle lands in `connections_export/_licenses/` because that is what
`--collect-data connections_export` picks up, so the same directory resolves
from a wheel, from a source checkout and from inside the frozen bundle.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from connections_export import sbom  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--into", type=Path, default=None, help="where to write the bundle")
    parser.add_argument(
        "--also",
        default="",
        help=(
            "Comma-separated distributions to include beyond the wheel's own "
            "dependencies -- what a frozen build actually drew in."
        ),
    )
    parser.add_argument(
        "--artifact",
        choices=("package", "executable"),
        default="package",
        help=(
            "What the NOTICE is describing. A package REQUIRES its components "
            "(pip installs them alongside); an executable CONTAINS them."
        ),
    )
    parser.add_argument(
        "--version",
        default=None,
        help=(
            "The version this bundle describes. Without it the subject is "
            "whatever version is INSTALLED here, which during a release build "
            "is the build environment's copy rather than the one being "
            "packaged -- how 0.1.1 and 0.1.2 came to ship an SBOM naming "
            "0.1.0."
        ),
    )
    args = parser.parse_args()

    destination = args.into or sbom.bundled_dir()
    also = {name.strip() for name in args.also.split(",") if name.strip()}
    document = sbom.write_license_bundle(
        destination, also=also, artifact=args.artifact, version=args.version
    )
    components = len(document["components"])
    print(f"{components} components -> {destination}")
    print(f"  {sbom.SBOM_FILENAME}, {sbom.NOTICE_FILENAME}, and one folder of texts each")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
