# Changelog

Notable changes to `connections-export`, newest first. Versions follow
[semantic versioning](https://semver.org/), and while the major version is 0
the archive format may still change between minor versions — the format's own
version is recorded inside every archive.

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
