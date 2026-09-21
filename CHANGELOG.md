# Changelog

Notable changes to `connections-export`, newest first. Versions follow
[semantic versioning](https://semver.org/), and while the major version is 0
the archive format may still change between minor versions — the format's own
version is recorded inside every archive.

## 0.1.5 — 2026-09-21

### Proxies, the way the browser does them

Reports of "proxy issues" came from machines whose proxy is configured in
Windows Internet Options — what Edge and Chrome use — and never as an
environment variable, which was the only place the tool looked.

- The tool now decides the way a browser decides: `--proxy` or the `proxy`
  configuration key; then `HTTPS_PROXY` and `NO_PROXY`; then, on Windows, the
  system's PAC script or automatic detection, evaluated for the deployment's
  address by the same engine the browser uses; then the system proxy setting
  and its bypass list; then direct.
- **The console itself is never reached through a proxy.** A PAC script that
  sends `127.0.0.1` to a proxy exists, and a proxy that refuses it is right to.
- New `connections-export probe proxy` prints what a request to the deployment
  and to the console would go through, and which step decided.
- A proxy demanding its own sign-in is a limit the tool names, with the fix,
  rather than a retry that looks like a deployment refusing.

### Exporting to Obsidian

The first report from a user: "the Obsidian export did not work, some json
file was missing." They were right, and it was not their mistake.

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
