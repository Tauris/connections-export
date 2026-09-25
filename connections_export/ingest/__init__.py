"""Reference ingesters — worked examples of consuming an interchange
package (docs/reference/interchange-format.md §7), and proof the written
contract is buildable with no HCL knowledge. `obsidian` is the first;
`jekyll` and `hugo` write content for a static site.
"""

from connections_export.ingest._bodies import DEFAULT_HTML_MODE, HTML_MODES, html_mode_for
from connections_export.ingest.hugo import (
    HugoStats,
    write_hugo_content,
)
from connections_export.ingest.hugo import (
    from_source as from_hugo_source,
)
from connections_export.ingest.hugo_starter import (
    DEFAULT_STARTER_LAYOUT,
    STARTER_LAYOUTS,
    check_layout,
)
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

#: Every format `connections-export ingest --format` and the console accept.
FORMATS = ("obsidian", "jekyll", "hugo")


def from_source_for_format(
    source,
    out_dir,
    format_name: str,
    html_mode: str | None = None,
    *,
    starter_site: bool = False,
    starter_layout: str | None = None,
):
    """Write `source` in `format_name`, its bodies in `html_mode` (the
    format's own default when `None`). `starter_site` adds Hugo's starter
    site (`hugo_starter`), its front page drawn as `starter_layout` (`list`
    when `None`); both are refused for any other format, and the layout
    without the starter site."""
    if format_name not in FORMATS:
        raise ValueError(f"unsupported ingest format: {format_name}")
    if starter_site and format_name != "hugo":
        raise ValueError("the starter site is for Hugo exports only")
    if starter_layout is not None and not starter_site:
        raise ValueError("the front-page layout is for the starter site: ask for it too")
    if starter_site:
        check_layout(starter_layout)
    mode = html_mode_for(format_name, html_mode)
    if format_name == "obsidian":
        return from_source(source, out_dir, html_mode=mode)
    if format_name == "jekyll":
        return from_jekyll_source(source, out_dir, html_mode=mode)
    return from_hugo_source(
        source, out_dir, html_mode=mode, starter_site=starter_site, starter_layout=starter_layout
    )


__all__ = [
    "DEFAULT_HTML_MODE",
    "DEFAULT_STARTER_LAYOUT",
    "FORMATS",
    "HTML_MODES",
    "STARTER_LAYOUTS",
    "HugoStats",
    "JekyllStats",
    "VaultStats",
    "from_package",
    "from_source",
    "from_source_for_format",
    "html_mode_for",
    "write_hugo_content",
    "write_jekyll_site",
    "write_obsidian_vault",
]
