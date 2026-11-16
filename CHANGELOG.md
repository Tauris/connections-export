# Changelog

Notable changes to `connections-export`, newest first. Versions follow
[semantic versioning](https://semver.org/). Since 1.0.0 the archive format
changes incompatibly only with a new major version; the format's own version
is recorded inside every archive.

## 1.0.1b2 — 2026-11-16 (beta)

Fixes for what 1.0.1b1 showed on a real forum thread. Archives captured with
1.0.1b1 are not flagged; capture an affected thread again with this beta.

### Fixed

- **An uploaded document is listed once, on its own reply.** A thread's page
  links a document relatively and labels it "Download", while the feed names
  it by its full address; the two were taken for different files, so the
  document appeared twice, once named "Download". And in a single-thread
  capture, where the topic and its replies share one page, a document the
  page did not attribute was put on the topic. Download addresses are now
  compared without their host, a link's text is a filename only when it looks
  like one, and a document goes to the post its download address names.
- **Attachments are saved under their names.** Saving an attachment from the
  Reader produced a file named after its hash, with no extension. It now has
  the attachment's filename; an image pasted into a post, which the source
  does not name, is listed as "Pasted image" and saved with the extension its
  type implies.

## 1.0.1b1 — 2026-11-16 (beta)

A beta of a fix for Forums attachments, and of a way to capture an affected
archive again without selecting everything anew. Install it with
`uv tool install --force --refresh --reinstall https://github.com/Tauris/connections-export/archive/refs/heads/beta.zip`.

### Added

- **Capture again.** Every archive on the Archives page offers *Capture
  again*: a new capture with the same deployment, components, community and
  author scope the archive recorded, with no date cutoff, into a new archive.
  What will be captured is shown before it starts, and the existing archive is
  left exactly as it is.
- **Archives from an older release say what they are missing.** An archive
  whose forums were captured before this release carries the note *Forum
  attachments may be missing*, and a notice on the Archives page explains the
  problem and offers *Capture again* for each. Once the new capture has
  finished, the old archive names it instead.

### Fixed

- **A forum reply's uploaded document is kept.** A reply with a pasted image
  and an uploaded file (a Word document, say) kept only the image. Uploaded
  files are `/forums/ajax/download/...` links named in their address and
  shown on hover, and these were not recognised as attachments. Every
  download link a topic or reply names is now an attachment, named by its
  title or its address.
- **What a post's page lists is part of the archive.** The page of each topic
  and reply is read, once per page, and the attachments it lists are added
  to the post they belong to. These were used before only to recover names,
  and attachments found only there were downloaded but not shown in the
  archive or the PDF. On a thread page showing several posts, each post gets
  only its own attachments.

## 1.0.0 — 2026-11-16

The first stable release. Everything from the 0.2.0 betas, listed below, in
one version: large archives exported to PDF in parts with a constant memory
footprint, several PDFs split along communities and apps, content fitted to
the printed page, a new cover, and an Export dialog and activity tray in the
console. This version has been used to export single PDFs of more than 6,000
pages from a real archive.

**Fitting content to the page is a best effort.** Pages written for a
scrolling browser window are reflowed, stacked or scaled so that nothing is
cut off, and the manual lists every step ("How content is made to fit the
page"). How well that works depends heavily on how a page was laid out, and
no set of rules can anticipate every layout an author has invented. The
content itself is never changed; the original page, as captured, stays in the
archive and in the Reader.

**From 1.0.0 on, the archive format is stable within a major version**: any
1.x reads every archive written by a 1.x, and a change that would break that
comes with 2.0.

### Added
- **A banner.** The console's Overview and the README open with the
  connections-export banner: content from Connections kept safe in an archive
  and turned into PDFs, notes and sites.
- **A PDF of any size is assembled without holding it in memory.** Joining
  the parts of a large PDF -- and adding bookmarks and attachments to any PDF
  -- used to take two to three times the size of the result in memory (858 MB
  for a 412 MB PDF). The export now copies Chromium's output straight from
  file to file and rewrites only the small pieces it has to: the same 412 MB
  PDF is assembled in 55 MB, whatever its size. The finished PDF is written
  straight to its file rather than handed around in memory.
- **A welcome, a "what's open" bar, and archive details.** On a first run (no
  archive open and none on this computer) the Overview opens with three clear
  starts: *Capture from Connections*, *Open an archive someone gave me*, and a
  labelled *Try it with demo data*; "Not now" is remembered. A bar at the top
  of every screen names the archive the Reader and Export act on, with how
  many items it holds and when it was updated, and a *Switch archive* list.
  Demo archives wear a "Demo data" badge there, on their cards and on their
  details page. Every archive card has a **Details** button: what is inside
  (communities, subcommunities, apps and wikis, blogs, forums, each with its
  items and "about N pages, about M MB as PDF"), the largest items, when it
  was captured, and Read, Export and Extend or update.
- **Help where you need it.** The manual now starts with "How do I…" (capture a
  community, open an archive someone sent, export one PDF or several, find
  exports, update later, what to do if an export runs out of memory), and the
  Export dialog, the Settings sections and archive details have a "?" that
  opens the manual at the right place.
- **An activity tray.** A small "Nothing running" / "PDF 42%" indicator at the
  foot of the sidebar shows what is running -- a capture (with a link to its
  live view) and PDF exports with progress, "File k of n", time so far, an
  estimate of the time left when it can be worked out, and Cancel. Reload the
  page, or open a second tab, and a running export is picked up again where it
  is.
- **Finish notices.** When a PDF you started finishes while you are in another
  tab, the tab title says so, and, if you opted in once ("Notify me when long
  exports finish"), the browser shows a notification.
- **Recent exports** on the Archives page: each PDF, ZIP or set of PDFs saved
  beside your archives, with when it was made, Download again and Show in
  folder.
- **Copy details** on error messages copies what support needs (the raw
  answer, the request, the time and the version).
- **One Export dialog for every format.** "Export..." on an archive in the
  Archives list and in the Reader's Export menu open the same dialog: the
  whole archive, chosen components or the open item, as one PDF, several PDFs
  (split automatically, per community, per app or per container, with optional
  page and size limits per file) or an Obsidian, Jekyll or Hugo export. It
  shows the proposed files and an estimate of pages, size and time before
  anything is made, warns when the machine is short of memory or disk, and
  remembers your choices per archive. When it finishes, the files are already
  saved beside the archive; the panel offers Download and Show in folder.
- **PDF exports are saved beside their archive.** Each export is written to a
  folder named after the archive with `-pdf` added (the archive list knows it
  is not an archive), and downloaded from there -- a large export no longer
  has to fit in memory or survive a browser download alone. The console can
  show the folder in your file manager.
- **Export a PDF straight from the Archives page.** Every archive in the list
  has its own Export PDF button, with the same choices as the Reader (comments,
  external images, open with bookmarks). It makes the whole archive's PDF with
  the same progress panel and Cancel, without opening the Reader first.
- **A captured community now records which community it sits under.** Each
  captured community's own entry is archived, and a subcommunity's link to its
  parent is read from it, so an export knows the community tree -- the basis
  for splitting a PDF along a parent and its subcommunities. Archives made
  earlier simply have no parent recorded.
- **Large PDF exports are laid out in parts and joined into one document.**
  The PDF used to be laid out in one piece, and the browser's memory grew with
  it until a large archive ran out (5 GB for 3,300 pages). Above the *PDF
  part size* the export is now laid out part by part and the parts joined:
  page numbers, "page N of M" footers, the running section name, the table of
  contents, bookmarks and every link -- including links from one part into
  another -- read exactly as in a one-piece PDF. The same 3,300 pages now need
  1.8 GB and take under 3 minutes instead of 16. A part that still runs out of
  memory is split smaller and tried again; only an item too large on its own
  stops the export, and the message names it.
- **PDF export shows how far it has got, and can be cancelled.** Exporting a
  large archive used to show only a spinner and a clock for many minutes. The
  console now keeps a small panel in the corner (and the percentage on the
  Export button) with a progress bar by pages, what is happening, "Part 2 of
  5" when the export is laid out in parts, the time so far, and a Cancel
  button. `connections-export pdf` prints the same steps to the terminal.
- **Large PDFs adapt to the memory your machine has free.** At the start of
  each export the tool reads how much memory is free and lays the document
  out in parts that fit comfortably, so nobody has to tune a number. *PDF
  part size (MB)* (Settings, `--part-size-mb`, `pdf_part_size_mb`) is empty
  by default; set it only to force a size, or 0 for one piece.
- **An export says what could go wrong before it starts.** Not enough disk
  space, no usable browser, or very little free memory are reported before
  the export begins, not minutes into it.
- **Running out of memory is now said plainly.** When the browser runs out of
  memory the export no longer just stops: the console shows a "Not enough
  memory" message saying what to try, and the command line prints it and exits
  with an error instead of a traceback.

### Changed
- **Screenshots side by side are printed one after another, at full width.**
  A table whose pictures its columns would squeeze below 60% of their own
  width -- a German column beside an English one, each with screenshots --
  prints its cells one after another. Tables of text, or with small pictures,
  stay side by side. The manual lists it among the fitting steps.
- **A new cover.** The first page is named for what the export contains --
  the community, the wiki, blog or forum, or the system it came from --
  and lists what is inside with the number of pages, posts, topics and
  files, the source and the date. It shows the community's picture when the
  archive holds one, and carries no running header or footer. The PDF's own
  title is the same name. Two new style tokens, `accent` and
  `cover_title_size`, set its colour and title size.
- **A design pass over the console.** Message dialogs now have one clear
  "Close" button instead of both "Close" and "OK"; the largest items on an
  archive summary keep their container names' capitals; a faint shadow no
  longer shows along the right edge of every page; Settings fields match the
  rest of the console; and the paste box on Archives fits a phone screen.
- **The "what's open" bar updates when you export from an archive card.**
  Opening the Export dialog from the Archives list now names that archive in
  the bar instead of saying "No archive open". "Download again" in Recent
  exports looks like the button it is, and the paste box on Archives matches
  the other fields.
- **Settings are split into everyday and Advanced.** Sign-in method, the PDF
  time limit, PDF part size and small external images moved under a collapsed
  "Advanced" section, each explained with an example. The PDF part size can be
  left empty, meaning automatic (from free memory), and saves that way.
- **One PDF** now says the name of the file it will make.
- **Error messages say what happened and what to try**, without jargon: failed
  PDFs, a missing browser, archives that will not open, a capture that will not
  start or sign in, bad paths and links, zip and delete failures. A capture
  that the console refuses to start now says why instead of showing an empty
  run.
- **Attachments are listed in the PDF, not carried inside it.** Each page
  attachment used to be a link whose address was the whole file, so every
  attachment of the export passed through the browser's memory and ended up
  in the PDF inside a link most viewers cannot open -- enough, on a large
  archive, to exhaust the machine. The PDF now lists each attachment with its
  size; the files themselves are in the export package. Asked to, the export
  attaches them to the PDF as real file attachments instead, which viewers
  show in their attachments panel.
- **A PDF export keeps its images on disk, not in memory,** while the browser
  lays the document out.
- **The demo's content now links across the whole export.** Every wiki page,
  blog post, forum topic and Highlights page carries a short "Related
  elsewhere in this community" paragraph linking to content far away in
  document order: across wikis, and between the wiki, blogs and forums, with
  the first container linking to the last and back. A large export rendered in
  separate parts can now be checked for links that survive being stitched
  together.

### Fixed
- **A table keeps its columns over a page break.** Each copy of a continued
  row worked its columns out again from what it held, so a column whose text
  had ended collapsed and the other took the whole width -- a screenshot in
  it at a quarter of the page on one page, full width on the next. Column
  widths are now fixed before the pages are made.
- **Nothing is lost where a page breaks.** The engine that divides a PDF into
  pages resumed after a break by searching for the text it had just printed,
  and took the first place it was found. In a paragraph or table cell whose
  text runs between screenshots, links or bold words, the same words often
  occur earlier, so a page could resume at the wrong place: content was
  printed twice and what followed it never, sometimes the whole second column
  of a two-column table. The engine is corrected as it is loaded, and the
  laid-out text is checked against the source in the tests, character for
  character.
- **A page's print styles no longer widen it after it has been fitted.**
  Pages are laid out as they are printed, in print mode and at the width of
  the sheet, so styles a page has for printing, or for a narrow window, are
  in effect while it is fitted to the page.
- **Tables whose cells ask for more width than the table no longer run off the
  page.** When a table would not fit, the widths its author gave the table,
  its columns and its cells are set aside and it lays out to the page. A box
  placed far to one side goes back into the text, a picture in a narrow cell
  is shrunk instead of the table around it, and whatever still cannot fit is
  scaled down as a whole, with no lower limit -- and a last check on every
  laid-out page shrinks anything that still reaches past it. Nothing is cut
  off. The manual describes each step ("How content is made to fit the page").
- **The archives folder is remembered.** It was the one setting kept only
  while the console ran, so every restart -- an update most visibly -- pointed
  it back at the default folder. It is now saved with the other settings; if
  the saved folder is missing at start-up (a disconnected drive), Settings says
  so instead of quietly showing the default. Settings also says when a typed
  folder has not been saved yet, and saving other settings leaves it alone.
- **`just console` starts on Linux without Kerberos headers.** Signing in as
  yourself compiles Kerberos there, and without the system's headers the
  install failed and the console never started. It now starts without that
  part and says how to add it (`sudo apt-get install libkrb5-dev`); demo data
  and archives need no sign-in.
- **Phone-width screens.** Archive cards no longer run off the side, and the
  sidebar shrinks to icons on a narrow window so the page keeps its room.
- **Starting the console no longer ends on what looks like an error.** The
  browser asks for a site icon on its own, and the console had none, so the
  last line in the terminal was a "404 Not Found" for `/favicon.ico`. Where
  the browser had opened behind other windows, that line was all anyone saw,
  and it read as the start-up failing. The console now has an icon, and once
  the browser is opened the terminal says so plainly: the console is
  running, it may be behind other windows, here is its address, keep this
  window open and press Ctrl+C to stop. If no browser could be opened, it
  says that and gives the address to open.
- **Links between apps work in the PDF.** A link from a wiki page to a blog
  post, a forum topic, a Highlights page or a captured file was resolved
  correctly, then written into the PDF as a jump to a wiki page that does
  not exist, so it went nowhere. Each now lands on its target; a link to
  something outside the exported selection keeps its address instead.

### Documentation
- **How to update, and why an old version can keep starting.** The README has
  an *Updating* section: the command for each way of installing, how to try a
  beta, and the usual reasons the old version still starts -- a console left
  running, `uv run` starting a project's own locked copy instead of the
  installed tool, a beta install split over two commands, another copy found
  first. The beta pre-release notes give the same one-line install.

## 0.1.17 — 2026-09-29

### Fixed
- **Large PDF exports no longer stop after 30 seconds.** Every image went
  into the document the browser loads as text, and loading grows far faster
  than the document: 50 MB of images took most of 30 seconds, 150 MB crashed
  the browser. Loading also had a fixed 30-second limit, so an ordinary
  community with a few dozen photos failed with "Timeout 30000ms exceeded",
  external images on or off, whatever the render timeout said. Images are
  now handed to the browser on request, straight from memory -- nothing
  goes over the network -- and the render timeout covers every step:
  loading, laying out and printing. And it now measures a stall, not the
  whole job: laying out continues as long as new pages keep appearing, and
  printing is allowed extra time for every page, so an archive of any size
  finishes, while a stuck export still gives up after the timeout (120
  seconds by default).
- **"What to export" shows its choices on the Export menu itself.** It was a
  dropdown inside the menu, and a dropdown opens a popup of its own, which
  some setups close the moment it appears -- a Linux browser shown on Windows
  through WSLg among them -- leaving only "Whole archive" in sight. Whole
  archive, the open item and Choose components are now three buttons you
  can see at once.
- **Under WSL the console opens your Windows browser.** Python looks for
  Linux browser helpers, which a WSL distribution usually lacks, so the
  console only printed its address. Under WSL it now opens it with `wslview`
  when installed, or `explorer.exe`, which uses the Windows default browser;
  `$BROWSER`, the Linux convention for choosing one, still wins.

### Added
- **The macOS executables are checked for the macOS they need.** Every
  native part declares the oldest macOS it runs on; the build now fails if
  any requires more than macOS 11, except Playwright's browser driver, which
  needs macOS 13.5 — now documented as the minimum for PDF export on a Mac
  (a `pip` install included). A dependency moving its wheels to a newer
  macOS can no longer raise the requirement unnoticed.

## 0.1.16 — 2026-09-28

### Fixed
- **The Linux executable signs in on older distributions.** It was built on
  Ubuntu 24.04, and the Kerberos libraries it carries needed glibc 2.38: on
  Ubuntu 22.04 — or a company WSL — the program started, but signing in failed
  with "GLIBC_2.38 not found". It is now built on glibc 2.28 and runs on
  Ubuntu 20.04, Debian 10, RHEL 8 and newer; every build is checked on a bare
  AlmaLinux 8 with no Kerberos of its own installed, so the executable must
  load the libraries it carries. glibc itself is never carried — the
  executable uses the system's own; the Kerberos libraries it does carry are
  brought to the distribution's latest security-patched release at every
  build, and the build log names their versions.
- `probe sign-in` inside an executable no longer suggests a `pip install` when
  its sign-in libraries fail to load — there is nothing to install; it asks
  for a report instead.

## 0.1.15 — 2026-09-28

### Added
- **The PDF has bookmarks.** Every wiki, blog, forum, file library and
  Highlights area, with its pages, posts and topics beneath it — a wiki's
  page tree nested as it is — each leading to the page it starts on; the PDF
  opens with them showing in the viewer's sidebar. Only the document's own
  structure: no "Comments" or "Replies" labels, and none of the headings
  authors wrote inside their pages.
  Whether a viewer opens with the bookmarks panel showing is a choice — "Open
  with bookmarks showing" in the export menu, `pdf --open-bookmarks` — off by
  default, since some viewers lay the panel over the page.

### Changed
- **Signing in as yourself works out of the box, on every platform.**
  *Why:* signing in with the session you already have is the default and
  the way most people use the tool — but outside the Windows executable it
  took extra steps. A `pip install` needed the `[sspi]` extra first; on a
  Mac that meant a Kerberos library that some environments could only get
  through a conda setup; and on macOS and Linux the default method still
  asked for Windows' SSPI and failed at once ("SspiAuth requires
  requests-negotiate-sspi"). An export tool for people who are not
  administrators should not need any of that.
  *What changes:*
  - The default method, "sign in as yourself", uses your Windows session on
    Windows and your Kerberos ticket on macOS and Linux — the same single
    sign-on, through the system that has it.
  - A plain `pip install connections-export` includes it on Windows and
    macOS, where it installs from ready-built packages with nothing to
    compile.
  - Every executable includes it, the macOS and Linux ones too; their build
    refuses to finish without it, and each executable proves it loads before
    it is published.
  - On Linux, `pip` users who sign in with Kerberos still add the `[sspi]`
    extra: Kerberos has no ready-built packages there and compiles against
    system headers, and making it a requirement would break the install for
    everyone who does not need it. Existing `[sspi]` install commands keep
    working everywhere.
  - `connections-export probe sign-in` reports which sign-in an installation
    uses, whether it loads, and — on macOS and Linux — whether a Kerberos
    ticket is held.
  - *If `pip install` on a Mac tries to compile Kerberos* — which happens
    only where the package index offers no ready-built packages for it, such
    as a restricted company mirror — use the macOS executable instead, or
    install into an environment that already provides Kerberos (for example a
    conda environment with `krb5`).
- **`connections-export pdf` prints the same PDF as the console:** the paged
  renderer with the running footer, page numbers in the table of contents and
  bookmarks. It used the simpler renderer without them.

## 0.1.14 — 2026-09-26

### Fixed
- **Comments and replies keep their paragraphs and line breaks in the Hugo,
  Jekyll and Obsidian exports.** Connections often delivers comment and reply
  text as plain text with line breaks — the reason the PDF already turns them
  into breaks. The Markdown exports squashed a comment onto one list line and
  ran a reply's paragraphs together. A comment's text now sits under its list
  entry with its paragraphs and line breaks (a one-line comment stays on the
  line, as before), a reply keeps its paragraphs, and comment times read
  `YYYY-MM-DD HH:MM UTC` like reply times.

## 0.1.13 — 2026-09-25

### Added
- **Hugo: several communities, community first.** A Hugo export that holds
  more than one community — archives of different communities combined, or a
  capture that took in a sub-community — gives each community a folder of its
  own, `content/<community>/`, with an overview page (`kind: community`)
  listing its sections and how much each holds, and the wikis, blogs, forums,
  files and Highlights beneath it. Content in no community goes into
  `content/other/`, and two communities with the same title get distinct,
  stable folders. Links between pages, across communities and across combined
  archives, still resolve. The starter site follows: its front page and
  header list the communities, a community's page lists its sections, and
  breadcrumbs start with the community. An export of one community, or none,
  is unchanged. The idea is from Christoph Stoettner (pull request #5).
- **Hugo starter site: a list by default, cards on request.** The front page
  lists the communities with how many items each holds, and a community's
  page lists each section with its wikis, blogs, forums, libraries and
  Highlights beneath it; long names wrap and are shown in full. The list
  design follows Christoph Stoettner's proposal in pull request #5. The card
  design is still there: `--starter-layout cards`, "Front page as" in the
  export dialog, or `homeLayout` under `[params]` in the export's
  `hugo.toml` — switchable at any time without a new export. With several
  communities the header leads to the list and names the community you are
  in, instead of listing every one.
- **Hugo starter site footer** links the Connections server the content came
  from (reduced to its host, and left out when unknown) and credits
  connections-export and Hugo.

## 0.1.12 — 2026-09-25

### Added
- **Hugo exporter.** `connections-export ingest --format hugo` and the console's
  "Export Hugo content" write content for a Hugo site of your own: a `content/`
  tree with a section per wiki, blog, forum, file library and Highlights area,
  the wiki's page tree as nested page bundles (sibling order kept in `weight`),
  images and attachments as page resources, links between pages as `relref`s
  that Hugo checks at build time, and a README documenting every front matter
  field. No layouts or site configuration — those stay yours.
- **Page content as Markdown, HTML, or both.** Every exporter takes
  `--html markdown|mixed|html|raw` (console: "Page content as"). `mixed` writes
  each part of a page as Markdown only when that shows exactly what the
  original did, and as cleaned HTML otherwise; the export reports how many
  parts stayed HTML. `html` writes cleaned HTML; `raw` writes the HTML as
  captured, not cleaned. Links, images and template-syntax escaping are handled
  in every mode.

- **Combine several archives into one export.** `connections-export ingest
  --format hugo|jekyll|obsidian` accepts `--archive` (and `--package`) more
  than once and writes one Hugo content folder, Jekyll site or Obsidian vault.
  Anything held by more than one archive appears once, taken from the most
  recent capture — item by item, so a wiki holds every page any archive
  captured, each in its newest version; comments and replies travel with the
  copy kept. Links from one archive to content another holds become internal
  links. Every page records its `source_archive`, and the README (Jekyll:
  `sources.md`) lists the archives and, for each container held more than
  once, whose copy was kept. The same id from two different deployments is
  kept apart and reported. A single archive's export is unchanged.
- **Guided export on the Archives screen.** Tick one or more archives and
  press **Export…**: choose the archives, the format and how page content is
  written, preview exactly what will be written — counts, duplicates and whose
  copy wins, links that become internal, and the output folder — then export,
  with progress and what to do next.

- **Hugo starter site.** `connections-export ingest --format hugo --starter-site`
  (console: "Add a starter site", on by default in the export dialog) also
  writes a `hugo.toml`, a small set of templates and one stylesheet beside
  `content/`, so the export can be viewed with `hugo server`: a home page with
  every section, each wiki's page tree in its own order, blog posts and forum
  topics newest first, the file libraries, pages with author, date, tags and a
  link to the original, and a page per tag. Raw HTML is allowed only when the
  page content includes HTML. Off by default on the command line; `content/`
  alone is still what goes into an existing site.
- **Preview with Hugo.** When Hugo is installed, the export dialog can show a
  Hugo export with its starter site straight away: it runs your own
  `hugo server` on a separate local address, never through the console, and
  opens it in a new tab; "Stop preview" (or closing the console) ends it.
  Without Hugo the dialog says so and links to Hugo's installation guide —
  nothing is downloaded or installed.

### Changed
- **The export dialog goes step by step.** Archives, Format & options, Check,
  Export — one short step at a time, with the step shown at the top and Back,
  Next and Close always in view, so the dialog fits a laptop screen and the
  result is never below the fold. The dry run is called **Check**, so it is
  not confused with **Preview with Hugo**, the result's main button after a
  Hugo export with its starter site.
- **One way to export a developer format.** The Reader's "Export Obsidian
  vault… / Jekyll site… / Hugo content…" buttons open the same dialog for the
  archive you are reading, with that format chosen — so the Hugo starter
  site, the page-content choice and the Check are there too.
- **Hugo is looked for as soon as you choose it.** The dialog says which Hugo
  it found; or that Hugo is not on this computer's PATH (restart the console
  if you installed Hugo after starting it), with a link to Hugo's
  installation guide; or that Hugo was found but did not answer. The console
  gives `hugo version` 15 seconds — a first run on Windows can be slow while
  antivirus checks it — and remembers a Hugo that answered.
- The Jekyll exporter now defaults to `mixed`: tables with merged or coloured
  cells, sized images, figures and coloured text keep their layout as HTML
  instead of being flattened. `--html markdown` gives the previous output.
  Obsidian still defaults to Markdown, unchanged.

### Fixed
- **Exports are not listed as archives.** Hugo content folders, Jekyll sites
  and Obsidian vaults written beside the archives no longer appear on the
  Archives screen as if they were archives.
- The Reader's Export menu no longer runs off the bottom of the window when
  the developer formats are open; it scrolls.
- **Obsidian names no longer collide.** Two different wikis, blogs or forums
  with the same title — including the same community captured from two
  deployments and combined — now get a folder each, and two pages, posts or
  topics with the same title get a note each instead of overwriting one
  another. Names that are the same file on Windows and macOS (differing only
  in case or Unicode form), titles that become equal once made safe for a
  filesystem, and attachment names count as the same name. The first keeps its
  plain name; each other one is tagged with a short code derived from its
  Connections id, e.g. "Handbook (1a2b3c)", so the same capture always
  produces the same vault. Links point at the note that was written; the
  summary counts disambiguated names and the vault README lists them.
- **Forum threads keep their replies, nested, in every export.** The Jekyll
  export wrote a forum topic without its replies; it now writes every reply
  with its body, images and links. In Jekyll, Obsidian and Hugo a reply to a
  reply goes one blockquote deeper per level, so a rendered page indents it
  under the reply it answers (deeper headings only showed in the Markdown),
  and reply times read `YYYY-MM-DD HH:MM UTC`.
- **Jekyll posts render with their title, author and correct characters.**
  Exported posts named no layout, so Jekyll rendered them bare: no title, no
  author, and no page head declaring UTF-8 — a browser then guessed the
  encoding and showed `‘` as `â€˜`. Every post now names `layout: post`.
  Reported by Christoph Stoettner (issues #3 and #4).
- **Content discovery in the console uses the sign-in method chosen in
  Settings.** With Username and password chosen, discovery still asked with
  Windows sign-in from the configuration, and found no feeds. Found and fixed
  by Christoph Stoettner (pull request #2).

## 0.1.11 — 2026-09-25

### Security fixes — please upgrade

A security review found ways for captured content, or an archive someone
hands you, to act against the person using the tool. All versions up to
0.1.10 are affected. Nothing here needs any action beyond upgrading.

- **The console no longer runs code from captured content.** Comments, forum
  replies, the activity log and the manual page could carry markup that ran
  as the console itself, which can start captures, change settings and
  delete archive folders. Comment and reply HTML is now sanitised, every
  value the console shows is escaped, page bodies can no longer address the
  console, and attachments are served so that they cannot run (non-image
  attachments now download rather than open in a tab).
- **Your sign-in stays with your deployment.** Console lookups could be made
  to sign in to a host named by a web page or by an archive — sending a
  password, a session cookie or a Windows sign-in handshake there. Lookups
  now go only to deployments you chose (configured, signed in to, or dropped
  into the setup screen), other websites cannot trigger them, cookies and the
  Basic password are bound to the deployment's own address, and sign-in
  follows a redirect to another host only within the deployment's own domain
  or to a host listed in `hcl_hosts`. An archive from a deployment you have
  not used yet offers to use it rather than signing in on its own.
- **PDF export is isolated.** Captured pages could run script and reach the
  network while the PDF was printed. The PDF is now cleaned with an
  allowlist and rendered with no network access and no page script, and the
  browser's sandbox is back on except where it cannot start without it
  (running as root). Author CSS that loaded web fonts or backgrounds from the
  internet no longer does so in the PDF. The external-image fetcher refuses
  internal, loopback and link-local addresses, checked on every redirect.
- **A handed-over archive cannot read your files.** Its references are
  accepted only as content hashes, so none can point outside the archive;
  an update skips such an entry and fetches it again. Oversized entries in a
  `.zip` are refused rather than read into memory.
- **Obsidian and Jekyll exports are inert.** Text that looked like HTML,
  Markdown or Jekyll template commands is escaped, `javascript:` links are
  dropped, front matter escapes line breaks, and file names handle Windows
  device names such as `CON` and `NUL`.
- **PDF export shows that it is working.** The menu closes when an export
  starts, which hid its progress; the Export button now shows "Rendering
  PDF…" with the time so far, and "PDF ready" when the download starts.
- **Smaller hardening.** A console exposed on the network (`--host 0.0.0.0`)
  prints a warning; proxy passwords are masked in diagnostic output; uploads
  and downloaded archives are capped at 20 GB; the release workflows pin
  every action to an exact commit and install from the lock file.

## 0.1.10 — 2026-09-24

### Username/password sign-in works from the console

Found, diagnosed and first fixed by Christoph Stoettner
([@stoeps13](https://github.com/stoeps13)) in pull request #1 — thank you.

- **The Settings screen shows the sign-in method you saved.** It reported none
  whenever the console was built the default way — a leftover of the removed
  demo mode — so it always showed Windows sign-in, and choosing Username and
  password never stuck.
- **A run starts with that method.** The run form kept its own Windows sign-in
  default and sent it with every start; it now follows the saved setting.
- **The console reads credentials from the environment**, as the command line
  always has: `CONNECTIONS_EXPORT_USER`/`CONNECTIONS_EXPORT_PASSWORD` (and a
  pasted token, and the proxy variables) were invisible to console runs and to
  the item-count lookups, which also asked with Windows sign-in whatever was
  configured.
- The README now says where the configuration file is looked for, and how to
  set up Basic Auth.

### The README is up to date

- Jekyll is named alongside Obsidian throughout, with the note that both are
  independent third-party applications; both exporters, and the console, cover
  everything captured, not only wikis; the console reads an archive or a `.zip`
  of one where it lies.
- The install instructions no longer point to the `[obsidian]` extra, which has
  not been needed since 0.1.8.

## 0.1.9 — 2026-09-24

### External images in the PDF: included, marked, and credited — your choice

- **The PDF can carry images from other websites.** Until now they were
  dropped from the PDF with a bare "[image not captured]". A new export
  option, **Include external images** (on by default; `pdf
  --no-external-images` on the command line), fetches them at export time
  and embeds them.
- **Each one is recognisable on paper.** An included external image sits in
  a dashed frame captioned _External image E1 · host_, and an **External
  content** page at the end (in the table of contents) lists every E-number
  with its full original address, the page it first appears on, and whether
  it could be retrieved. Small images in running text — icons, emoji, badges,
  anything shown at 48 px or less, adjustable under Settings → Small external
  images or `pdf_small_image_px` in `connections-export.toml` — get a
  superscript _E1_ instead of the frame,
  so a sentence is not broken up; they are listed just the same. The page states that these are third-party material
  and that the tool does not assess rights to them — that decision rests with
  whoever makes or passes on the document.
- **Left out, the address still travels.** With the option off, or when a
  fetch fails, the marker carries the original URL; a deployment image that
  was not captured now says which one, too.
- **Fetched by the tool, not the browser, and without credentials.** Each
  request is bounded (15 s, 20 MB, images only) and runs alongside the
  others, so a third-party host that does not answer costs one request rather
  than the whole render timeout; no cookies or sign-in headers are sent.
- The reader and the Obsidian/Jekyll exports are unchanged: they keep
  external images as references to their original addresses.

## 0.1.8 — 2026-09-23

### The Obsidian and Jekyll exporters work from a plain install

- **`markdownify` is now a base dependency**, not an opt-in extra. The Obsidian
  and Jekyll exporters need only it (pure Python: `beautifulsoup4` + `six`, no
  system libraries), so gating them behind `pip install
  'connections-export[obsidian]'` bought little and confused people — a plain
  `pip`/`uv install connections-export` now runs both. The `[obsidian]` and
  `[jekyll]` extras remain as empty aliases so an existing install command still
  resolves. The only opt-in extra left is `[sspi]`, which genuinely needs system
  Kerberos headers.
- **A failed export in the console shows why, in the console.** The
  developer-format export surfaced only some failures to the browser and let the
  rest (for example a missing dependency) fall through to a bare "export failed"
  with the real message in the terminal. It now returns the reason for any
  failure, so the console shows it.

### The version reads correctly everywhere

- **The executable now names its own version.** The single-file build could not
  read its own package metadata, so `own_version()` fell back to
  `0.0.0+unknown` — which showed in the console's version line and was stamped
  into every archive's `generator_version` and the SBOM. The build now bundles
  the package's `.dist-info` (`--copy-metadata`), and the release gate fails if
  the frozen executable cannot state its version.
- **A `pip`/`uv` install shows a clean release version.** Only the downloadable
  executables carry a build stamp, so a wheel install had none — and the
  console treated a missing stamp as "dev", labelling every installed release
  "v0.1.7 · dev". An installed wheel is now recognised as a release (via PEP 610
  `direct_url.json`); only a source/editable checkout reads "· dev".
- **`connections-export --version`** prints the build label — the same string
  the console shows (`v0.1.8`, or `… · test build · <commit>`, or `… · dev`).

## 0.1.7 — 2026-09-23

### Storing and sharing an archive

- **Save an archive as a single `.zip`, from the console.** Each archive on the
  Archives screen has a **Save as .zip** action: because archives are local and
  can be large, it writes `<name>.zip` beside the archive on your own disk
  (nothing is streamed through the browser) and tells you where. The `.zip`
  opens straight back in the Reader, read-only, and can be moved to OneDrive, a
  share, or an email.
- **The manual now explains** zipping an archive to move it, the read/write vs.
  read-only difference between a directory and a `.zip`, and how efficiently a
  `.zip` reads from local disk versus an online-only cloud file or a network
  share.
- **Each archive records the source repository** (GitHub) alongside the PyPI
  URL it already carried, so whoever is handed one can find both the tool and
  its code.

### The build you are running is now visible

- **The console shows its version at the sidebar foot.** A real release reads
  "v0.1.7"; a downloadable **test build reads "v0.1.7 · test build · <commit>"**
  in a called-out colour, so a test build can never be mistaken for an official
  release; a source checkout reads "v0.1.7 · dev". The commit and git ref are
  stamped into the executable at build time.

### Clearer, honest repair progress

- **The aggregate comment index now actually narrows the repair.** It resolves
  which posts a change touched by walking the parent-comment ID chain, instead
  of a feed URL that matched no post — which had made it fall back to re-reading
  almost every post's comment feed (thousands of requests on a large blog). A
  repair now fetches only the affected posts' feeds.
- **A repair can be stopped.** A **Stop** button halts a run that is going awry
  (too slow, too many feeds): the archive it is on keeps what it already
  fetched, the rest are not started, and running Repair again finishes them.

- **Repair progress is reported as separate dimensions**, not one blended
  number that could confusingly exceed the post count. The status line now
  shows aggregate index pages, per-post comment feeds, and unique posts done of
  total — each counted for the current run from its own events, never from the
  cumulative request log of past attempts.
- **The effective pacing is shown**, so it is obvious when a run launched away
  from its config file is pacing at the 1-second default rather than a faster
  configured interval.
- **Each repair leaves a `repair-report.json`** in the archive — run time,
  index pages read, posts done, feeds fetched, comments recovered, pacing —
  so a finished or interrupted repair is understandable after a reload.
- An interrupted (never-completed) blog repair stays offered for retry, with
  the reason that the previous repair did not finish.
- **A repaired archive stops offering a repair.** Whether an archive needs
  repair is now decided from its latest blog run — a completed capture with the
  fixed adapter means done — rather than from the append-only request log, in
  which the original capture's failed first-page requests lived forever and
  kept proposing a repair that had already run.

### PDF export

- **The PDF render timeout is configurable.** Settings → PDF appearance gains a
  **Render timeout (seconds)** field (120 by default): raise it for a large or
  slow-loading document that was timing out, lower it to fail sooner. When an
  export does hit the limit, the console reports it and writes a
  `pdf-failure-*.md` report naming the resource that did not finish, instead of
  failing silently.

### Exporting to Obsidian and Jekyll

- **Both developer exporters are now reachable from the console, and on equal
  footing.** The Reader's Export menu gains an "Advanced — developer formats"
  section offering **Export Obsidian vault** and **Export Jekyll site** — the
  same two exporters the CLI has always had. Each writes a folder beside the
  archive and reports where. It sits behind a disclosure on purpose: PDF stays
  the everyday export, so casual users see no new clutter.
- **The manual now introduces Obsidian and Jekyll side by side**, with a
  sentence on what each is and a link to its website, framed for people who
  want to re-home content into a tool they edit — while making clear the PDF is
  all most people need.
- **Both the manual and the console state plainly** that Obsidian and Jekyll are
  independent, third-party applications: the exporters are a convenience, not an
  endorsement, and each application is governed by its own licence and terms.

### Blog comments

- **Blog comments are captured in full.** Comment feeds are numbered from
  page 0, like blog entry feeds, but the crawl read them from page 1 — so any
  comments on the first page were silently missed. Every new capture now reads
  them from page 0. Blog posts were unaffected; only their comments.
- **A repaired archive now shows the comments it recovered.** Reading an
  archive back (derive/replay) also defaulted to page 1 for blog comments, so
  an archive could hold the page-0 comment responses and still display zero.
  Derivation now reads comments from page 0, falling back to page 1 only for
  older archives whose comments genuinely sit there. This was the reason an
  early repair could report "recovered 0" though the responses were present.
- **Older blog archives can be repaired in place, without recapturing.** When
  any archive may be missing first-page comments, a banner appears at the top
  of Archives with a single **Repair all** button. It doubles as a live status
  line — "Repairing archive X of N — recovered K comments so far…" — and
  finishes with how many previously-missing comments were recovered and that
  every archive is now up to date. Repair re-reads only the comment feeds into
  the archives you already have; it recaptures nothing, is safe to run again,
  and no longer asks you to type a count. From the command line:
  `connections-export crawl --repair --into <archive>`, which needs only the
  archive and takes what to re-read from its own recorded provenance. An
  archive from before that provenance existed is recognised from its request
  log, so the offer is made whenever completeness cannot be confirmed and
  never withheld on a guess. See the manual's "Repairing a blog archive from
  an older release."
- **Repair works on old archives that predate run provenance.** The earliest
  archives recorded only a source URL and adapter version — no completed-run
  provenance — so repair could flag them but not reconstruct what to re-crawl.
  It now falls back to `archive-summary.json` and the component IDs in
  `manifest.jsonl`, so a legacy archive can be repaired from what it already
  contains.
- **A repair no longer fails because of an unrelated component.** Repair used
  to re-crawl every component the archive captured, so one stale 404 in a
  forum or wiki marked the whole run failed and suppressed the blog comments it
  had just recovered — the archive would report "recovered 0" though the
  comments were there. Repair now targets only blog components (the defect it
  exists for), and the recovered count is reported even when something else in
  the run did not come back cleanly.
- **Repair is faster and shows its progress.** On a large blog, re-reading
  every post's comment feed was slow and gave little sense of what was
  happening. Repair now reads the blog's aggregate comments feed once, as an
  index of which posts actually hold comments, and re-fetches only those — and
  the console shows posts done, total, and comments recovered as it goes.

## 0.1.6 — 2026-09-21

### Capturing and exporting a community

- **Community capture from the command line finds its components.**
  `connections-export crawl <community-url>` reported "no wiki, blog or forum"
  for every community, over a perfectly authenticated connection: the
  discovery read the wrong field of the tool's own response and saw every feed
  as unreadable. The console was unaffected; only the CLI path had never been
  exercised.
- **Images and attachments export from an archive, not only a package.**
  `ingest --archive` wrote no image or attachment files — every one counted as
  "not captured" — because the archive path required a bare blob digest where
  the model carries the `sha256:`-prefixed form. The Reader and
  `ingest --package` were fine; only `--archive` went through the strict
  lookup, which nothing had tested with a present file.
- **A community's Files library travels to the vault or site.** Each file's
  bytes are copied out, a body link to a file resolves to that copy rather than
  the dead deployment URL — the referenced case especially — and a `Files.md` /
  `files.md` lists every document, an uncaptured one shown as a visible gap.
  Both the Obsidian and Jekyll exporters.

### Proxies

The proxy handling is now principled and non-assuming: it obeys your
organization's configuration and your explicit choice, and never silently
guesses in either direction.

- **On Windows the PAC leads.** A request to the deployment is resolved the
  way a browser resolves it: your PAC script or auto-detection (via WinHTTP)
  decides per address, then the system proxy setting, and only then
  `HTTPS_PROXY`/`NO_PROXY`. A blunt global `HTTP_PROXY` no longer overrides a
  PAC that correctly returns DIRECT for an internal host — the case that made
  an internal deployment look unreachable. `--proxy` / the `proxy` config key
  still override everything, and a private address is never *assumed* direct: a
  deployment may legitimately sit behind a proxy, and the PAC decides.
- **The Windows PAC path actually runs now.** A pointer bug made every PAC
  evaluation fail silently and fall back to direct; it is fixed, and the
  evaluation is bounded so WPAD cannot hang a run.
- **Uncertainty is surfaced, not assumed away.** A PAC that fails or times out
  is reported and the connection is flagged, so a host that genuinely needs a
  proxy is never mistaken for a dead server.
- **One decision governs every connection**, including the Windows sign-in
  handshake, which previously read proxy environment variables on its own and
  could disagree with the crawl. Your corporate CA bundle is preserved.
- `connections-export probe proxy` prints the whole chain — including
  "undetermined" — and every run logs its proxy decision. See the manual's
  Proxies section.

## 0.1.5 — 2026-09-21

### Proxies, the way the browser does them

Reports of "proxy issues" came from machines whose proxy is configured in
Windows Internet Options — what Edge and Chrome use — and never as an
environment variable, which was the only place the tool looked. (0.1.6 revises
this further; see above.)

- The tool decides the way a browser decides: `--proxy` or the `proxy`
  configuration key; then the environment; then, on Windows, the system's PAC
  or automatic detection and the system proxy setting; then direct.
- **The console itself is never reached through a proxy.** A PAC script that
  sends `127.0.0.1` to a proxy exists, and a proxy that refuses it is right to.
- New `connections-export probe proxy` prints what a request to the deployment
  and to the console would go through, and which step decided.
- A proxy demanding its own sign-in is a limit the tool names, with the fix,
  rather than a retry that looks like a deployment refusing.

### Exporting to Obsidian

- `ingest` takes the **archive** a capture writes — the directory the console
  lists under Archives, or a `.zip` of one — as readily as a package. It
  required a package, and nothing produced one: the manual said every capture
  writes a package, the package writer existed, and no command called it. The
  documented command failed for everyone on the first try with a traceback
  naming `interchange.json`. Either flag now accepts either kind of directory,
  and a directory that is neither is told what it is missing, in words.
- **Blogs and forums are laid out as notes.** A folder per blog with a note
  per post in the blog's order, comments beneath; a folder per forum with a
  note per topic and its replies as nested headings beneath it, each reply
  converted like the topic rather than flattened to a line. Links between
  items resolve as `[[wikilinks]]` whatever kind is on either end. The vault
  had held wikis only, so a blog captured for exactly this purpose produced
  an empty vault.
- **New `--format jekyll`.** Writes a conventional Jekyll site fragment:
  every page, post and topic becomes a dated Markdown file under `_posts/`,
  with body images under `assets/images/imported/` and in-export links
  rewritten to the target post. It is a fragment on purpose — the site's own
  `_config.yml`, layouts, `url` and `baseurl` stay with the site owner — so it
  drops into an existing GitHub Pages site. Needs the `jekyll` extra when
  installed with `pip`; the executable has it built in.
- New `connections-export package --archive DIR --output DIR` writes the
  portable interchange package the manual has always described —
  `interchange.json`, its blobs, the capability manifest and the format's own
  reference document — for an ingester of your own, or to hand to someone who
  has never seen this tool.

## 0.1.4 — 2026-09-18

### Working out what a community holds

On a real deployment the component list on Select & Tailor came back empty,
and the cause was the console's own patience: it gave up after 35 seconds
while the answer was still on its way.

- Discovery makes its ten to fifteen reads through **one signed-in session**
  instead of opening a new one for each. With integrated authentication a new
  session is a full handshake, so each read was paying for two or three round
  trips before the one it was for.
- Those reads are no longer paced like an export. The delay between requests
  exists so a capture of thousands of feeds is a good citizen; a dozen reads to
  list a community's parts are not that.
- Each read may take up to two minutes, rather than the thirty seconds a
  capture allows. A slow deployment that answers in forty seconds is
  answering.
- The console shows what discovery is doing — which phase it is in, and how
  many requests it has made — and gives up only after **two minutes of
  silence**, not two minutes of work. When it does give up, it says which
  phase the deployment went quiet in.
- Sub-communities added to a run are read the same way, with the same
  patience. They previously had no time limit at all.
- A sign-in that cannot even begin — the Windows authentication package
  missing, no domain to authenticate against — is reported as the reason the
  list is empty, rather than as a server error.

## 0.1.3 — 2026-09-06

### Capturing one person's share of a community

Asked for "only me", a community capture used to read all of it anyway — every
forum topic and every reply — because whether a thread is yours is only
knowable once its replies have been read. In a busy community that is tens of
thousands of entries fetched to keep a few hundred threads.

- The capture now asks the deployment's own Search which threads in that
  community the person is in, and reads those. Each selected thread is still
  read in full — topic, every reply, attachments and images — and the author
  filter still decides what is kept. Search chooses what to read; it never
  stands in for the content and never decides what is yours.
- The Ingest view says what happened: how many threads were chosen out of how
  many hits, whether the answer was complete, and — the case that matters —
  whether it fell back to reading the forums in full. Falling back is always
  available and always correct; it is never silent.
- Only a user id the deployment itself returned is used, which the console gets
  from **Only me** or from resolving a person by email. A name typed by hand
  reads the forums in full instead. `connections-export crawl` takes
  `--search-userid` for the same thing, explicitly, because a command line
  cannot tell a resolved id from a typed one.
- Fixed: a capture seeded this way ignored the Preview limit, and did not apply
  the author filter to the threads it fetched.
- Fixed: a community captured from the command line produced wikis that did not
  record which community they belonged to.
- Fixed: the recovery of forum attachment filenames ran even on a capture that
  was not collecting assets.

### The live Ingest view

- A console that reconnects or is reloaded during a capture no longer loses the
  part of the run it missed, and no longer counts it twice. The stream is
  replayable, and a connection the browser silently re-establishes resumes
  where it left off rather than starting over.
- A stream opened after a capture has finished now ends, instead of replaying
  the run and then staying open forever.
- An author-filtered run shows a live count of what it has read but not kept.
  Such a run must read every thread before it can know whether you are in it,
  so it fetches steadily while the kept tree stands still — which used to read
  as stalled at the moment it was working hardest.
- A pruned item is now labelled by its id. It used to be formatted as a URL,
  which made every pruned line claim to have been fetched from the console
  itself.

### Archives

- The archive list has a stable order. Archives written in the same second
  share a modification time, and the list then fell back to whatever order the
  filesystem returned — so two listings of a directory nobody had touched
  could disagree, differently on Linux, macOS and Windows.

### Diagnostics

- New `connections-export probe search-reach`: asks how far a person's Search
  query reaches — how many pages came back and whether the last was full —
  without crawling anything. It exits non-zero when the answer was cut short at
  the page limit, so a comparison run under the same limit is not read as
  complete.
- `compare-author` reports progress while it works. It crawls a whole container
  before it can compare anything, and used to print nothing at all until the
  table at the end, where an hour of silence is indistinguishable from a hang.
- `compare-author` now distinguishes an answer that ended because there was no
  more from one cut short at the page limit. They produced identical output, so
  a truncated answer read exactly like a complete one.
- Probes check their arguments before opening a connection, and report an
  authentication failure as something to act on rather than as a stack trace.

### Development

- `just sync-sspi`, and `just console`, install the Windows integrated-auth
  extra — so a development console authenticates the way the released one does.

## 0.1.2 — 2026-08-29

- Windows: integrated authentication works in the packaged executable.
  `pywin32` imports `win32timezone` lazily, so static analysis never saw it and
  it was not frozen into the bundle; the SSPI handshake failed and every
  community read as empty.
- Which deployment a capture reads follows from the URL and from nothing else.
  The separate demo mode is gone: the built-in synthetic deployment is a
  deployment like any other, at an address of its own, and the setup screen
  offers its URLs to drop the way it would one of yours.
- Launched with no arguments on Windows, the console could bind a port another
  program was already using. `SO_REUSEADDR` does not mean on Windows what it
  means elsewhere.
- The log names the host each request went to, so a request answered by the
  wrong machine is visible.

## 0.1.1 — 2026-08-28

- README renders on PyPI: links that pointed at files in the repository are
  absolute.

## 0.1.0 — 2026-08-27

First public release.
