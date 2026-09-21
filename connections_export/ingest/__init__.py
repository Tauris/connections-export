"""Reference ingesters — worked examples of consuming an interchange
package (docs/reference/interchange-format.md §7), and proof the written
contract is buildable with no HCL knowledge. `obsidian` is the first.
"""

from connections_export.ingest.jekyll import (
    JekyllStats,
    write_jekyll_site,
)
from connections_export.ingest.jekyll import (
    from_source as from_jekyll_source,
)
from connections_export.ingest.obsidian import (
    VaultStats,
    from_package,
    from_source,
    write_obsidian_vault,
)


def from_source_for_format(source, out_dir, format_name: str):
    if format_name == "obsidian":
        return from_source(source, out_dir)
    if format_name == "jekyll":
        return from_jekyll_source(source, out_dir)
    raise ValueError(f"unsupported ingest format: {format_name}")


__all__ = [
    "JekyllStats",
    "VaultStats",
    "from_package",
    "from_source",
    "from_source_for_format",
    "write_jekyll_site",
    "write_obsidian_vault",
]
