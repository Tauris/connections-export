# Manual

<nav class="manual-toc" aria-label="Manual sections">
  <a href="#man-intro">What this is</a>
  <a href="#man-format">The archive format</a>
  <a href="#man-export">Export — reconstructing into other tools</a>
  <a href="#man-scope">What gets captured — links and assets</a>
  <a href="#man-open">Reading an archive someone handed you</a>
  <a href="#man-move">Moving an archive around</a>
  <a href="#man-communities">Several communities in one archive</a>
  <a href="#man-update">Adding to an archive later</a>
  <a href="#man-repair">Repairing a blog archive from an older release</a>
  <a href="#man-pacing">Pacing, and running overnight</a>
  <a href="#man-proxy">Proxies</a>
  <a href="#man-cli">The command line</a>
  <a href="#man-style">Changing how the PDF looks</a>
  <a href="#man-marks">The running header and footer</a>
</nav>

## <a id="man-intro"></a>What this is

`connections-export` reads the wikis, blogs, forums, community files, and Highlights pages in an HCL Connections deployment and writes what it finds into a self-contained archive on disk — a directory of files you hold yourself, not a database or a subscription. Once that archive exists, this console can browse it offline in the built-in Reader, and turn any part of it into a PDF, with no further contact with the source system. It's built for content you already have access to.

The everyday flow lives in three console sections, in order:

- **Select & Tailor** — paste or drop a URL and choose how much to capture: a whole wiki, one sub-tree, a single page (the same kind of choice exists for blogs, forums, files and highlights). Optionally name yourself in **“Only content authored by”** and the capture keeps just what you wrote or took part in — matched on your name, user id, or contributor credit — each item with its full surrounding thread.
- **Ingest** — a live view of the capture in progress: pages discovered, fetched, and archived, with any truncation or data-loss warning surfaced as it happens rather than buried in a log afterward.
- **Reader** — reconstructs the hierarchy, threads, and comments from the archive so you can read, search, and export any of it to PDF, entirely offline.

The **demo** is a small synthetic HCL server built into the tool, and it runs this exact pipeline against it — the same crawler, the same archive format — so you can see the whole Select → Ingest → Reader flow end to end before pointing it at anything real. It is not a mode: it is a deployment like any other, at an address of its own, and the Select & Tailor screen offers its URLs to drop the way you would drop one of yours. Which deployment a capture reads follows from the URL you give it and from nothing else.

In the demo you are signed in as **M. Lindqvist**, one of the people in that synthetic data — the demo impersonates them. They wrote something in every component, so **Only me** filters to a real subset rather than to nothing, and the identity comes back the same way it does from a real deployment: the tool asks the server who you are and is told.

### <a id="man-one-person"></a>Capturing one person’s share of a community

A busy community is mostly other people’s writing, and asked for “only me”, a capture would have to read all of it anyway — every forum topic and every reply — because whether a thread is yours is only knowable once its replies have been read. In a busy community that is tens of thousands of entries fetched to keep a few hundred threads.

It does not work that way. When you press **Only me** (or resolve a person by email), the console gets a user id back from the deployment itself, and the capture asks the deployment’s own Search which threads in this community that person is in. Search answers with whole threads: reply forty times in one and it comes back once; reply once in someone else’s and it still comes back. Those threads are then read in full — topic, every reply, attachments and images — and the author filter still decides what is kept. Search chooses what to read; it never stands in for the content, and it never decides what is yours.

Three things this deliberately does not do. It does not run on a name you typed: only an id the deployment gave back is used, because a name Search happens to answer for could quietly return less than reading everything would. It does not run without a community, because a person query spanning a whole deployment is far more likely to be cut short. And if Search refuses, returns nothing, or names something this tool doesn’t recognise, the capture reads the forums in full instead and says so in the Ingest view — the one thing it must never do is quietly capture less. The same line tells you when Search’s own answer was cut short at this tool’s page limit.

The CLI does the same when given `--search-userid` with `--author` and a community URL. It is an explicit option there because the command line cannot tell a resolved id from a typed one.

## <a id="man-format"></a>The archive format

A capture writes an **archive** — the directory the console shows under Archives, holding every response as it came off the wire. From it, `connections-export package` writes a **package**: a directory holding the normalized content of one or more wikis, blogs, forums, file libraries, and community front pages, built to be read by any program — not just this tool, and not only by something written by someone who knows what HCL Connections is. The full specification ships as `docs/reference/interchange-format.md` in the source tree, and a verbatim copy, `INTERCHANGE.md`, travels inside every package it describes. This section summarizes it.

### <a id="man-format-layout"></a>Layout

```
<package>/
  interchange.json     the normalized content model — one JSON document
  manifest.json         a capability manifest: what this package
                         does and does not contain
  provenance.json        where this package came from
  INTERCHANGE.md          the format spec, copied in verbatim
  blobs/
    <sha256-hex>          binary files (images, attachments), one per
                          distinct file, named by its own content hash
```

`interchange.json` is the whole content model in one JSON document: every wiki's pages, with hierarchy (`parent_id`/`child_ids`, already sorted), comments, version history, attachments, tags, and per-entity provenance; every blog's posts and comments; and every forum's topics with their arbitrarily deep reply trees. Order and grouping are always pre-computed for you — a reader walks a wiki's `root_page_ids` and each page's `child_ids` and never has to re-derive tree structure or sibling order itself.

### <a id="man-format-blobs"></a>Content-addressed blobs

Every image and attachment's bytes are stored once in `blobs/`, named by the SHA-256 hash of their own content. A page's body keeps its original `<img src>`/`<a href>` values untouched; the capture-time meaning of each reference — whether the file was actually retrieved, and which blob holds it — is recorded separately as a resolved asset (`present`, `blob_hash`, `scope`). Because blobs are named by their own hash, identical files referenced from many pages are stored only once.

### <a id="man-format-provenance"></a>Provenance and honest gaps

Each page, post, and topic carries a `provenance` block recording where it came from, plus a human-readable `note` whenever capturing it was incomplete. Nothing is silently dropped: an asset from the same deployment that couldn't be retrieved shows up as `present: false`, never as a missing entry; a link is always classified as pointing inside the export, elsewhere on the same deployment, or fully external, rather than left for a reader to guess at. A `manifest.json` alongside the model summarizes all of this at a glance — counts, and whether version history, comment threading, and ACLs are actually present in this particular package — so an integrator knows what to expect before opening `interchange.json` itself.

This is what "portable" means here: nothing about the format's shape assumes HCL Connections. Field names are generic (`pages`, `comments`, `body_html`, …); the one HCL-specific identifier (`provenance.hcl_id`) is confined to one place; and the package explains itself, since its own reference document ships inside it.

### <a id="man-format-fidelity"></a>CSS fidelity: what travels with the archive

HCL authors could style a page's own content with CSS (never JavaScript), and that **author CSS** is captured into the archive along with everything else. HCL Connections' own **platform/system CSS** — the chrome, fonts, and layout the live site wraps around that content — is a different thing entirely: it lives on the running HCL system itself, so it can only combine with a page's author CSS _while that system is online_. This capture never tries to retrieve it; every offline path built from the archive shows the same honest approximation of that platform look, never a silent guess presented as exact.

<figure class="fidelity-diagram">
            <svg viewBox="0 0 860 400" role="img" aria-labelledby="fidelity-svg-title fidelity-svg-desc" xmlns="http://www.w3.org/2000/svg">
              <title id="fidelity-svg-title">CSS fidelity: now vs. later vs. live</title>
              <desc id="fidelity-svg-desc">The HCL system combines author CSS and system CSS only while it is
                online. Ingest captures author CSS into the archive but not system CSS. From the archive, the
                Reader, Portable PDF, and re-homing to another system all preserve structure and author CSS but
                not system CSS. Only a live Browser PDF captures author and system CSS exactly, and only while
                the system is online — it cannot be recreated from an archive afterward.</desc>
              <defs>
                <marker id="fc-arrowhead" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path class="fc-arrowhead" d="M0,0 L10,5 L0,10 z"></path>
                </marker>
                <marker id="fc-arrowhead-live" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path class="fc-arrowhead-live" d="M0,0 L10,5 L0,10 z"></path>
                </marker>
              </defs>

              <!-- arrows (drawn first so boxes/text sit cleanly on top) -->
              <line class="fc-arrow" x1="390" y1="78" x2="250" y2="150" marker-end="url(#fc-arrowhead)"></line>
              <line class="fc-arrow fc-live-arrow" x1="470" y1="78" x2="680" y2="150" marker-end="url(#fc-arrowhead-live)"></line>
              <line class="fc-arrow" x1="250" y1="214" x2="250" y2="246"></line>
              <line class="fc-arrow" x1="95" y1="246" x2="430" y2="246"></line>
              <line class="fc-arrow" x1="95" y1="246" x2="95" y2="286" marker-end="url(#fc-arrowhead)"></line>
              <line class="fc-arrow" x1="260" y1="246" x2="260" y2="286" marker-end="url(#fc-arrowhead)"></line>
              <line class="fc-arrow" x1="430" y1="246" x2="430" y2="286" marker-end="url(#fc-arrowhead)"></line>

              <!-- group captions -->
              <text class="fc-group" x="250" y="140" text-anchor="middle">Now — ingest</text>
              <text class="fc-group" x="680" y="140" text-anchor="middle">Live only — system online</text>
              <text class="fc-group" x="262" y="278" text-anchor="middle">Later — from the archive (no system CSS)</text>

              <!-- HCL system (the source, while online) -->
              <rect class="fc-box fc-src" x="320" y="14" width="220" height="64" rx="10"></rect>
              <text class="fc-title" x="430" y="42" text-anchor="middle">HCL system</text>
              <text class="fc-sub" x="430" y="60" text-anchor="middle">author CSS + system CSS</text>

              <!-- Archive (ingest result) -->
              <rect class="fc-box fc-archive" x="140" y="150" width="220" height="64" rx="10"></rect>
              <text class="fc-title" x="250" y="178" text-anchor="middle">Archive</text>
              <text class="fc-sub" x="250" y="196" text-anchor="middle">author CSS only</text>

              <!-- from the archive, later, offline -->
              <rect class="fc-box fc-out" x="20" y="286" width="150" height="64" rx="10"></rect>
              <text class="fc-title" x="95" y="314" text-anchor="middle">Reader</text>
              <text class="fc-sub" x="95" y="332" text-anchor="middle">offline, browse</text>

              <rect class="fc-box fc-out" x="185" y="286" width="150" height="64" rx="10"></rect>
              <text class="fc-title" x="260" y="314" text-anchor="middle">Portable PDF</text>
              <text class="fc-sub" x="260" y="332" text-anchor="middle">structure + author CSS</text>

              <rect class="fc-box fc-out" x="350" y="286" width="160" height="64" rx="10"></rect>
              <text class="fc-title" x="430" y="314" text-anchor="middle">Re-home</text>
              <text class="fc-sub" x="430" y="332" text-anchor="middle">move to another system</text>

              <!-- Live Browser PDF -- the one exact, online-only path -->
              <rect class="fc-box fc-live" x="560" y="150" width="240" height="92" rx="10"></rect>
              <text class="fc-title" x="680" y="178" text-anchor="middle">Live Browser PDF</text>
              <text class="fc-sub" x="680" y="196" text-anchor="middle">author CSS + system CSS (exact)</text>
              <text class="fc-sub" x="680" y="214" text-anchor="middle">live only · not recreatable from archive</text>
            </svg>
            <figcaption><strong>Author CSS preserved · platform CSS approximated.</strong> System CSS
              only ever combines with a page while HCL Connections is online — the Reader, Portable
              PDF, and re-homing to another system all work from the archive alone, after the system
              CSS window has closed. A live Browser PDF is the one path that captures the exact
              combined styling, and only for as long as the source system stays reachable; it can't be
              produced again from an archive later.</figcaption>
          </figure>

### <a id="man-format-spec"></a>Full interchange format specification

The sections above summarize the shape reconstruction needs; the full document — every field, the capability manifest, and the target-integration contract in §7 — is the same `docs/reference/interchange-format.md` that ships as `INTERCHANGE.md` inside every package. It's this tool's own trusted documentation, so it's safe to render and read right here.

<button class="btn" id="man-spec-toggle" type="button">Show the full specification</button>

<div id="man-spec-container" class="manual-spec" hidden></div>

## <a id="man-export"></a>Export — reconstructing into other tools

For most people the export they want is the **PDF** — a document to keep, print or send, produced right from the Reader. This section is about something different, and a bit more involved: turning a capture into a **Markdown project you can edit and re-home** into another tool. It is aimed at people who are comfortable with developer tools; if the PDF is all you need, you can happily skip the rest of this section.

Two such exporters ship today, on an equal footing — **Obsidian** and **Jekyll**. Both read the same capture and write a folder of Markdown next to it; they differ only in what that folder is shaped for.

- **[Obsidian](https://obsidian.md)** is a free, local Markdown notes app. The export is an Obsidian *vault* — a linked, browsable knowledge base you open and edit on your own machine, with the community's structure and cross-links preserved as `[[wikilinks]]`.
- **[Jekyll](https://jekyllrb.com)** is a static-site generator: it turns a folder of Markdown into a plain website. The export is a Jekyll *site* you can preview locally and publish anywhere static files are served — [GitHub Pages](https://pages.github.com), for instance.

Obsidian and Jekyll are **independent, third-party applications**, not part of this tool and not affiliated with it. These exporters are provided purely for convenience, to write formats those tools can read; providing them is **not an endorsement** of either, and this project makes no claim about them. Each is governed by its own licence and terms — obtain and use it from its own project. The names are the property of their respective owners.

**From the console.** Open an archive in the Reader, then use **Export → “Advanced — developer formats”** and choose **Export Obsidian vault** or **Export Jekyll site**. Each writes a folder beside the archive (`…-obsidian-vault` / `…-jekyll-site`) and tells you exactly where. The advanced section is tucked away on purpose, so the everyday PDF export stays front and centre.

**From the command line**, the same two exporters are one command:

```
connections-export ingest --format obsidian --archive PATH/TO/ARCHIVE --output PATH/TO/VAULT
connections-export ingest --format jekyll   --archive PATH/TO/ARCHIVE --output PATH/TO/SITE
```

`--archive` is an archive directory (the one holding `manifest.jsonl` and `blobs/`) or a `.zip` of one; `--package` takes a package written by `connections-export package` instead. Either flag accepts either kind — the directory says what it is — and `--author` narrows the output to one person's content. Nothing is read from or sent to the source deployment during an export — it only ever touches what is on disk. Both exporters work from a plain install — the executable has them built in, and a `pip`/`uv` install includes what they need (`markdownify`) as a base dependency, so no extra is required.

### <a id="man-export-obsidian"></a>Obsidian — a linked vault

The Obsidian exporter converts a capture into a vault. Every wiki becomes one note per page, with the page hierarchy mirrored as nested folders. Every blog becomes a folder with one note per post, in the blog's own order, comments beneath each. Every forum becomes a folder with one note per topic, its replies laid out beneath it as nested headings so a thread reads as a thread — each reply converted the same way as the topic, images and all, rather than flattened to a line. Links between items inside the export become `[[wikilinks]]` to the target note, whatever kind of item is on either end; other links are kept as-is. Images and attachments are copied out of the capture into the vault's `attachments/` folder and embedded by name (`![[file]]` / `[[file]]`) — an asset that was never captured becomes a visible marker instead of vanishing. YAML frontmatter carries each item's title, author, dates and tags, a `kind` for posts and topics, and its source provenance; a `README.md` at the vault root indexes every wiki, blog and forum. Open the folder in Obsidian as a vault and the whole community is there to browse.

### <a id="man-export-jekyll"></a>Jekyll — a publishable site

The Jekyll exporter writes a site fragment: dated Markdown files under `_posts/`, with images under `assets/images/imported/`, ready to drop into a Jekyll site whose `_config.yml` and layouts you own. It is the shape a static-site generator expects, so `jekyll serve` previews it locally and a push to a [GitHub Pages](https://pages.github.com) repository publishes it. It is the right choice when the goal is a public, browsable website rather than a private notebook — the same captured content, aimed at a different destination.

### <a id="man-export-new"></a>Building a new exporter

A self-contained brief for this — the model in one screen, the two-function shape, how to test without a deployment — is `docs/writing-an-ingester.md` in the source tree, written for someone starting from an example of the target format and nothing else.

An ingester needs no knowledge of HCL Connections at all — only of the interchange package. The contract it follows is written out step by step in `docs/reference/interchange-format.md` §7, "Reconstructing content in a target wiki": create every page first and keep an id mapping, set hierarchy and order from the pre-computed lists, attach comments/versions/attachments, then rewrite links and body assets using that id mapping, and finally record whatever the target format has no place for. The Obsidian ingester (`connections_export/ingest/obsidian.py`) is a worked, runnable example of exactly that shape — proof the contract is, in its own words, "buildable with no HCL knowledge."

A new exporter follows the same two-function shape:

- a writer that takes an already-loaded interchange model and a blob-reader function (`blob_hash → bytes | None`) and writes the target — see `write_obsidian_vault(interchange, blob_reader, out_dir)`;
- a thin `from_package(package_dir, out_dir)` convenience wrapper that loads the package with `connections_export.interchange.load_package`, wires up a real blob reader over `connections_export.interchange.package.open_blob`, and calls the writer.

Export it from `connections_export/ingest/__init__.py` alongside the Obsidian one, and add its name to the `--format` choices in `ingest_main` (`connections_export/cli.py`) so `connections-export ingest --format <yours>` can reach it.

## <a id="man-scope"></a>What gets captured — links and assets

An export captures **what you selected**. Understanding where that boundary falls saves a lot of puzzlement later.

### A community's files

A community's **Files** section is captured as a component in its own right, alongside its wiki, blogs and forums — select it the same way. Documents are **downloaded and written under their own names**, arranged in their folders, rather than stored as opaque blobs.

Some names cannot survive a filesystem: two documents genuinely called `Report.pdf`, a colon or question mark in a name, `Report.pdf` and `report.pdf` which are two files here and one on Windows, or `CON.txt`, which Windows will not open whatever its extension. Those are made safe, and the manifest records the original name and _why_ it changed — so a system that can hold the original is able to put it back rather than inheriting our compromise.

Folders are an association, not a location: a file can belong to more than one. The bytes are written once, and the manifest carries every membership.

In the reader, a file library appears under **FILES** as a listing rather than a document — a spreadsheet or an archive has no page to turn — with each captured file linking to its content, and each file that did _not_ come down marked **not captured** rather than left to look like the rest. If you captured with an author filter, the documents by other people were never asked for, and they say **outside the filter** instead — a document you left out and a document that would not come down are not the same thing, and the listing does not use one word for both. The PDF export carries the same listing.

A **Preview** run caps how many documents are downloaded, not how many are listed: the library is always described in full, so a capped run reads as “these are the files, and these are the ones I fetched”.

### The community's front page

A community's **Highlights** area can carry **Rich Content** pages — the text, tables and images the owners wrote _about_ everything else. They are captured as a component of their own, with their bodies intact, and appear in the reader under **HIGHLIGHTS** and in the PDF.

An owner can place a rich content area and never write in it. There is nothing to capture in that case, so the export records how many areas were _placed_ alongside how many had content — a count of three pages never quietly means there were four.

### Images and attachments: embedded is captured, linked is not

Two things are fetched and stored byte-for-byte:

- **Images embedded** in a captured body (an `<img>`) — including ones held elsewhere on the deployment, such as a picture stored in a Files library rather than in the wiki itself.
- **Attachments attached to** a captured page, post or topic.

A **link to a document in Files** is captured too, and this is the exception worth knowing. A page that links a specification, a spreadsheet or a slide deck held anywhere on the deployment — _including in a community you did not select_ — has that document fetched and stored, so the archive is **self-contained**: a link only works while the deployment is reachable, and a file that travels with the archive does not depend on that. People link across communities liberally, without meaning to decide which of their material would stand on its own.

Two limits, both deliberate. Only a link that names the document's **bytes** is fetched; a link naming a document by id points at a viewer page, and fetching that would store HTML under the name of a file — an archive claiming to hold something it does not. And it is fetched **with your own credentials**: a document you could not open in the live system is recorded as not captured, with the reason. That is the same position you were already in, and this is a user's export, not an administrator's backup.

"Elsewhere on the deployment" means the system you are exporting from, plus any additional hosts you list as belonging to it. Images on the public web are **recorded but never fetched** — the archive notes them and leaves them where they are. Personal avatars are never exported at all: consent to appear in the source system does not extend to a copy of it.

### Links between things you captured: resolved

Links are rewritten to point inside the archive whenever their target is _also in this export_. That works across everything a single run captured: page to page in a wiki, post to post in a blog, and **between components** — a blog post linking to a wiki page, a forum topic linking to a blog post. Capture a community and its wiki, blogs and forums cross-link to each other, because they were captured together.

### Links to things you did not capture: left pointing at the original

This is the part worth being clear about. Apart from the Files documents above, **following a link never pulls anything in.** If a page links to an article in a forum, blog or wiki you did not select, that target is _not_ fetched and does not join the archive. The link is kept exactly as it was, still pointing at the live system.

The difference is what the target is. A file is a leaf — it has bytes and nothing else. A page, post or topic brings its own comments, assets and onward links, and following those is how a user's export turns into a crawl of the whole deployment by accident. If you want one of those too, select it: everything selected in one run cross-links.

So an export is not a spidering crawl that follows links outward until it runs out of Connections. It captures what you chose, and is honest about the edges: a link out of the archive stays visible and clickable rather than quietly disappearing. If you want the target too, select it as well — everything selected in one run cross-links.

## <a id="man-open"></a>Reading an archive someone handed you

The **Archives** screen lists what is in the archives folder. An archive you were given does not start there — it is a zip in Downloads, a folder on a share, a file synced out of OneDrive, or a link in a message. You do not have to file it first: **drop it anywhere on the console** and it opens in the Reader.

- A **folder** or a **.zip** dragged from your file manager is read _where it lies_ — nothing is copied, and the archives folder is not touched.
- A file dragged out of **OneDrive or SharePoint** usually reaches the browser as content with no path at all, so its bytes are sent to the console and read from a temporary copy.
- A **link to a .zip** is downloaded and opened. If the share wants a login it answers with a sign-in page rather than an error, so what came back is checked for actually being a zip — an archive that is really a login page is refused rather than read.

Only archives are treated this way: a dropped Connections URL still starts a capture, as it always did. A zipped archive is **read-only** — it browses and exports to PDF, and “Extend & update” refuses rather than appearing to run.

## <a id="man-move"></a>Moving an archive around

An archive is a directory of many small files. That is the right shape to write — each item lands as it is captured, and a run that stops halfway leaves everything up to that point readable — and an awkward shape to move. File sync (SharePoint, OneDrive) handles thousands of small files worst of all, and a copy that silently drops some of them is the failure mode an archive can least afford.

So an archive is also read from a **`.zip` directly, without unpacking it**. To make one, use **Save as .zip** beside any archive on the Archives screen: the console writes `<name>.zip` next to the archive folder — on your own disk, nothing sent through the browser, so it works for archives of any size — and tells you where. (You can still zip the folder yourself if you prefer.) The `.zip` appears in the console's archive list like any other; move it wherever you like (a share, OneDrive, an email, a USB stick), or point a command at it:

```
connections-export open my-export.zip
```

Both zip layouts work: the archive's files at the top of the zip, and the archive nested under one folder — which is what Windows' "Send to → Compressed folder" and the **Save as .zip** button both produce. You do not have to know which one you have.

**A directory is read/write; a `.zip` is read-only.** Both browse identically in the Reader, and both export to PDF, Obsidian and Jekyll. The one difference is that a directory can be _extended_: "Extend & update" can add a component it never captured, or bring it up to date, because it writes back into the folder. A `.zip` cannot be written into, so "Extend & update" refuses in words rather than appearing to run and landing nowhere — unpack it to a folder to add to it. So keep the working copy as a directory, and make a `.zip` when you want to store or send a finished snapshot.

**Reading a `.zip` in place is efficient** when the file is actually on the machine: the console reads it by random access — the two small index files when it opens, then images only as you view the pages that use them — so it never unpacks the whole thing or loads it into memory. Two cases are slower and worth knowing. An **online-only cloud file** (OneDrive "Files On-Demand") is downloaded in full by the operating system the first time any byte is read; a **live network share** turns each of those random reads into a network round-trip. For either, a large archive is quicker if you let it download once — copy it to a local disk, or open it from a link, which fetches it a single time — rather than reading it repeatedly over the wire. A `.zip` that is synced and available offline is just a local file, and none of this applies.

## <a id="man-communities"></a>Several communities in one archive

A run captures a **set** of communities, not one. Everything you tick goes into a single archive, and that is what makes the links between them survive.

### Why one archive rather than several

A link is rewritten to point inside the archive when its target is _also in this export_. So capturing community A on its own leaves its links into B as dead references that only resolve while the deployment is reachable; capturing A and B **together** makes those same links resolve inside the archive, with no extra work. Exporting B separately and keeping both folders looks equivalent and is not — it gives you two archives that each know the other's content only as an outside reference.

### Adding communities

Identify one community as usual. Underneath its components the console offers any **sub-communities** it can see, _by name_, each with its own Add — or add all of them at once. A community you merely know about can be added by pasting its URL or uuid, which is the only way to reach one that is related to your work but not related in the system.

Every added community gets the same component list as the first: its wikis, blogs, forums, files and Highlights, each with a count, each independently tickable, with its own Select all. Nothing is privileged about the community you started from. A community with nothing ticked is simply not captured — unticking everything _is_ how you remove it, so there is no second control to disagree with.

### Sub-communities are a convenience, not the feature

Discovering a community's children saves you finding them, and the parent/child relation is worth keeping as a fact about the set. But it is not what the feature is built on: people also keep communities that are simply **related** — a programme and its workstreams, a department and its projects — with no parental relation recorded anywhere, and they link across those exactly as liberally. A child is an ordinary community here in every other respect.

### What you see, and what it costs

The list of sub-communities is the one _you_ can see. A restricted community requires membership to read, and one you cannot reach is not in your export — the same contract as everywhere else in this tool: an export contains what you can read.

Each community is a separate discovery and a separate crawl, so five communities is roughly five times the work of one. The estimate above the Start button counts the whole selection before you begin.

### Reading the result

During the ingest, the live tree groups what arrives **under the community it came from** rather than as one flat list, and the verdict at the end breaks its counts down per community as well as per app. The reader groups its navigation the same way. An archive of a single community reads exactly as it always did, with no heading above it repeating what the title bar already says.

The archive is named after the first community you added, which is a guess rather than a decision — the archives list lets you rename it, and the new name is written beside the data rather than moving the directory, so every reference to it keeps working.

## <a id="man-update"></a>Adding to an archive later

An archive is not finished when the run ends. As long as the original system is still reachable you can come back to one and do two different things — often in the same visit:

- **Extend** — capture a component you left out. The wiki you took in March, plus the community's Files this time.
- **Update** — pick up what has changed since you last captured.

**Nothing is ever duplicated.** A re-captured item replaces its own earlier copy; anything untouched is left exactly as it was; and nothing already captured is removed, even if it has since been deleted on the original system. An archive is a record, not a mirror.

**Links between components heal.** If you captured a wiki in one visit and its community's blog in another, links from those wiki pages to blog posts start pointing inside the archive as soon as the blog is in it — you are not penalised for having decided in stages.

### What an update costs depends on the component

The original system offers different help for each, and the difference is large enough that it is shown to you before you start rather than explained afterwards:

- **Blogs and Ideas** can be asked directly for what changed since a date, so new and edited posts cost a handful of requests. **Comments need a second question**, because adding a comment to a post does not count as changing that post and does not move it in the dated feed. Each blog is therefore also asked which _comments_ changed — one request per blog — and only the posts named that way have their discussion re-read. A post whose comments grew is fetched even when the dated feed leaves it out, which is how a conversation on a years-old post still reaches your archive. A reply to another comment costs no more: the post it belongs to is found from what your archive already holds.
- **Forums** can be asked the same question, and here a reply _does_ move its topic, so the dated feed returns exactly the topics with new discussion. Their replies are re-read; every other topic is left alone.
- **Wikis** cannot be asked. The wiki's own page list is re-read instead, and that list carries every page's date — so a single request says which pages moved, however many pages the wiki has, and only those are opened again. A comment moves a page's date here, so new discussion arrives through that same comparison at no extra cost. A page whose date has not moved is skipped entirely: text, history, comments and images are all left in the archive. You can also simply leave a wiki as it is and update the rest.
- **Files** compare each document's date; unchanged documents are not downloaded again.
- **Highlights** re-reads the community's layout every time — it is small — and fetches a page's body only when that page's own date has moved.

### The date an update works from

It is taken from the archive itself — when its last successful capture _started_, minus a minute. The start rather than the end, because an item edited while that capture was running would otherwise fall into the gap between one run finishing and the next one beginning. The spare minute covers small clock differences between your machine and the original system. Both choices can only cause a little re-checking; neither can lose anything. You can set a different date if you want, and an earlier one always re-checks more, never less.

### <a id="man-recheck"></a>New comments on old content

Asking a system “what changed since Tuesday” returns the items that _changed_, and someone adding a comment to a two-year-old post may not count as changing it. Whether that loses anything depends on the component:

- **Wikis** — a comment moves the page's own date. A plain update sees it.
- **Forums** — a reply moves its topic, and the dated feed returns that topic. A plain update sees it.
- **Blogs and Ideas** — a comment moves neither the post nor the dated feed, so each blog is asked which _comments_ changed as well. An update sees it; there is nothing to switch on.

**“Also re-check comments and replies”** re-reads comments for every item whether or not the item looks changed. Nothing above needs it — it is there for a deployment that behaves differently, and it costs roughly one extra request per item, which on a large wiki is thousands. It is wikis and forums it covers; a blog is asked its separate question either way.

### <a id="man-repair"></a>Repairing a blog archive from an older release

A release before this one read blog **comments** starting from the second page of each comment feed, because it assumed those feeds were numbered from 1. HCL numbers them from 0, so any comments on the first page were missed — silently, with no error. Blog **posts** were unaffected; only their comments, and only on blogs, were short.

**You do not need to recapture anything.** If any archive was captured with an older release, the console tells you the moment you open **Archives**: a banner across the top says how many archives may be missing blog comments and offers a single **Repair all** button. You do not have to find the affected archives or confirm a count — one click repairs exactly the ones that need it. The same banner then becomes a live status line: “Repairing archive X of N — recovered K comments so far…”, and when it is done it reports how many previously-missing comments were recovered and that **every archive is now up to date**. Repair re-reads only the comment feeds — from page 0 this time — into the archives you already have. It adds the missing comments and touches nothing else; posts, images, other components and every other archive are left exactly as they were, and it is safe to run again. A **Stop** button beside it halts a repair that is taking too long: the archive it is on keeps what it already fetched, the remaining archives are simply not started, and running Repair again picks up the rest.

From the command line the same repair is:

```
connections-export crawl --repair --into PATH\TO\ARCHIVE
```

`--repair` needs `--into` and takes nothing else — no URL, no `--component`. What to re-read is taken from the archive’s own record of what it captured and for whom, so it repairs precisely the blogs (and author scope) that are in it, against the same deployment, with no date cutoff. It is safe to run more than once.

**How the archive is recognised as needing repair.** A capture records which version of each component reader produced it; a blog captured before the fix is marked with the older reader and is what the offer keys off. An older archive from before that record existed is recognised instead from its own request log — a blog whose comment feeds were never read from page 0 — so the offer is made whenever completeness cannot be confirmed, and never withheld on a guess. An archive captured by this release or later is never flagged, because its comments are already complete.

## <a id="man-pacing"></a>Pacing, and running overnight

An export is thousands of requests against a live deployment. It waits **1 second between them** by default.

For anything large, **3 seconds overnight** is the kinder choice. It is the same number of requests either way, and a slower export is one nobody has to notice — no rate limiting, no puzzled admin, no crawl competing with people trying to work. The control is on the Output step, and as a default under Settings.

The pacing is for the export. Working out what a community holds — the dozen or so reads behind the component list on Select & Tailor — is not paced, and is made through one signed-in session rather than one per read. While it runs the console says which phase it is in and how many requests it has made, and it gives up only after **two minutes of silence**, not two minutes of work: a slow deployment that is still answering is still answering.

## <a id="man-proxy"></a>Proxies

The tool reaches the deployment the way your browser does, and it never guesses. It decides **per address**, in a fixed order of authority, and it can always be told explicitly. It does **not** assume a proxy is unnecessary, and it does **not** assume one is required — it reads your organization’s own configuration and does what that says.

**The order of authority.** Whichever of these first has an answer for the address wins:

1. **What you told it** — `--proxy <url>` (or `--proxy direct` for no proxy) on the command, or `proxy = "…"` in `connections-export.toml`. This always wins; it is how you settle any case the automatic steps get wrong.
2. **On Windows, your system’s proxy policy** — the **PAC script** or automatic detection (WPAD), evaluated *for that specific address* by the same Windows engine (WinHTTP) your browser uses, and then the static proxy setting from Internet Options with its bypass list.
3. **The environment** — `HTTPS_PROXY` / `HTTP_PROXY`, honouring `NO_PROXY`.
4. A **direct** connection, when nothing above has anything to say.

**Why the PAC comes before the environment on Windows.** A PAC script is your organization deciding, address by address, what needs a proxy and what does not — an internal server DIRECT, a cloud service through the proxy, whatever your network actually is. A browser obeys it and ignores proxy environment variables entirely. This tool does the same, because a single `HTTPS_PROXY` variable is a blunt instrument: it applies to *everything* unless a matching `NO_PROXY` carves out the exceptions, and a machine that has one set globally (for general web access) will otherwise send even an internal deployment to a proxy that cannot reach it. Letting the PAC lead means the right thing happens without you having to curate `NO_PROXY`. On systems with no PAC (Linux, or Windows without one) the environment leads, as those conventions expect.

**When it cannot tell.** If a PAC script fails or times out, the tool does **not** quietly assume “no proxy needed” — a host may genuinely need one. It connects directly *and says so*, and if that direct connection then fails, the error names the possibility that a proxy is required and points you at `--proxy`. Uncertainty is surfaced, never hidden behind a silent guess.

**The one unconditional rule** is about the tool talking to *itself*, not to your deployment: requests to `localhost` / `127.0.0.1` — the local console — always go direct, whatever any script says. A PAC that routes loopback to a proxy exists, and a proxy that refuses it is behaving correctly, so the console’s own traffic is never offered to one.

**One decision, every connection.** The proxy the tool settles on governs *all* of its outbound traffic to the deployment — the crawl, the metadata lookups, and the Windows sign-in handshake alike. (Earlier the sign-in step read proxy environment variables on its own and could disagree with the rest; it no longer does.) Your corporate TLS certificate bundle (`REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`) is still honoured throughout.

**Proxy sign-in.** If a proxy demands its *own* credentials (a 407), the tool cannot supply them and says so plainly rather than retrying. The fix is the one a network team would give anyway — have the deployment’s host added to the proxy bypass list; a deployment on the local network usually is already.

**Seeing the decision.** `connections-export probe proxy` prints, for the deployment and for the console, exactly what a request would go through and which step decided it — including “undetermined” when a PAC could not be evaluated. A report that begins “proxy issues” becomes a fact with that one paste. Every run also logs its proxy decision when it is anything but a plain direct connection.

## <a id="man-cli"></a>The command line

Everything this console does, it does by calling the same code a command can call directly. That matters when nobody is sitting in front of it: a capture that runs overnight from a scheduled task, a PDF rebuilt after a stylesheet change, an archive opened on a machine with no browser to hand.

Run `connections-export` with no arguments and you get this console, in a browser. Every command below takes `--help`.

| Command | What it does |
|---|---|
| `crawl` | Capture a deployment into an archive. Takes the same URL you would drop onto the setup screen. |
| `serve` | Start this console. `--open` opens a browser at it. |
| `open` | Open an existing archive or package to read and export — no capture, so no deployment needed. |
| `pdf` | Render an archive or package to PDF. |
| `ingest` | Reconstruct a capture into another tool: an Obsidian vault, or a Jekyll site fragment. |
| `package` | Write a capture’s portable interchange package, for an ingester of your own. |
| `style` | Show or dump the PDF stylesheet, and list every setting in it. |
| `licenses` | What is inside this build and under what terms, with the licence texts. |
| `probe` | Ask a live deployment a question that cannot be answered without one — or, with `proxy`, what a request would go through and why. |
| `compare-author` | Compare what an author filter keeps against what the deployment’s own search returns. |

### <a id="man-cli-connection"></a>Saying which deployment, and how to sign in

The commands that talk to a deployment share these. All four have a configured default (see **Settings**, or `connections-export.toml`), so you usually pass none of them.

| Option | What it does |
|---|---|
| `--base-url` | The deployment root. |
| `--auth-mode` | `sspi` (Windows sign-in), `kerberos`, `basic`, or `paste_token`. |
| `--auth-root` | Which authentication path the deployment serves its API under — `basic` unless yours differs. |
| `--config` | A configuration file other than the one found automatically. |

### <a id="man-cli-capture"></a>Capturing

`connections-export crawl <url>` — the URL is what to capture, exactly as on the setup screen: a wiki, a blog, a forum, or a community.

| Option | What it does |
|---|---|
| `--output-dir` | Where the archive goes. |
| `--component` KIND | Capture only these parts of a community. Repeatable; without it you get all of them. |
| `--author` | Keep only what this person wrote or took part in. |
| `--search-userid` | A user id the deployment’s Search knows. With `--author` and a community URL, the community’s forums are chosen by asking Search which threads this person is in, rather than reading every topic and reply to find out. See [Capturing one person’s share of a community](#man-one-person). |
| `--proxy` | A proxy URL, or `direct`. Unset, decided the way a browser decides — see [Proxies](#man-proxy). |
| `--delay` | Seconds between requests. See [Pacing](#man-pacing) — 3 overnight is the kinder choice. |
| `--max-entries` | Stop after this many items. For a look before committing to the whole thing. |
| `--into` ARCHIVE | Add to an existing archive instead of starting one. This is what makes the run an [update](#man-update), and the cutoff comes from that archive’s own record of when it last ran. |
| `--since` | Override that cutoff. Earlier always re-checks more, never less. |
| `--recheck-comments` | Re-read comments for every item, changed or not. [Nothing needs it](#man-recheck) on what has been measured. |
| `--demo` | Capture the built-in synthetic deployment. Needs no URL, no credentials and no network — everything it reads ships inside the program. Naming a URL as well is refused rather than one of them being ignored. |

### <a id="man-cli-reading"></a>Reading, printing, converting

These need no deployment: they work from an archive or a package.

| Option | What it does |
|---|---|
| `open --archive DIR`<br>`open --package DIR` | Serve one for reading. `--host`/`--port` if the defaults clash. |
| `pdf --archive DIR`<br>`pdf --package DIR` | Render it. `--output` names the file. |
| `pdf --fidelity` | Render through the original system’s own stylesheets instead of the portable ones. Needs the deployment. |
| `pdf --css FILE` | Your own stylesheet, appended after the captured pages’ own CSS so it wins. |
| `ingest --archive DIR --output DIR` | Reconstruct a capture into a developer format: `--format obsidian` writes an Obsidian vault, `--format jekyll` a Jekyll site. `--package` takes a package instead of an archive. |
| `package --archive DIR --output DIR` | Write the portable interchange package for a capture — for an ingester of your own, or to hand to someone who has never seen this tool. |
| `style --dump` | Write the whole stylesheet out to edit. `--marks` lists the header and footer settings. |

Every one of these also takes `--author`, so a filtered reading, PDF or vault can be produced from an unfiltered archive without capturing again.
{: .foot-note }

## <a id="man-style"></a>Changing how the PDF looks

The PDF stylesheet is built on a small set of **design tokens** — type sizes, line height, contents-list sizes, colours. Those are the supported surface: they keep working across versions, where the rules beneath them may be rearranged.

Set them in `connections-export.toml`:

```
[pdf_style]
body_size = "11pt"
font = "Georgia, serif"
toc_title_size = "18pt"
```

`connections-export style --tokens` lists every name.

The quickest way to try one is **Settings → PDF appearance**: change a value, press Preview, and a real two-page sample is rendered with it. Each field shows its default, so an empty one means unchanged.

The same card has a **Render timeout (seconds)** — how long the browser may take to lay out and paginate a document before an export gives up (120 seconds by default). Raise it for a very large PDF, or one with slow-loading images, that would otherwise time out; lower it if you would rather a failing export failed sooner. When an export does hit the limit, the console says so and writes a `pdf-failure-*.md` report naming what did not finish, rather than failing silently.

For anything the tokens do not cover, do not guess at class names — have the program write its own stylesheet out, edit that, and pass it back:

```
connections-export style --dump --output mystyle.css
connections-export pdf --css mystyle.css --archive ./archive --output export.pdf
```

### <a id="man-marks"></a>The running header and footer

What appears in the margins of every page is configured separately from the stylesheet — the two renderers build their margins by different mechanisms, so this is a set of named fields rather than markup you write.

Six text slots, three at the top of the page and three at the bottom:

```
[pdf_marks]
header_left   = ""
header_center = ""
header_right  = ""
footer_left   = "{section}"
footer_center = ""
footer_right  = "{page} / {pages}"
```

Inside any of them, these stand for something:

- `{page}` — the page number
- `{pages}` — the total page count
- `{title}` — the document title
- `{date}` — the date the PDF was made
- `{section}` — where in the document this page is: the wiki, blog or forum and the page you are on
- `{logo}` — the community's own picture, if one was captured. It comes from the archive rather than the live system, so a PDF made from an old archive still shows it, and it resolves to nothing at all when there is none. Drawn at `logo_size` (6mm by default; these are 155×155 natively) and circle-cropped like the source system shows them — set `logo_shape = "square"` if you would rather it were not

Everything else is literal, so `footer_right = "Confidential — {page}"` does exactly what it looks like, and an empty slot prints nothing.

**One honest limitation.** `{section}` is exact in the portable PDF, which paginates the document itself and so knows which section each page fell in. The **Live PDF** renders its margins outside the document and cannot know, so there it falls back to the document title — the most specific thing it can name. Every other placeholder is identical in both.

Five more fields shape the marks rather than fill them: `mark_size` (e.g. `9pt`), `mark_color` (e.g. `#555`), `mark_font` — left empty it follows the document, which is usually what you want — and `header_rule` / `footer_rule`, a CSS border such as `1px solid #ddd` drawn under the header or above the footer.

#### When the slots are not enough

A title between two rules, a mark on every page, two lines instead of one — for those, hand over a whole band of your own HTML with `header_html` or `footer_html`. It replaces that band's three slots, and the placeholders work inside it exactly as they do in a slot:

```
[pdf_marks]
footer_html = """
<hr style="border:0;border-top:1px solid #999">
<div style="text-align:center"><b>{title}</b> — {page} of {pages}</div>
<hr style="border:0;border-top:1px solid #999">
"""
```

**One difference between the two renderers.** The Live PDF fetches its margins outside the document, so an image referenced by URL will not load there — an icon has to be an inline `<svg>` or a `data:` URI. The portable PDF has no such limit and will load anything.

To see all thirteen with their current values, in a form you can paste straight into `connections-export.toml`:

```
connections-export style --marks
```

Or set them in **Settings → PDF appearance**, under _Header and footer_, and press Preview to see a real two-page sample before committing to anything.

Your stylesheet is appended **after** the captured pages' own CSS, so it wins. Worth knowing before you wonder why a rule has no effect: a captured page's own styles outrank ours, which is why yours has to come last.
