# The connections-export interchange format

This document describes a **package**: a directory of files produced by
`connections-export` that holds the full content of one or more wikis, blogs,
and forums, in a form
meant to be read by *any* program — not just `connections-export` itself, and not
just programs written by people who know anything about HCL Connections,
the product this content was originally exported from.

If you were handed a package and simply want to read it, the tool that
wrote it will do that for you — it browses an archive offline and exports
to PDF, and it is at
<https://pypi.org/project/connections-export/>. You do not need it: the
content is plain JSON and this document describes all of it. But knowing
a program exists is worth more than a search term, so every package says
so, in `manifest.json`, in `provenance.json`, and here.

If you are building a tool that reads one of these packages (an
"ingester") — to import the content into a different wiki system, a
static site, a document store, or anything else — this document is
everything you need. You do not need to know what HCL Connections is,
how its APIs work, or anything about the tool that produced the package.

A copy of this document, named `INTERCHANGE.md`, ships inside every
package it describes, so the package explains itself even in isolation.

## 1. Purpose and guarantees

A package is the **normalized, portable output** of one export run. It
contains:

- every wiki, page, comment, version record, attachment, blog post,
  blog comment, forum topic, forum reply, and file ("blob") the export
  run was able to capture;
- an explicit record of anything it *couldn't* capture (a missing
  attachment, an uncaptured page body) — never a silent gap;
- enough information to rebuild the page hierarchy, sibling order,
  comment threads, and links between pages, in a different system.

### What "lossless" means here

"Lossless" describes the **capture and normalization step**, not
necessarily what a target system can accept. The package is built to
hold everything the source system exposed. Whether a given piece
survives into a *target* wiki depends on what that target's importer
does with it — that loss, if any, happens later, in the writer that
reads this package, against a package that still has the data. See
section 6.

### HCL-agnostic

Nothing about the shape of this format assumes HCL Connections, or any
other specific wiki product. Field names are generic (`pages`,
`comments`, `content_html`, …), not the vendor's internal terminology.
Wherever a vendor-specific identifier is genuinely useful to keep around
(for traceability, debugging, or matching content back to the original
system), it is confined to one place — a `provenance` block attached to
the relevant entity — and never appears in a field you need to
understand the content itself.

## 2. Package layout

A package is a directory with this layout:

```
<package>/
  interchange.json     the normalized content model — one JSON document
  manifest.json         a capability manifest: what this package does
                         and does not contain
  provenance.json        where this package came from
  INTERCHANGE.md          this document, copied in verbatim
  blobs/
    <sha256-hex>          binary files (images, attachments), one per
                           distinct file, named by its own content hash
```

`interchange.json` is the primary artifact — the entire content model in
one JSON document. Everything else exists to help you interpret it:
`manifest.json` tells you what to expect (and what to expect is
missing), `provenance.json` tells you where it came from, `blobs/` holds
the actual bytes of every image and attachment, and this document
explains how to read all of it.

## 2.1 Raw archive display metadata

The crawler's raw archive directory may also contain an
`archive-summary.json` sidecar. It is a small, derived-at-run-completion
description used by archive browsers so they can show archive identity and
component counts without re-reading and deriving the full archive:

```json
{
  "status": "ok",
  "base_url": "https://connections.example.corp",
  "source_version": "8.0",
  "hcl_hosts": ["connections.example.corp"],
  "groups": [
    {"kind": "wiki", "title": "Engineering", "count": 42},
    {"kind": "blog", "title": "Team Blog", "count": 12},
    {"kind": "ideation_blog", "title": "Ideas", "count": 7},
    {"kind": "forum", "title": "Python", "count": 99}
  ],
  "communities": [{"title": "Developer Community"}]
}
```

This sidecar is a display index, not a second source of truth. Its counts and
titles describe the derived model; readers must open the raw archive and
derive it to reconstruct content. Older archives without the sidecar remain
valid and may be characterized on demand by a compatible browser.

## 3. The content model (`interchange.json`)

`interchange.json` is one JSON object. This section documents every
field in it, in plain language. Field names below match the JSON keys
exactly.

A field that is `null` means "this piece of information was not
available," not "this piece of information is empty." An empty string
or empty list means the value legitimately *is* empty (e.g. a page with
no body). This distinction matters: do not treat `null` and `""` as
interchangeable when you reconstruct content.

### 3.1 The top level (`Interchange`)

The root JSON object.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer | The version of this format. See section 7. |
| `base_url` | string or `null` | The web address of the source deployment this package was exported from. Informational — useful for constructing a human-readable "originally at" note, not required for reconstruction. |
| `source_version` | string or `null` | The version of the source product (e.g. `"8.0"`), if known. |
| `run_id` | string or `null` | An identifier for the specific export run that produced this data. Useful for correlating a package with export-run logs; has no meaning to a target system. |
| `hcl_hosts` | list of strings | The set of host names the export run treated as "part of the same deployment" as `base_url`, when classifying links (see 3.9). Informational. |
| `author_filter` | string or `null` | The person this export was filtered to, if any. `null` means the run captured everyone's content. It says what the package HOLDS, not how to display it: with a filter set, an item can be absent because it was never asked for, and a consumer that reports absences should say so differently. |
| `wikis` | list of `DerivedWiki` | Every wiki captured in this run. |
| `blogs` | list of `DerivedBlog` | Every blog captured in this run. Empty if none. |
| `forums` | list of `DerivedForum` | Every forum captured in this run. Empty if none. |
| `file_libraries` | list of `DerivedFileLibrary` | Every community file library captured. Empty if none. See **3.15**. |
| `rich_content` | list of `DerivedRichContent` | Every community's Highlights pages. Empty if none. See **3.17**. |
| `communities` | list of `DerivedCommunity` | Community ownership/grouping for captured containers. Empty when no community metadata was available. |

A package can hold any mix of wikis, blogs, forums, file libraries, and rich content; each list is independent and empty when that app was not part of the export. `communities` is a grouping layer and does not replace the app lists. Everything below about wikis applies section-by-section; the other apps are documented in **3.11–3.17**.

### 3.1.1 What every container and every item carries

Two field sets repeat across all five apps, so the model states them once. You
do not need to know this to read a package — every field still appears in the
JSON of every object that has it — but knowing which fields are universal means
an ingester can write one reader for all five apps instead of five.

**Every container** (`DerivedWiki`, `DerivedBlog`, `DerivedForum`,
`DerivedFileLibrary`, `DerivedRichContent`) carries `id`, `title`,
`community_uuid`, `community_title` and `alternate_url`. Each keeps its own
name for its children — `pages`, `posts`, `topics`, `files` — because that
vocabulary is what makes the model readable.

**Every item** (`DerivedPage`, `DerivedBlogPost`, `DerivedForumTopic`,
`DerivedForumReply`, `DerivedFile`, `DerivedRichContentPage`) carries `id`,
`title`, `author`, `author_userid`, `contributors`, `content_html`, `created`,
`modified`, `assets`, `links`, `provenance` and `alternate_url`.

Two items narrow one field, and the difference is real: a `DerivedForumReply`
and a `DerivedRichContentPage` may have `content_html: null`, meaning no body
was captured, where an empty string would claim one was and it was empty. A
Highlights widget that was placed and never written into is exactly that case.

A `DerivedComment` is deliberately **not** an item: it carries no `assets` and
no `links`, and empty ones would state something untrue about what was
captured.

### 3.2 A wiki (`DerivedWiki`)

One wiki: a named collection of pages.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | An opaque, unique identifier for this wiki. Stable across a single export run; treat it as an arbitrary string, not something to parse. |
| `label` | string | The wiki's short internal name (URL-safe). |
| `community_uuid` | string or `null` | The owning community's UUID, when the wiki belongs to one. |
| `community_title` | string or `null` | That community's display title, when captured — carried on the container so a single wiki can be read without the `communities` list. |
| `title` | string | The wiki's human-readable display name. Use this for anything shown to a person. |
| `root_page_ids` | list of strings | The `id`s of this wiki's top-level pages (pages with no parent), **already in display order**. |
| `pages` | object | Every page in this wiki, keyed by page `id`. The map itself is unordered — use `root_page_ids` and each page's own `child_ids` (3.3) to determine order, never the iteration order of this object. |

### 3.3 A page (`DerivedPage`)

One wiki page. This is the largest entity in the model.

**Identity and content**

| Field | Type | Meaning |
|---|---|---|
| `id` | string | An opaque, unique identifier for this page (unique within the whole package, not just its wiki). Treat it as an arbitrary string. |
| `label` | string or `null` | The page's short internal name. |
| `title` | string or `null` | The page's human-readable title. Use this for display. |
| `content_html` | string | The page's content, as an HTML fragment. Always present as a string; `""` means the page legitimately has no body (not that the body failed to capture — check `provenance.note`, 3.10, to tell the two apart). See "Body HTML" below for what's inside it. |

**Hierarchy and order**

| Field | Type | Meaning |
|---|---|---|
| `parent_id` | string or `null` | The `id` of this page's parent page, or `null` if it's a top-level page (also listed in its wiki's `root_page_ids`). |
| `ordinal` | integer | This page's position among its siblings (the other pages sharing the same `parent_id`), lowest first. |
| `child_ids` | list of strings | The `id`s of this page's direct children, **already sorted by their own `ordinal`**. This is the authoritative child order — do not re-derive it by scanning for `parent_id` matches and sorting yourself; use this list. |
| `parent_discrepancy` | string or `null` | Set (non-`null`) only when the source system recorded two disagreeing signals about this page's parent. When set, `parent_id` is still the value to use — this field is a note for anyone auditing the export, not an instruction to act differently. |

**How to reconstruct the tree:** start from each wiki's `root_page_ids`
(already ordered). For each page, its children are exactly
`child_ids`, already ordered. Recurse. You never need to compute order
or grouping yourself — both are handed to you pre-computed.

**Comments, versions, attachments, assets, links** — see their own
subsections below (3.4–3.9); each page carries a list of each.

**Other metadata**

| Field | Type | Meaning |
|---|---|---|
| `author` | string or `null` | The page's author, as a display name or account identifier from the source system. Not guaranteed to resolve to anything in a target system. |
| `author_userid` | string or `null` | The author's stable account id (`snx:userid` in HCL Connections), when present — more reliable than the display name for identifying who authored something. |
| `contributors` | list of strings | Additional contributor display names (HCL records participation as `atom:author` *and* `atom:contributor`). Empty when none. |
| `is_context` | boolean | `false` normally. `true` only in an **author-filtered** package (see §3.14): this page wasn't authored by the filtered user — it's kept solely to preserve the tree path to a descendant that was. |
| `created` | string or `null` | Creation timestamp, ISO 8601 (`"2026-07-20T12:00:00Z"` shaped) when present. |
| `modified` | string or `null` | Last-modified timestamp, same format. |
| `tags` | list of strings | The page's **tags** (free-text labels), one string per tag — a documented, retrievable characteristic of every page. **Always present**; an empty list `[]` when the page has none, never `null`. Order follows the source. In HCL Connections these come from the page's tag feed (`?category=tag`), whose feed-level `<category term=…>` elements name the tags. A reconstructing ingester should map these to its own tagging mechanism (e.g. the reference Obsidian ingester writes them as native `tags:` frontmatter — section 7). |
| `acls` | list of strings | Access-control entries, if any were captured. **In practice this is always empty in current packages** — access control lists are not yet captured by the export (the manifest's `acls` capability, section 5, will say `"absent"` when this is the case). The field exists so the format doesn't have to change shape when that capability is added. |
| `provenance` | `Provenance` object | Where this page's data came from. See 3.10. |

**Body HTML**

`content_html` is an HTML fragment (not a full document — no `<html>` or
`<body>` wrapper). Two things to know before you use it:

1. **Author-written CSS is preserved.** If the original author applied
   inline styles or embedded `<style>` content to their page, that
   markup is kept as-is inside `content_html`.
2. **Platform CSS is *not* included.** The visual styling that the
   source wiki platform itself applied around and within page content
   (its own theme, layout classes, chrome) is not part of `content_html`
   and is not captured anywhere in this package. If a page looked a
   certain way in the original product because of platform styling
   rather than anything the author wrote, that appearance will not be
   reproduced automatically — a target system's own styling applies
   instead. This is expected and is not a defect in the export: the
   goal is the author's *content*, not the source product's chrome.

Image and file references inside `content_html` (e.g. `<img src="...">`)
are **not rewritten** in this HTML — the original `src`/`href` values
from the source system are left exactly as they appeared. The resolved,
capture-time meaning of each such reference is available separately, in
`assets` (3.8) and `links` (3.9); use those lists to rewrite references
when you reconstruct content in a target system (see section 6, step
5).

### 3.4 A comment (`DerivedComment`)

One comment on a page, from `DerivedPage.comments`.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | An opaque, unique identifier for this comment. |
| `author` | string or `null` | The commenter, as a display name or account identifier. |
| `author_userid` | string or `null` | The commenter's stable account id (`snx:userid`), when present. |
| `contributors` | list of strings | Additional contributor names, if any. Empty when none. |
| `content_html` | string or `null` | The comment's content as an HTML fragment. `null` means it was not captured; distinct from an empty comment. |
| `created` | string or `null` | Creation timestamp, ISO 8601. |
| `modified` | string or `null` | Last-modified timestamp, ISO 8601. |
| `parent_comment_id` | string or `null` | The `id` of the comment this one is a reply to, or `null` if it's a top-level comment on the page. |

**Threading:** a page's comments are a flat list (`DerivedPage.comments`),
not pre-nested. To reconstruct threads, group comments by
`parent_comment_id`: comments with `parent_comment_id == null` are
top-level; every other comment is a reply to the comment with that `id`.
If **no** comment on a page (or in the whole package) ever has a
non-`null` `parent_comment_id`, the source system did not capture reply
relationships for that content — see the manifest's `comment_threading`
capability (section 5) to check this at a glance before assuming
threading data exists.

### 3.5 A version (`DerivedVersion`)

One historical version record for a page, from `DerivedPage.versions`.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | An opaque, unique identifier for this version record. |
| `version_label` | integer or `null` | The version's sequence number (1, 2, 3, …), if known. |
| `author` | string or `null` | Who created this version. |
| `created` | string or `null` | When this version was created, ISO 8601. |
| `content_present` | boolean | Whether **this specific version's own body content** was captured anywhere in the package. |

**Important:** version records typically capture *metadata about a
page's history* (who edited it, and when) without capturing the actual
historical *content* of each past version — only the current
`DerivedPage.content_html` is guaranteed to hold real content. Check
`content_present` before assuming a version's own text is available
anywhere; if it's `false`, this version is a history entry only ("page
was edited by X on date Y"), not a retrievable snapshot. The manifest's
`versions` capability (section 5) summarizes this for the whole package
at a glance.

### 3.6 An attachment (`DerivedAttachment`)

One file attached to a page, from `DerivedPage.attachments`.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | An opaque, unique identifier for this attachment. |
| `filename` | string or `null` | The attachment's original file name. |
| `content_type` | string or `null` | The attachment's MIME type, e.g. `"application/pdf"`, if known. |
| `asset` | `ResolvedAsset` object | The attachment's actual file content, resolved against the package's blob store. See 3.8. |

To get an attachment's bytes: read `asset.present` and
`asset.blob_hash`, then follow the same steps as any other asset (3.8,
section 4).

### 3.7 A wiki-level manifest note

There is no separate wiki-level manifest — each page carries its own
`provenance` (3.10), which is where per-page capture problems are
recorded. There is no wiki-wide or package-wide list of "things that
went wrong"; check each page's `provenance.note`.

### 3.8 An asset reference (`ResolvedAsset`)

A reference to one binary file — either an image embedded in a page
body (`DerivedPage.assets`) or an attachment's content
(`DerivedAttachment.asset`).

| Field | Type | Meaning |
|---|---|---|
| `original_href` | string | The reference exactly as it appeared in the source (an `<img src="...">` value, or an attachment's source location). Kept verbatim for traceability; not necessarily usable as a URL in any other system. |
| `resolved_url` | string | `original_href` resolved to an absolute URL against the page it was found on, at capture time. |
| `filename` | string or `null` | The human filename recovered from the source page when the source feed exposes only a generic download name (for example, HCL Forums may report `name="blob"`). `null` means the source did not expose a filename in the archived metadata. |
| `blob_hash` | string or `null` | If the file's bytes were captured, this identifies which file in `blobs/` holds them (see section 4). `null` if the file was never captured. |
| `present` | boolean | Whether this file's bytes actually exist in the package's `blobs/` directory. **Always check this before trying to open a blob.** |
| `scope` | `"same"` or `"external"` | `"same"` means this file lived on the same deployment as the exported content (so its absence, if `present` is `false`, is a genuine capture gap worth noting). `"external"` means it referenced a third-party host outside the source deployment — those are commonly not captured at all, and that is expected, not a defect. |

**`present: false` is a signal, not silence.** If `present` is `false`
for a `scope: "same"` asset, the export ran across a same-deployment
file it could not retrieve — this is exactly the kind of gap the
manifest's `assets_not_present` count (section 5) surfaces in aggregate,
so you don't have to walk every page to notice it.

### 3.9 A link (`LinkRef`)

A hyperlink (`<a href="...">`) found in a page's `content_html`, from
`DerivedPage.links`. Every link in a page body is classified into
exactly one of three scopes, so a target system knows how to treat it.

| Field | Type | Meaning |
|---|---|---|
| `original_href` | string | The link exactly as it appeared in the source HTML. Always present, regardless of scope. |
| `resolved_url` | string or `null` | The link resolved to an absolute URL, when it could be resolved. |
| `scope` | `"in_export"`, `"hcl_deployment"`, or `"external"` | See below. |
| `target_page_id` | string or `null` | Set only when `scope` is `"in_export"`: the `id` of the page in *this package* that the link points to. |
| `target_file_id` | string or `null` | Set only when `scope` is `"in_export"`: the `id` of the FILE in *this package* the link points to, when the link named a document rather than a page. |

The three scopes, and how to rewrite each when reconstructing content
in a target system:

- **`in_export`** — the link points to something that *is* part of this
  package. `target_page_id` gives you its `id` directly, or `target_file_id`
  when the link named a document: a body that links a file keeps that file,
  because an archive has to stand on its own, and the link is not a slow
  path to the content but nothing at all. Exactly one of the two is set.
  **Rewrite this link to point at wherever you placed that item** in the
  target system (its new URL, its new page ID, whatever your target
  uses for internal links). This is the case you should always be able
  to fully repair.

  The target may be in a different component from the link: the scope is the
  package, not the container. A blog post may link to a wiki page, a forum
  topic to a blog post, and a community's Highlights front page to any of them
  -- linking into the community is what a front page is for. It reads in the
  other direction too: `target_page_id` may name a Highlights page. A single
  export can capture a whole community, and everything captured together is
  cross-linked. `target_page_id` is unique across the package, so one lookup
  finds it wherever it lives.

  A linked file is resolved the same way and just as often across containers:
  people link to documents in communities other than the one they are writing
  in. A document the export did NOT capture stays `hcl_deployment` with both
  target fields `null` -- an honest dead reference rather than one that claims
  to resolve.

- **`hcl_deployment`** — the link points back to the *same source
  deployment* the export came from (its host matches `base_url` or one
  of `hcl_hosts`), but not to a page captured in this package — it might
  point to another wiki that wasn't exported, a file-library document, or
  another application entirely on that deployment. **This link cannot be
  rewritten from the package alone** — the target it points to was never
  captured. Leave it as `original_href`/`resolved_url` (a link back to
  the source system, which may or may not still be reachable), or replace it
  with a clear placeholder, depending on what makes sense for your
  target system. Do not silently drop it — a broken-but-visible link is
  more honest than one that vanishes.

- **`external`** — the link points to a genuinely different host (the
  public web, or any system unrelated to the source deployment). **Leave
  it exactly as-is.** These links need no rewriting; they meant the same
  thing in the source and will mean the same thing anywhere else.

An *absolute* URL that happens to point back at the source deployment is
never misclassified as `external` merely for being absolute — the
`hcl_deployment` scope exists specifically to distinguish "still points
at the old system" from "points somewhere else entirely."

### 3.10 Provenance (`Provenance`)

Attached to each page (`DerivedPage.provenance`) — the only place
source-system-specific identifiers appear, and the record of anything
that went wrong capturing that entity.

| Field | Type | Meaning |
|---|---|---|
| `source_url` | string or `null` | The web address this entity's data was originally fetched from. |
| `blob_hash` | string or `null` | If the entity's own raw record (not its body/assets) was archived as a blob, this identifies it. Rarely needed by an ingester; mainly for audit/debugging. |
| `hcl_id` | string or `null` | The source system's own internal identifier for this entity. **This is the one field in the whole model where a source-system-specific identifier lives.** It has no meaning outside the source system; use it only for cross-referencing back to the original deployment (e.g. while debugging an export), never as a key you build target-system behavior around. |
| `discovered_from` | string or `null` | The URL of whatever page/feed first led the export to this entity, if recorded. |
| `note` | string or `null` | A human-readable explanation, **only present when something about capturing this entity was incomplete or went wrong** — e.g. a page whose body could not be fetched, or a feed that failed partway through. `null` means nothing unusual happened. **Always check this before assuming a page's content is complete.** This is the one field allowed to contain source-system-specific wording (it may quote an internal API error or an internal field name) — it exists purely for a human debugging the export, not for programmatic use. |

### 3.11 Blogs (`DerivedBlog` → `DerivedBlogPost` → `DerivedComment`)

A **blog** is a flat, chronological list of posts (no page hierarchy).

`DerivedBlog`: `id`, `handle` (the blog's URL handle, may be `null`), `title`, `community_uuid` (the owning community UUID, when known), `community_title` (that community's display title, when captured — carried on the container so a single container can be read without the `communities` list), `kind` (`"blog"` or `"ideation_blog"`), `alternate_url`, `post_ids` (post ids in feed order), and `posts` (a map of post id → `DerivedBlogPost`). Iterate `post_ids` for order; look posts up in `posts`.

`DerivedBlogPost`: `id`, `title`, `author`, `author_userid`, `contributors`, `content_html` (the post body — same rendering/sanitizing guidance as a page body, 3.3), `created`, `modified`, `ordinal` (feed position), `tags` (the post's tags — a list of strings, always present, empty when none), `ranks` (a name→count map, e.g. recommendations/hits — informational), `comments` (a list of `DerivedComment`), `assets` (3.8), `links` (3.9), `alternate_url`, and `provenance` (3.10).

`DerivedComment`: `id`, `author`, `author_userid`, `contributors`, `content_html`, `created`, `modified`, and `parent_comment_id` — the id of the comment this one replies to, or `null` for a top-level comment. Reconstruct the same way as page comments (3.4); do not assume only one level unless the individual data shows that constraint.

### 3.12 Forums (`DerivedForum` → `DerivedForumTopic` → `DerivedForumReply`)

A **forum** is a set of topics, each carrying an **arbitrary-depth reply tree**.

`DerivedForum`: `id`, `title`, `community_uuid` (the owning community UUID, when known), `community_title` (that community's display title, when captured — carried on the container so a single container can be read without the `communities` list), `alternate_url`, `topic_ids` (topic ids in feed order), and `topics` (map of topic id → `DerivedForumTopic`).

`DerivedForumTopic`: `id`, `title`, `author`, `author_userid`, `contributors`, `content_html` (the opening post body), `created`, `modified`, `flags` (a list of markers such as `pinned`, `locked`, `question`, `answered`), `alternate_url`, `attachments`, plus the reply tree and resolved `assets`/`links`/`provenance`. Attachments belong to the topic opening post and are not moved to the end of the thread.

- `reply_ids` — the ids of the **top-level** replies (those replying to the topic itself), in order.
- `replies` — a map of reply id → `DerivedForumReply`.
- each `DerivedForumReply` has `child_ids` — the ids of the replies nested directly under it, in order.

To render the thread, start from `reply_ids`, look each up in `replies`, then recurse into its `child_ids` — depth is unbounded.

`DerivedForumReply`: `id`, `author`, `author_userid`, `contributors`, `content_html`, `created`, `modified`, `flags` (e.g. `answer` marking an accepted answer), `child_ids`, `alternate_url`, `attachments`, resolved `assets`/`links`, and `provenance`. Attachments belong to the individual reply and must be rendered immediately after that reply's body. (Note: a reply's `provenance.hcl_id` carries the reply's bare identifier; the full source LSID is not exposed for replies.)

Forum attachment filenames are recovered from the original rendered topic/reply page when the Atom feed provides only a generic download label or omits the attachment record. Some Connections deployments expose these in a rendered `dfAttachments` list (`fileName` plus a direct download link). This is a targeted metadata fetch performed only when needed; the attachment bytes and the original feed remain archived as usual.

### 3.13 Communities (`DerivedCommunity`)

A community is a grouping layer recovered from container metadata. It does
not contain copies of the content; the content remains in `blogs` and
`forums`, and the IDs below point into those lists.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | The community UUID or another opaque community identifier. |
| `title` | string or `null` | Community display title, when captured. |
| `wiki_id` | string or `null` | ID of the community's wiki, when present. |
| `forum_ids` | list of strings | IDs of forums belonging to this community. A community may have multiple forums. |
| `blog_id` | string or `null` | ID of the community's normal blog, when present. |
| `ideation_blog_id` | string or `null` | ID of the community's ideation blog, when present. |
| `file_library_id` | string or `null` | ID of the community's file library (3.15), when captured. |
| `rich_content_id` | string or `null` | ID of the community's Highlights container (3.17), when captured. |
| `logo` | `ResolvedAsset` or `null` | The community's own picture, captured like any other asset (3.8) — its bytes are in `blobs/`, addressed by hash. `null` when the community has none, or when the run did not capture one. |

The logo is a **captured asset, not a URL**: a package read years later, with
the original system gone, still has the image. Consumers that show a community
should prefer it over any icon of their own. Note that the endpoint serving it
is not documented by HCL and was determined by inspecting the community
document; a package produced before that was settled may carry `null` here even
for a community that has a picture.

Note that a community holds **pointers only**, never content — including for
Files and Rich Content, which belong to the community rather than to any
container inside it. Both live in their own top-level lists for that reason.

The grouping is best-effort: standalone containers and containers whose feed
does not expose community metadata remain usable in their app list but do not
appear in `communities`.

### 3.14 Author-filtered packages

A package can be **narrowed to one user's involvement** — the "capture everything I authored" filter (`--author`, or the setup screen's author field). It keeps only entities that user authored or participated in, matched against `author`, `author_userid`, or `contributors` (case-insensitively, since authorship is recorded as both `atom:author` and `atom:contributor`):

- a **wiki page** kept if the user authored it or authored any of its comments — the **whole** page is kept (every comment, version, attachment, tag), plus its **ancestor pages** as `is_context: true` (3.3) so the tree path survives;
- a **blog post** kept if the user authored it or any comment — the whole post with all comments; the blog keeps only its kept posts;
- a **forum thread** kept if the user authored the topic or any reply at any depth — the whole thread; the forum keeps only its kept topics;
- containers (wiki/blog/forum) with nothing kept are dropped.

This is a property of the **derived/exported model only** — the raw archive is always complete. A consumer needs no special handling: a filtered package is a normal, smaller package (watch `is_context` if you want to distinguish authored pages from context ancestors). The filter is applied client-side because the source Search API's person filter is a *superset* (author OR contributor OR community membership), not exact authorship — see `hcl-search-api.md`.

### 3.15 File libraries (`file_libraries`)

A community's **Files** section, when one was captured. Shaped like the other
containers -- items keyed by id, plus `file_ids` in feed order -- so a consumer
that walks a blog already knows how to walk this.

| Field | Meaning |
| --- | --- |
| `id` | the library id |
| `community_uuid` | the community it belongs to; `DerivedCommunity.file_library_id` points back |
| `community_title` | that community's display title, when captured |
| `files` | `{file_id: file}`, and `file_ids` gives feed order |
| `folders` | `[{id, name, file_ids}]` |

A **file** carries `name` (exactly as Connections holds it), `title`, `author`,
`created`/`modified`, `size`, `content_type`, `version_label`, `tags`,
`folder_ids`, and `asset` -- the bytes, resolved like any other asset, so a
file's content lives in `blobs/` addressed by hash.

Two shapes are deliberate and worth respecting when you consume this:

- **`folder_ids` is a list.** Folders are an *association* over a flat store,
  not a location: a file may belong to several, and HCL's own documentation says
  so. A model that treats a folder as a file's location cannot express it.
- **`name` is verbatim**, including characters a filesystem would reject. It is
  the authority for any reconstruction. If you write these to disk you must
  make the names safe yourself -- and if you do, record what you changed, or the
  original is lost at your boundary rather than ours.
- **`excluded_by_author_filter` (boolean) separates two absences.** A file whose
  `asset` is missing or has `present: false` may be a download that failed, or a
  document nobody asked for because the run was filtered to one person
  (`author_filter` at the top level). This flag is `true` only for the second.
  Reporting both as "not captured" tells a reader that a filtered export is a
  damaged one.

### 3.16 Files on disk (`files/`) and `files.json`

When a package contains a file library it also carries the documents as
**files**, under their own names, in their folders:

```
files/<folder>/<name>      the readable copy
files.json                 what was written, and what had to change
blobs/<sha256hex>          the bytes, once, addressed by hash
```

Both copies exist on purpose. `blobs/` is the integrity anchor and holds each
byte-sequence once; `files/` is what a person opens, because a library of 65
documents as 65 hex digests is not an archive anyone can use. For many purposes
**copying `files/` out is the whole job**.

`files.json` is what makes that safe rather than lossy. Per file:

| Field | Meaning |
| --- | --- |
| `original` | the name exactly as Connections holds it |
| `name` | what was written |
| `reasons` | why they differ — **empty means they do not** |
| `path` | where it was written, relative to the package |
| `folders` | *every* folder the file belongs to, not just the one it was filed under |
| `blob` | the blob the bytes came from |
| `written` | false if the bytes were never captured |

A filesystem cannot hold everything Connections can: two files named
`Report.pdf`, a colon in a name, `Report.pdf` beside `report.pdf` (two files
here, one on Windows), or `CON.txt`, which Windows will not open whatever its
extension. Those are made safe and the change is recorded, so a target system
that **can** represent the original is entitled to restore it instead of
inheriting our compromise. `reasons` values: `forbidden-characters`,
`reserved-device-name`, `trailing-dot-or-space`, `truncated`,
`empty-after-cleaning`, `collision`.

Two properties worth relying on:

- **Names do not depend on ordering.** Disambiguation is derived from the file's
  own id, so the same file is written under the same name in every export, and a
  diff between two packages shows only what changed.
- **Paths are unique when case is folded**, so a package unzipped on Windows or
  macOS does not lose a file to a case-only collision.

### 3.17 Rich Content (`rich_content`)

A community's **Highlights** pages — the free text, tables and images its
owners put on the front page. Captured because an export that holds the wiki,
the blogs, the forums and the files but loses the page people wrote *about* all
of it has lost the thing that explains the rest.

Shaped like the other containers: items keyed by id, plus `page_ids` in the
order the community's own widget layout gives them. That order is the owners'
arrangement of their front page, so it is preserved rather than sorted.

| Field | Meaning |
| --- | --- |
| `id` | the container id (the community UUID) |
| `community_uuid` | the community it belongs to; `DerivedCommunity.rich_content_id` points back |
| `community_title` | that community's display title, when captured |
| `pages` | `{page_id: page}`, and `page_ids` gives layout order |
| `placed` | how many rich content areas the community has |
| `initialized` | how many of those had content to capture |

A **page** carries `resource_id` (what it is addressed by in the source system),
`title`, `content_html` (**the body, inline**), `author`, `created`/`modified`,
`version_label`, `assets` and `links` — the same asset and link resolution as
any other body (3.8, 3.9), so an image pasted into a Highlights page lands in
`blobs/` like any other.

**`placed` can exceed `len(page_ids)`, and that is not data loss.** An owner can
place a rich content area and never write in it; it has no content to retrieve
and no page here. The two counts are kept so a consumer can tell the difference
between *"this community has three Highlights pages"* and *"this community has
three Highlights pages and a fourth space nobody ever used"*. If you surface a
page count to a user, consider surfacing the gap too — it is the only record
that the empty area existed.

`content_html` is authored HTML from the source deployment. Treat it exactly as
you treat a wiki page body: sanitise before rendering (see 3.3).

## 4. Blobs (`blobs/`)

Every binary file the package captured — images referenced from page
bodies, and attachment content — is stored once in the `blobs/`
directory, named by the SHA-256 hash of its own bytes, in lowercase hex:

```
blobs/1f3870be274f6c49b3e31a0c6728957f...
```

To fetch the bytes for a `ResolvedAsset`:

1. Check `asset.present`. If `false`, there is nothing to fetch — the
   file was never captured. Stop here.
2. Take `asset.blob_hash`. It has the form `"sha256:<hex>"`.
3. Strip the `"sha256:"` prefix; the remainder is the file name inside
   `blobs/`.
4. Read `blobs/<hex>` as raw bytes. That is the file's exact original
   content — no re-encoding, no transformation.

Because files are named by their own content hash, **identical files
are automatically stored once**, even if multiple pages or attachments
reference the same image. Do not assume a one-to-one relationship
between the number of `ResolvedAsset` entries in the model and the
number of files in `blobs/` — expect fewer files than references,
whenever content repeats.

A blob carries no file name or extension of its own; if your target
system needs one, take it from wherever the reference came from (an
attachment's own `filename` field, section 3.6) or generate one — the
content-type, where known, is on `DerivedAttachment.content_type` for
attachments, or (for body images) can be guessed from the bytes
themselves.

## 5. The capability manifest (`manifest.json`)

`manifest.json` tells you, at a glance, what this specific package
contains and doesn't — **derived from what's actually in
`interchange.json`, never asserted independently of it.** Read this
file before writing an ingester's logic, so you build to what the data
actually supports rather than guessing.

```json
{
  "schema_version": 2,
  "minimum_reader_version": 2,
  "generator": "connections-export/interchange-1",
  "generator_version": "0.1.0",
  "generator_url": "https://pypi.org/project/connections-export/",
  "counts": {
    "wikis": 3,
    "pages": 128,
    "comments": 47,
    "versions": 128,
    "attachments": 12,
    "blogs": 2,
    "blog_posts": 30,
    "blog_comments": 55,
    "forums": 1,
    "forum_topics": 18,
    "forum_replies": 74,
    "communities": 1,
    "file_libraries": 1,
    "files": 65,
    "rich_content": 1,
    "rich_content_pages": 7,
    "blobs": 9
  },
  "capabilities": {
    "versions": "list",
    "comment_threading": "flat",
    "acls": "absent",
    "assets": "resolved",
    "assets_not_present": 1,
    "blogs": "present",
    "forums": "present",
    "communities": "present",
    "blog_comment_threading": "present",
    "forum_reply_threading": "present"
  }
}
```

**`counts`** — simple totals across the whole package: how many wikis,
pages, comments (wiki-page comments), version records, and attachments
`interchange.json` holds; how many blogs, blog posts, and blog comments;
how many forums, forum topics, forum replies, and communities; how many file
libraries and files, and how many rich content containers and pages; plus how
many distinct files actually exist in `blobs/`. Every per-app count is `0` when
that app is not in the package.

`blobs` accounts for **every** app's bytes, community documents included — a
library's files are assets like any other, and `write_package` copies them, so a
count that skipped them would understate the package by the whole library.

**`capabilities`** — a summary of what kind of data this package
carries, so you can decide how to handle each area before you start:

- **`versions`**: one of `"none"` (no version history was captured at
  all — no page in the package has any `DerivedVersion` entries),
  `"list"` (version history exists — who edited what, and when — but no
  version's own content, i.e. every `content_present` is `false`), or
  `"full"` (at least one version record's own content was actually
  captured). Check this before building any "restore an old version"
  feature — it tells you upfront whether that's even possible with this
  package.
- **`comment_threading`**: `"present"` if at least one comment anywhere
  in the package has a non-null `parent_comment_id` (3.4) — i.e. reply
  structure genuinely exists in the data — or `"flat"` if none does
  (every comment is top-level, or there are no comments at all). If
  `"flat"`, don't build UI or import logic that assumes threaded
  replies; there's nothing to thread.
- **`acls`**: `"present"` if any page's `acls` list is non-empty, or
  `"absent"` if none is. In current packages this is always `"absent"`
  — access-control data is not yet part of what gets exported — but the
  manifest states it explicitly rather than leaving you to assume.
- **`assets`**: always `"resolved"` in this format version — every
  image/attachment reference in the package has already been resolved
  to a `ResolvedAsset` with a `present` flag (3.8), rather than left as
  a raw, unverified link.
- **`assets_not_present`**: the total count of `ResolvedAsset` entries
  (across body images and attachments, every page) where `present` is
  `false` — i.e. a reference existed but the file itself could not be
  retrieved. A non-zero count doesn't necessarily mean something is
  broken (an `external`-scope asset failing to capture is normal and
  expected) — but it's always worth knowing the number before assuming
  every image made it across. Cross-reference with individual
  `ResolvedAsset.scope` values (3.8) to see which gaps, if any, were
  same-deployment content that should have been captured.
- **`communities`**: `"present"` when the package contains at least one
  `DerivedCommunity`; otherwise `"absent"`. This is grouping metadata only;
  the actual blog/forum content remains in the top-level `blogs` and
  `forums` lists.

**The manifest never claims more than the data backs up.** If you find
a mismatch between what the manifest says and what you observe walking
`interchange.json` yourself, trust `interchange.json` — the manifest is
a derived summary, not a second source of truth.

## 6. Provenance file (`provenance.json`)

A small JSON object recording where the whole package came from:

```json
{
  "base_url": "https://wikis.example.com",
  "source_version": "8.0",
  "run_id": "2026-07-20T12-00-00Z",
  "hcl_hosts": ["wikis.example.com", "files.example.com"],
  "generated_at": "2026-07-20T12:00:00Z",
  "generator": "connections-export/interchange-1",
  "generator_version": "0.1.0",
  "generator_url": "https://pypi.org/project/connections-export/"
}
```

- `base_url`, `source_version`, `run_id`, `hcl_hosts` mirror the
  same-named fields on the top-level `Interchange` object (3.1). A `run_id` is
  the instant that run began, in a form that is legal as a filename -- do not
  parse it as a timestamp; it is an identifier.
- `generated_at` is the timestamp the package itself was written, ISO
  8601. This is when the *package* was produced, which may be later
  than when the underlying content was originally captured from the
  source system.
- `generator`, `generator_version` and `generator_url` name the program
  that wrote the package, the release of it, and where that release can
  be obtained. `manifest.json` carries the same three. A package is
  opened long after it is written, sometimes by someone holding nothing
  but the directory — an address is more use to them than a name.

None of these fields are needed to reconstruct content — they exist for
traceability (which run produced this package, when, and with what).

## 7. Reconstructing content in a target wiki

This is the step-by-step an ingester follows to bring a package's
content into a different system. The steps are ordered so that
everything a later step needs already exists by the time it runs.

1. **Read the manifest first** (section 5). Know upfront whether
   threading, full version content, and ACLs are present before you
   design your import logic around assumptions the data can't back up.

2. **Create every page, without wiring up hierarchy yet.** Walk every
   wiki's `pages` map (3.2) and create one page/document in the target
   system per `DerivedPage`, using `title` (falling back to `label`) for
   its name and `content_html` for its content. Keep a mapping from each
   source `id` to whatever identifier the target system assigns it —
   every later step needs this mapping.

3. **Set hierarchy and order**, now that every page exists and you have
   the id mapping. For each wiki, walk `root_page_ids` (3.2) — those are
   the wiki's top-level pages, already ordered. For each page, look at
   its `child_ids` (3.3) — also already ordered — and set each child's
   parent in the target system to the page you're on. Recurse down
   `child_ids` to cover the whole tree. You never need to compute
   sibling order yourselves; `ordinal` (3.3) has already been applied to
   produce both `root_page_ids` and every page's `child_ids` in the
   right order.

4. **Attach comments, versions, and attachments** to each page:
   - **Comments** (3.4): create one comment per `DerivedComment`. If
     the manifest says `comment_threading: "present"`, wire up replies
     using `parent_comment_id` (group by it, same technique as 3.4);
     otherwise create every comment as top-level.
   - **Versions** (3.5): if the manifest says `versions: "full"`,
     restore whichever versions have `content_present: true` as actual
     historical snapshots; for `"list"`, only the metadata (who/when) is
     available — represent that as a history entry if your target
     system supports one, otherwise it's safe to skip. `"none"` means
     there's nothing to attach here at all.
   - **Attachments** (3.6): for each `DerivedAttachment`, check
     `asset.present` (3.8); if true, fetch its bytes from `blobs/`
     (section 4) and upload it to the target system under `filename`;
     if false, there is nothing to attach — optionally record that an
     attachment named `filename` existed but its content was not
     captured.
   - **Tags** (3.3): apply each string in the page's `tags` to the
     target system's own tagging mechanism. The reference **Obsidian
     ingester writes them as native `tags:` YAML frontmatter**, so a tag
     is searchable and filterable in Obsidian; a wiki target might apply
     them as page labels. An empty `tags` list means there is nothing to
     apply. (Blog posts, 3.11, carry `tags` the same way.)

5. **Rewrite links and body assets**, now that step 2's id mapping
   exists:
   - For each `LinkRef` in a page's `links` (3.9): if `scope` is
     `"in_export"`, rewrite the link's `href` in `content_html` to point at
     the target-system location of `target_page_id` (via your mapping
     from step 2). If `"hcl_deployment"`, leave it pointing at the
     original source system or replace it with a clear placeholder — it
     cannot be repaired from this package alone. If `"external"`, leave
     it untouched.
   - For each `ResolvedAsset` in a page's `assets` (3.8): if `present`
     is true, fetch its bytes from `blobs/` (section 4), upload it to
     the target system, and rewrite the corresponding reference in
     `content_html` to point at its new location. If `present` is false,
     the image cannot be restored — leave a visible gap (a broken image
     reference, or a note) rather than silently dropping the reference;
     that keeps the loss visible instead of hidden.

6. **Note what your target system can't accept.** Some source content
   may have no equivalent in your target system at all (a metadata
   field your target doesn't model, an ACL entry your target represents
   differently). **This is where loss, if any, actually happens** — in
   the writer you're building, against a package that still has the
   data. Record what you chose to drop and why; the package itself lost
   nothing up to this point.

### 7.1 How much a target accepts varies widely

Step 6 is not a formality. Targets differ enormously in how much of
`content_html` they will store, and that difference — not the package —
determines what an ingester can achieve.

The reference **Obsidian ingester** sits at the permissive end: its
target is a directory of Markdown files, so it can write whatever it
decides to write, and every design choice is the ingester's own.
Building against a file-based target is largely a matter of deciding
how to represent the model in section 3.

Other targets impose a fixed content model that the ingester cannot
widen. **SharePoint site pages** are a documented example, and a common
first assumption because their ingestion API is straightforward:
`POST /sites/{site-id}/pages` is GA in Microsoft Graph v1.0, and
`textWebPart` carries an `innerHtml` string that appears to accept a
page body directly. The API accepts the call; the rich-text editor then
governs what is retained. Its constraints, as encoded in Microsoft's own
migration tooling ([`HtmlTransformator`][spht], the component that
converts classic SharePoint wiki HTML into editor-safe HTML):

- **Headings** are limited to `h2`–`h4`. `h1` maps to `h2`; `h5` and
  `h6` become formatted text.
- **CSS does not survive.** On block elements only `margin-left` and
  `text-align` are retained; on inline elements only `width` and
  `text-align`. Colour and size are not CSS but a closed set of named
  classes (`Small`…`XxxLarge`; `Red`, `RedDark`, `Yellow`, …), so
  arbitrary values have no representation.
- **Inline `<img>` and `<iframe>` are dropped** when the page is next
  opened for editing. Images have to become separate image web parts,
  or use SharePoint's own inline-image mechanism after upload.
- **Tables** must carry specific wrapper markup
  (`canvasRteResponsiveTable` / `tableWrapper`) and a table style class
  from a fixed set; nested tables are removed. A table written without
  the wrappers renders correctly and is discarded on first edit.
- **No `<pre>` or `<code>`**, and no horizontal rule (`<hr>` becomes two
  `<br>`).

Two properties of this are worth noting for any target, not just this
one. First, the losses are **silent**: the write succeeds, `GET` returns
what was sent, and the page renders correctly — some of the divergence
appears only when a person later edits the page. An ingester's own tests
will not necessarily observe it. Second, the constraint is in the
target's editor, not in its API, so it is not visible from the API
reference.

None of this is an argument for or against any particular target. It is
the reason step 6 asks for an explicit record: **check what your target
retains before assuming `content_html` can be handed over intact**, and
write down what it does not. Where a target's content model is narrower
than the package, options include rendering the body through the
target's own extension mechanism (for SharePoint, an SPFx web part),
storing the original HTML alongside as a file, or accepting and
documenting the reduction.

[spht]: https://github.com/pnp/pnpframework/blob/dev/src/lib/PnP.Framework/Modernization/Transform/HtmlTransformator.cs

## 8. Versioning

`Interchange.schema_version` (an integer, currently `2`) identifies the
shape of `interchange.json`. `manifest.json`'s own `schema_version`
field always mirrors it.

### Version 2 is a breaking change from version 1

This is the explicit call-out that version 1 of this document promised would
be made if a breaking change ever became necessary. **Version 2 removes and
renames fields that version 1 documented.** An ingester written against v1
must not read a v2 package.

What changed, and why:

| v1 | v2 | Why |
|---|---|---|
| `DerivedPage.content_html` | `content_html` | Every other content type already called it `content_html`. One name for one thing. |
| `published` (blog posts, forum topics and replies, blog comments) | `created` | Four spellings across five apps for "when was this made". |
| `updated` (rich-content pages) | `modified` | Same, for "when did it last change". |
| `DerivedComment` | `DerivedComment` | With the timestamps unified the two classes were identical. |

Forum topics and replies also gain `modified`, which they did not previously
carry.

This was done because **nothing had been released**: no package written by any
version of this tool existed outside a developer's machine, so there was no
ingester to break and no migration to write. That will not be true of the next
change, which is why the guarantee below is now enforced rather than promised.

### The guarantee, from version 2 onward

This format evolves **additively**: future versions may add new entity kinds
and new optional fields, without removing or repurposing anything documented
here. The schema supports wikis, blogs, forums, community file libraries,
rich content and community grouping; consumers must not assume `wikis` is the
only populated top-level content list. An ingester built against this document
should keep working against a later `schema_version` unmodified, as long as it:

- ignores JSON object keys it doesn't recognize, rather than rejecting them;
- **checks `minimum_reader_version` in `manifest.json` and refuses the package
  if that number is higher than the version it was built against.**

That second rule is what makes the first one safe. Treating any higher
`schema_version` as "a superset of what I know how to read" is only correct
while nothing is ever removed — and if that assumption is ever wrong again, an
ingester obeying it does not fail. It finds the fields it knows absent, derives
empty bodies, and reports success. A silent gap is the one failure this project
refuses everywhere else, so the format now carries the means to make it loud:

```json
{ "schema_version": 2, "minimum_reader_version": 2 }
```

`minimum_reader_version` is the oldest reader that can read this package
correctly. It stays put while changes are additive, and rises only when
something is removed or repurposed. A reader that checks it degrades to a
clear refusal instead of a quiet, wrong answer.
