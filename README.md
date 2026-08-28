# connections-export

> Preserve HCL Connections content — wikis, blogs, and forums — as a portable,
> self-documenting archive you control.

Wikis, blogs, and forums hold years of a team's shared knowledge.
**connections-export** captures that content from an HCL Connections deployment
into a durable, open-format archive you can keep, browse, and re-home — so what
matters isn't bound to the lifetime of any single platform.

Built for content you already have access to, and for keeping it close: the
archive is meant to live alongside the originals, not to travel further than
they would.

## An alternative that keeps your options open

HCL Connections can already export a wiki or a blog, and anything can be printed
to PDF — but those are snapshots. A PDF flattens the page hierarchy, comment
threads, and versions into something you can read but can't rebuild, and neither
route is really meant to be re-homed somewhere else. connections-export keeps
that structure intact in an open format, with far less loss than a PDF — so the
content stays faithful today and portable for whatever you move it into next.

## What it does

- **Exports wikis, blogs, forums, a community's files, and its front page** —
  page hierarchy, blog posts with their comment threads, forum topics with
  nested replies, the documents in a community's Files section downloaded under
  their own names, and the Rich Content pages on its Highlights area.
- **Lossless capture** — every response is archived byte-for-byte. Nothing
  captured is silently dropped, and anything missing is surfaced rather than
  hidden.
- **A portable, self-documenting package** — content lands in an open,
  tool-agnostic format (`interchange.json` + content-addressed blobs), with the
  format specification bundled inside every package, so anyone can build a
  reader without product-specific knowledge.
- **Browse it back** — a built-in local web console reconstructs the wiki, with
  hierarchy, threaded comments, images, and search.
- **Export onward** — render to a single print-ready **PDF**, or reconstruct a
  wiki as an **Obsidian vault** (Markdown with `[[wikilinks]]`).
- **Respects people** — personal avatars are never exported; consent for the
  source system doesn't extend to a copy.

## Install

```sh
pip install connections-export
# with the Obsidian exporter:
pip install "connections-export[obsidian]"
```

Requires Python 3.12 or newer (tested on 3.12, 3.13 and 3.14, on Linux, macOS
and Windows).

**No Python?** Each release also ships a single-file executable for Linux,
macOS (Intel and Apple silicon) and Windows — download it from the
[Releases](https://github.com/Tauris/connections-export/releases) page, make it executable, and run it. It needs no
Python and no install. See *PDF export needs a browser* below for its one
external requirement.

**Windows Integrated Auth.** The Windows executable includes it — SSPI is the
default auth mode there, so it signs in with your existing session and needs
nothing installed. With `pip`, ask for the extra: `pip install
"connections-export[sspi]"`. On Linux that extra needs system Kerberos headers
first (`libkrb5-dev` on Debian/Ubuntu, `krb5-devel` on Fedora/RHEL); Windows and
macOS need nothing extra.

### PDF export needs a browser

PDF rendering drives a Chromium-based browser. The tool finds one by itself, in
this order: **Microsoft Edge**, then **Google Chrome**, then Playwright's own
bundled Chromium. Edge ships with Windows, so on Windows this normally needs no
setup at all.

If none is found:

- **Installed with `pip`** — install Edge or Chrome, *or* run
  `playwright install chromium` to fetch a private copy.
- **Using the standalone executable** — install Edge or Chrome. The executable
  has no `playwright` command, so fetching a private Chromium is not an option
  there.

Two things worth knowing:

- **The browser must be in the same operating system as the tool.** A Windows
  Edge cannot be driven from a WSL or Linux process — install a browser there
  instead. This is the one that most often looks like a bug.
- Override the automatic choice with `CONNECTIONS_EXPORT_BROWSER_CHANNEL`
  (`msedge` or `chrome`) or `CONNECTIONS_EXPORT_BROWSER_PATH` (an explicit
  executable).

Everything else — capture, browsing, the Obsidian export — works with no
browser at all.

## Quick start

**Start with the console.** It is the way in for almost everything — drop in a
URL to capture, or open an archive you already have, and watch it happen:

```sh
connections-export serve
```

To see the whole thing working with no deployment at all — against a synthetic
one that ships inside the tool — start the console and drop one of the demo
URLs it offers on the setup screen:

```sh
connections-export serve
```

The console carries **the manual** — what an archive is, what a capture takes
in and leaves out, what an update costs, and every command-line switch. It is
the same document as [`docs/manual.md`](https://github.com/Tauris/connections-export/blob/main/docs/manual.md) here, so everything
below is a summary of a page you already have.

### Or from the command line

The command line captures the same things, for when you want a capture to run
unattended — scripted, scheduled, repeated. What it does **not** do is help you
decide: there is no browsing, no preview, no picking from a list.

So you do not have to assemble it by hand. **Choose in the console, and it shows
you the exact command for what you selected** — URL, components, author filter,
size — updating as you change things, with a Copy button. Paste that into your
script.

A URL is the unit of specification, and it is the same URL you would drop into
the console:

```sh
# a wiki, a blog, a forum — or one page, post or thread, if the URL names one
connections-export crawl https://connections.example.corp/wikis/home/wiki/eng-handbook

# a community, with the parts to capture (this is the line the console writes)
connections-export crawl "https://connections.example.corp/communities/service/html/communityview?communityUuid=…" \
    --component wiki:eng-handbook --component forum:… --component blog:… \
    --component files:… --component rich_content:… \
    --target-label "Engineering" --output-dir ./archive
```

The URL carries the deployment address, so `--base-url` is not needed when you
give one. Several URLs and components land in **one** archive, which is what
makes a batch a batch. `--author` narrows to one person's content, `--max-entries`
caps how much is taken per container.

With nothing to point at yet, `--demo` runs the whole thing against a synthetic
deployment that ships inside the tool:

```sh
connections-export pdf --demo --output sample.pdf
connections-export crawl --demo --output-dir ./demo-archive
```

Then render or convert what you captured:

```sh
connections-export pdf --archive ./archive --output export.pdf
connections-export ingest --format obsidian --package <dir> --output <vault>
```

Settings can also come from `connections-export.toml` or `CONNECTIONS_EXPORT_*`
environment variables. Precedence is CLI flags → environment → config file →
defaults. Secrets come only from the environment or a prompt, never from a file.
Windows Integrated Auth (SSPI/Kerberos) uses your existing session.

Run `connections-export <command> --help` for the full set.

### Pacing, and running overnight

An export is thousands of requests against a live deployment, so it waits **1
second between them by default**. For anything large, **3 seconds overnight** is
the kinder choice — it is the same number of requests either way, and a slower
export is one nobody has to notice.

```sh
connections-export crawl <URL> --delay 3
```

The console has the same control on the Output step, and as a default under
Settings. The [manual](https://github.com/Tauris/connections-export/blob/main/docs/manual.md) says why 3 is the kinder
number.

### Changing how the PDF looks

Type sizes, line height, colours and the running header and footer are settings
rather than markup — `connections-export style --tokens` lists them, and
**Settings → PDF appearance** puts them on screen with a **Preview** button that
renders a real two-page sample. For anything they do not cover, have the program
write its own stylesheet out with `style --dump`, edit that, and pass it back
with `pdf --css`.

The [manual](https://github.com/Tauris/connections-export/blob/main/docs/manual.md) has each setting and what it does.

## How it looks

The archive preserves your **content and its structure** — hierarchy, threads,
attachments — and keeps each author's own inline styling. It does **not**
reproduce the platform's own look and feel, so a reconstructed page is faithful
in substance but may look different from the original.

The one exception is the **direct browser export**, which captures pages with
full visual fidelity from the running system itself. Because it reads the live
deployment, it's available only while the original system is still reachable —
it cannot be produced from an archive after the fact.

## What gets captured

An export captures **what you selected**, and is honest about the edges.

- **Images embedded in a page are captured**, including ones held elsewhere on
  the deployment — a picture stored in a Files library rather than in the wiki
  itself comes along. So are **attachments attached to** a page, post or topic.
- **A document linked to in Files comes too**, even from a community you did
  not select — a page that links a specification or a spreadsheet keeps it,
  because a link only works while the deployment is reachable. Only a link
  naming the document's bytes, and only with your own credentials: one you
  could not open in the live system is recorded as not captured, with the
  reason. Images on the public web are recorded but never fetched, and
  personal avatars are never exported at all.
- **A community's files come down as files.** Documents land in `files/`, under
  their real names and in their folders — copying that directory out is the
  whole job. Where a name cannot survive a filesystem (two files called
  `Report.pdf`, a colon in a name, `CON.txt` on Windows) it is made safe, and
  `files.json` records the original and why it changed, so a system that can
  hold the original can restore it rather than inherit the compromise.
- **A community's front page is captured too.** The Rich Content areas on the
  Highlights page — the text, tables and images the owners wrote *about*
  everything else — come across as pages with their bodies intact. Where an
  owner placed a rich content area and never wrote in it, the export says so
  rather than quietly reporting one page fewer.
- **An archive can be added to later.** While the original system is still
  reachable, come back to an archive to capture a component you left out, or to
  pick up what has changed since. A re-captured item replaces its own earlier
  copy — nothing is duplicated, and nothing already captured is removed. Links
  between components captured in different visits resolve once both are in.
- **What an update costs is shown before you start**, because it differs by
  component: blogs, ideas and forums can be asked directly for what changed
  since a date; a wiki cannot, so its page list is re-read and each page's date
  compared (unchanged pages cost nothing beyond that). A wiki can also simply
  be left as it is.
- **New comments on old content still arrive.** Asking a system "what changed
  since Tuesday" returns the items that *changed*, and a comment on a
  years-old post may not count as changing it — so a wiki page's own date is
  compared, a forum reply moves its topic, and each blog is asked which
  *comments* changed as well. An update sees all three; there is nothing to
  switch on.
- **Links between things you captured are resolved** to point inside the
  archive — across components, not just within one. Capture a community and its
  wiki, blogs and forums cross-link to each other, because they were captured
  together.
- **Several communities go into one archive**, and that is what makes the links
  between them survive. Exporting each separately looks equivalent and is not:
  it leaves two archives that each know the other's content only as an outside
  reference.
- **Following a link never pulls a *page* in.** A link to an article in a
  forum, blog or wiki you did not select is kept exactly as it was, still
  pointing at the live system. A file is a leaf and comes along; a page brings
  its own comments, assets and onward links, and following those is how an
  export turns into a crawl of the whole deployment by accident. If you want
  the target too, select it as well.

## The archive format

For what a capture takes in, what it leaves out, and how an archive is added to
later, the [manual](https://github.com/Tauris/connections-export/blob/main/docs/manual.md) is the full account; this is the
shape of the thing it produces.

The export is deliberately not a proprietary blob. It's an open interchange
package described by [the format specification](https://github.com/Tauris/connections-export/blob/main/docs/reference/interchange-format.md), a copy
of which travels inside every package — the Obsidian exporter is just a worked
example of consuming it. Your content stays portable and readable long after the
export.

**On writing your own exporter:** how much work that is depends far more on the
destination than on the package. Obsidian is a directory of Markdown files, so
the exporter can write whatever it decides to write. Hosted platforms often
impose a fixed content model that no amount of care on the reading side can
widen — SharePoint site pages, for instance, have a straightforward ingestion
API, but their rich-text editor retains only a limited subset of HTML: headings
`h2`–`h4`, effectively no CSS, no code blocks, inline images and tables only in
specific forms. Content outside that subset is dropped, in some cases not at the
point of writing but when a person later edits the page. That is a property of
the target, not of the format, and it is worth checking before assuming a given
platform can take a wiki intact. Section 7.1 of the format specification covers
what to look for.

## Status

An early release, targeting HCL Connections 8.0. Keep your own backups and
verify an export before you rely on it.

## What is inside it, and under what licence

The tool ships an SBOM and the full licence text of every component it
carries — Python packages and the vendored JavaScript alike:

```sh
connections-export licenses                  # the inventory
connections-export licenses --texts          # every licence text, to stdout
connections-export licenses --extract ./out  # ...or written to a directory
connections-export licenses --sbom           # CycloneDX JSON
```

The console has the same page under **Licenses**, with the SBOM as a download.

Both read a bundle generated at build time from what actually ships, so it
cannot go stale, and it covers the single-file executable too — where there is
no `pip list` to run. A component whose licence cannot be established fails the
build rather than shipping: see `connections_export/sbom.py`.

## Not affiliated with HCL

connections-export is an independent, community tool. It is not affiliated with,
endorsed by, or sponsored by HCL. "HCL" and "HCL Connections" are trademarks of
their respective owners.

## Development

Requires [`uv`](https://docs.astral.sh/uv/). Common tasks live in the
[`justfile`](https://github.com/Tauris/connections-export/blob/main/justfile) — run `just` to list them:

```sh
just sync     # create the venv and install dependencies
just test     # run the test suite
just check    # lint + format-check + tests (what CI runs)
just console  # the console; drop a demo URL from its setup screen to try it
```

Every task also works as the underlying `uv run …` command, shown in the
justfile, if you would rather not install `just`.

## License

[BSD 3-Clause](https://github.com/Tauris/connections-export/blob/main/LICENSE)
