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
says so instead of rendering blank pages. Only `hugo.toml` is generated: its
title comes from the export and whether raw HTML is allowed depends on the
mode the bodies were written in.
"""

from __future__ import annotations

from pathlib import Path

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
    "layouts/_partials/count.html",
    "layouts/_partials/crumbs.html",
    "layouts/_partials/entries.html",
    "layouts/_partials/facts.html",
    "layouts/_partials/tree.html",
    "static/css/site.css",
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


def hugo_config(title: str, html_mode: str) -> str:
    """The starter site's `hugo.toml`."""
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


def write_starter_site(root: Path, *, title: str, html_mode: str) -> None:
    """Write the starter site into `root`, beside its `content/`."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "hugo.toml").write_text(hugo_config(title, html_mode), encoding="utf-8")
    for name in STARTER_FILES:
        if name == "hugo.toml":
            continue
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((_FILES_ROOT / name).read_bytes())


def readme_section() -> list[str]:
    """The export README's section on the starter site."""
    return [
        "## Starter site",
        "",
        "Beside `content/` is a small starter site to view this export with:",
        "",
        "- `hugo.toml` — the site configuration: the title, relative URLs so the "
        "built site works from any folder, the `tags` taxonomy, and raw HTML allowed "
        "when the page content includes HTML.",
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
        "These files are a starting point, meant to be edited or replaced — change the "
        "templates, restyle it, or add a theme. To put the content into an existing "
        "site instead, `content/` alone is what goes in; leave the starter files "
        "behind.",
        "",
    ]
