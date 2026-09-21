"""Reference ingesters — worked examples of consuming an interchange
package (docs/reference/interchange-format.md §7), and proof the written
contract is buildable with no HCL knowledge. `obsidian` is the first.
"""

from connections_export.ingest.obsidian import (
    VaultStats,
    from_package,
    from_source,
    write_obsidian_vault,
)

__all__ = ["write_obsidian_vault", "from_package", "from_source", "VaultStats"]
