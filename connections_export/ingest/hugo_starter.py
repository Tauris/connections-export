"""The optional starter site beside a Hugo export: enough to view it with
`hugo server`, and no more.

The Hugo exporter writes content only, because a site's templates and
configuration belong to its owner (`hugo.py`). Someone who has no Hugo site
yet still wants to SEE the export, though, and a folder of Markdown with no
templates renders as nothing. The starter site closes that gap: a
`hugo.toml`, a small set of templates and one stylesheet, written next to
`content/` -- never inside it, so `content/` is still exactly what goes into
an existing site, and the starter files are simply left behind.

The templates and the stylesheet are files in `hugo_starter_site/`, copied as
they are, so they can be read and edited like any others. They use Hugo's
template layout from 0.146 on (`layouts/home.html`, `section.html`,
`page.html`, `_partials/`); `hugo.toml` names that minimum, so an older Hugo
says so instead of rendering blank pages. They read both layouts the
exporter writes -- sections at the top, or a folder per community first --
from `params.kind`. Only `hugo.toml` is generated: its
title comes from the export and whether raw HTML is allowed depends on the
mode the bodies were written in.

Two site parameters in `hugo.toml` steer the templates. `homeLayout` draws
the front page, and each community's page, as a plain list (`list`, the
default) or as cards (`cards`); it is a parameter rather than a choice baked
into the templates so the site's owner can switch by editing one line, with
no export run again. `connectionsSites` names the Connections deployment the
content came from, for the footer: written here as TOML strings, it reaches
a page only through html/template's escaping.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

#: The templates and stylesheet, as they are copied: their paths below the
#: export folder, which are also their paths below `_FILES_ROOT`.
_FILES_ROOT = Path(__file__).parent / "hugo_starter_site"

#: Every file the starter site writes, relative to the export folder.
STARTER_FILES = (
    "hugo.toml",
    "layouts/baseof.html",
    "layouts/home.html",
    "layouts/section.html",
    "layouts/page.html",
    "layouts/taxonomy.html",
    "layouts/term.html",
    "layouts/_partials/article.html",
    "layouts/_partials/containers.html",
    "layouts/_partials/count.html",
    "layouts/_partials/crumbs.html",
    "layouts/_partials/entries.html",
    "layouts/_partials/facts.html",
    "layouts/_partials/overview.html",
    "layouts/_partials/tree.html",
    "static/css/site.css",
)

#: How the front page and each community's page are drawn: a plain list, or
#: a card per community or section. The first is the default.
STARTER_LAYOUTS = ("list", "cards")
DEFAULT_STARTER_LAYOUT = STARTER_LAYOUTS[0]

#: A DNS name as a URL carries it, lower-cased: labels of letters, digits
#: and inner hyphens. Anything else in a host -- a quote, a bracket, a space
#: -- does not name a deployment, and is not linked.
_HOSTNAME = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
)

#: The oldest Hugo the templates run on: 0.146 introduced the template layout
#: they use.
MIN_HUGO_VERSION = "0.146.0"


def _toml_string(value: str) -> str:
    """`value` as one TOML basic string. The title is an author's, so every
    character that could end the string or the line is escaped; TOML allows
    no raw control character in a basic string, DEL included."""
    out = []
    for char in value:
        if char in ('"', "\\"):
            out.append("\\" + char)
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def check_layout(layout: str | None) -> str:
    """`layout` if it is one of `STARTER_LAYOUTS`, the default for `None`;
    anything else is refused."""
    if layout is None:
        return DEFAULT_STARTER_LAYOUT
    if layout not in STARTER_LAYOUTS:
        raise ValueError(
            "the starter site's front page is "
            + " or ".join(f"'{name}'" for name in STARTER_LAYOUTS)
            + f", not {layout!r}"
        )
    return layout


def site_of(url: str | None) -> tuple[str, str] | None:
    """The Connections deployment `url` belongs to, as (name, address): its
    host (with its port, when that is not the scheme's default) and the root
    of that host -- or `None` when `url` is not an http(s) URL with a plain
    host.

    Only the scheme, host and port are kept, and the address is rebuilt from
    them: the path, query and any user name are dropped, so nothing an
    address carried besides its host can reach the page. The footer links
    to the deployment, not to wherever a base URL happened to point."""
    if not url:
        return None
    try:
        split = urlsplit(url.strip())
        port = split.port
    except ValueError:
        return None
    scheme = split.scheme.lower()
    host = (split.hostname or "").lower().rstrip(".")
    if scheme not in ("http", "https") or not host:
        return None
    if _HOSTNAME.fullmatch(host):
        name = host
    else:
        try:
            name = f"[{ipaddress.IPv6Address(host)}]"
        except ValueError:
            return None
    if port is not None and port != (443 if scheme == "https" else 80):
        name = f"{name}:{port}"
    return name, f"{scheme}://{name}/"


def hugo_config(
    title: str,
    html_mode: str,
    *,
    layout: str | None = None,
    sites: list[tuple[str, str]] | tuple = (),
) -> str:
    """The starter site's `hugo.toml`. `layout` is the front page's
    (`STARTER_LAYOUTS`, the default for `None`); `sites` the deployments the
    content came from, as `site_of` gives them, linked in the footer."""
    layout = check_layout(layout)
    params = [
        "",
        '# The front page, and each community\'s page: "list", a plain list of what',
        '# the site holds, or "cards", a card for each community or section. Switch',
        "# by editing this line -- `hugo server` shows the change at once, `hugo`",
        "# builds it, and the content does not need exporting again.",
        "[params]",
        f"  homeLayout = {_toml_string(layout)}",
    ]
    if sites:
        params += [
            "  # The HCL Connections deployment the content came from, linked in every",
            "  # page's footer.",
            "  connectionsSites = [",
            *(
                f"    {{ name = {_toml_string(name)}, url = {_toml_string(url)} }},"
                for name, url in sites
            ),
            "  ]",
        ]
    lines = [
        "# The starter site's configuration: enough to view this export with",
        "# `hugo server`. Edit or replace it -- see README.md.",
        "",
        '# "/" and relative URLs, so the built site works from any folder or path',
        "# it is copied to, not only from the root of a web server.",
        'baseURL = "/"',
        "relativeURLs = true",
        f"title = {_toml_string(title)}",
        "",
        "# Only the page kinds the templates render: no feeds, sitemap, robots.txt",
        "# or 404 page, which a site of your own can turn back on.",
        'disableKinds = ["rss", "sitemap", "robotsTXT", "404"]',
        *params,
        "",
        "# The items' own tags from Connections; no other taxonomy is written.",
        "[taxonomies]",
        '  tag = "tags"',
        "",
        "# The templates use the layout Hugo introduced in 0.146.",
        "[module.hugoVersion]",
        f'  min = "{MIN_HUGO_VERSION}"',
    ]
    if html_mode != "markdown":
        lines += [
            "",
            f"# The pages were exported in {html_mode!r} mode, so parts of them are HTML.",
            "# Hugo leaves raw HTML out of a page unless this is on; without it those",
            "# parts would be missing. In 'mixed' and 'html' mode the exporter has",
            "# already removed scripts and event handlers; 'raw' is the HTML exactly",
            "# as captured, and is your responsibility to publish.",
            "[markup.goldmark.renderer]",
            "  unsafe = true",
        ]
    return "\n".join(lines) + "\n"


def write_starter_site(
    root: Path,
    *,
    title: str,
    html_mode: str,
    layout: str | None = None,
    sites: list[tuple[str, str]] | tuple = (),
) -> None:
    """Write the starter site into `root`, beside its `content/`: its front
    page drawn as `layout`, its footer linking to `sites`."""
    config = hugo_config(title, html_mode, layout=layout, sites=sites)
    root.mkdir(parents=True, exist_ok=True)
    (root / "hugo.toml").write_text(config, encoding="utf-8")
    for name in STARTER_FILES:
        if name == "hugo.toml":
            continue
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((_FILES_ROOT / name).read_bytes())


def readme_section(layout: str | None = None) -> list[str]:
    """The export README's section on the starter site, whose front page is
    drawn as `layout`."""
    layout = check_layout(layout)
    return [
        "## Starter site",
        "",
        "Beside `content/` is a small starter site to view this export with:",
        "",
        "- `hugo.toml` — the site configuration: the title, relative URLs so the "
        "built site works from any folder, the `tags` taxonomy, raw HTML allowed "
        "when the page content includes HTML, and the Connections deployment the "
        "content came from (`connectionsSites`), linked in the footer.",
        "- `layouts/` — the templates: a page frame with the sections in its header, "
        "a home page, each wiki's page tree in the wiki's own order, blog posts and "
        "forum topics newest first, the file libraries, single pages with their "
        "author, date, tags and a link back to the original, and a page per tag.",
        "- `static/css/site.css` — one stylesheet, light and dark, nothing loaded from "
        "anywhere else.",
        "",
        f"To view it, install Hugo ({MIN_HUGO_VERSION} or later), then in this folder run:",
        "",
        "```sh",
        "hugo server",
        "```",
        "",
        "and open the address it prints. `hugo` on its own builds the site into `public/`.",
        "",
        "The front page — and, in an export of several communities, each community's "
        'page — is drawn as a plain list (`"list"`, the default) or as cards, one for '
        'each community or section (`"cards"`). The setting is `homeLayout` under '
        f'`[params]` in `hugo.toml`, `"{layout}"` in this export: edit that line and '
        "rebuild; the content does not need exporting again.",
        "",
        "These files are a starting point, meant to be edited or replaced — change the "
        "templates, restyle it, or add a theme. To put the content into an existing "
        "site instead, `content/` alone is what goes in; leave the starter files "
        "behind.",
        "",
    ]
