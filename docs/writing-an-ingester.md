# Writing an ingester

A brief for adding a new output format to `connections-export ingest` —
written for someone (or an LLM) working from this repository with an
example of the target format to hand, and no access to an HCL Connections
deployment. None is needed: everything below runs against the built-in
synthetic deployment.

## What an ingester is

A capture produces an **archive**: every response as it came off the wire.
From it, the tool derives an **interchange model** — wikis, blogs, forums,
files and front pages, normalized into one shape with no HCL vocabulary in
it — plus a **blob store** of every image and attachment, keyed by hash.

An ingester takes that model and a way to read blobs, and writes a target.
It never touches the network. The contract is written out step by step in
[`docs/reference/interchange-format.md`](reference/interchange-format.md) §7
("Reconstructing content in a target"). Three ingesters ship with the tool,
each that contract as working code for a different kind of target — read
the one closest to yours first.

## Three examples, three kinds of target

The model is the same for all three; what differs is what the target can
hold, how it links, and what it would execute. Each shows one way to adapt.

- **Obsidian** ([`ingest/obsidian.py`](../connections_export/ingest/obsidian.py))
  — a *notes app*: a folder of Markdown notes that the app indexes itself.
  The page tree becomes nested folders; links become `[[wikilinks]]`, which
  the app resolves **by note name** across the whole vault, so the exporter
  allocates every name once and disambiguates collisions (two "Home" pages,
  names that differ only in case) and writes a path where a name repeats.
  Images and attachments go into one shared `attachments/` folder. Nothing
  is built or executed later, so plain Markdown is the default.
- **Jekyll** ([`ingest/jekyll.py`](../connections_export/ingest/jekyll.py))
  — a *blog-shaped static-site generator*: everything is a dated post in a
  flat `_posts/` folder, so a wiki's hierarchy has no place and is carried in
  front matter instead. Links go through the site's own `relative_url` so
  they honour its base URL. The target **executes a template language
  (Liquid) over the content** when the site is built, so every piece of
  captured text is escaped for it — the exporter's own `relative_url`
  expressions are the only Liquid left. Posts name `layout: post`; layouts
  and `_config.yml` stay the site owner's.
- **Hugo** ([`ingest/hugo.py`](../connections_export/ingest/hugo.py))
  — a *section-based static-site generator*: the page tree maps directly onto
  nested sections and page bundles (a folder per page, its images beside it),
  with `weight` keeping the wiki's own order. Links are `relref`s, which Hugo
  checks at build time; its shortcodes run anywhere in content, so captured
  `{{< … >}}` is written in Hugo's comment form. It writes content only — the
  site's templates are its owner's — with an optional starter site
  (`ingest/hugo_starter.py`) to view it, and lays out an export of several
  communities community first.

Whichever is closest: the part to copy is how it plans every location
**before** writing anything (so a link can point at an item written later),
how it rewrites in-export links and captured images to those locations, and
how it treats text the target would interpret.

## Shared building blocks

Reuse these rather than writing your own — each carries lessons from the
exporters above:

- [`ingest/_markdown.py`](../connections_export/ingest/_markdown.py) — HTML to
  Markdown that keeps captured text inert (text that looks like HTML or
  Markdown is escaped, `javascript:` links dropped), `escape_inline` for
  titles and names, and helpers for comments and replies that keep their
  paragraphs. Front matter goes through `_yaml_scalar` in `obsidian.py`, which
  the Jekyll and Hugo writers share.
- [`ingest/_bodies.py`](../connections_export/ingest/_bodies.py) — the
  page-content modes every exporter offers (`markdown`, `mixed`, `html`,
  `raw`): `render_body` with your own link and image rewriting.
- Combined exports: a writer that accepts the `combined` argument gets
  several archives in one export for free — the combining, deduplication and
  cross-archive links happen before it runs (`derive/combine.py`).

## The shape to copy

Two functions, as each of the three has:

```python
def write_<target>(interchange: Interchange, blob_reader: BlobReader, out_dir) -> <Stats>:
    """The writer. Takes an already-loaded model and `blob_hash -> bytes | None`."""

def from_source(source, out_dir) -> <Stats>:
    """Thin: `source.get_model()` + `source.get_blob(hash)` -> the writer."""
```

`ModelSource` (in `connections_export/gui/model_source.py`) already opens an
archive directory, a `.zip` of one, or a written package, and applies the
`--author` filter — so an ingester takes a `ModelSource` and never parses
anything itself. `cli._content_source(path, author=...)` turns a path into
one and says what kind it found.

Then:

1. Add the module under `connections_export/ingest/`, export it from
   `connections_export/ingest/__init__.py`.
2. Add the format name to `--format` in `ingest_main` (`connections_export/cli.py`)
   and dispatch on it. Keep `obsidian` as the default. To reach it from the
   console as well, add the format to `POST /api/ingest`
   (`connections_export/gui/routes/ingest.py`) and the export dialog.
3. If the target needs a library the base install lacks, add it as an
   optional extra in `pyproject.toml` and import it lazily inside the writer,
   so a plain install still starts and says what is missing.

## The model, in one screen

Every piece of content — page, post, topic, reply, file, front page — is a
`DerivedItem` (`connections_export/derive/model.py`):

| field | what it is |
| --- | --- |
| `id`, `title`, `author`, `author_userid`, `contributors` | identity and authorship |
| `content_html` | the body, as captured — convert it however the target needs |
| `created`, `modified` | ISO 8601 |
| `assets` | every `<img>`/attachment the body references: `original_href`, `present`, `blob_hash` |
| `links` | every `<a>` in the body, classified `in_export` (with `target_page_id`), `hcl_deployment`, or `external` |
| `provenance` | where it came from: `source_url`, `hcl_id` |

Containers add structure: a `DerivedWiki` has `pages` and a hierarchy
(`parent_id`, `root_page_ids`); a `DerivedBlog` has `posts` in `post_ids`
order; a `DerivedForum` has `topics`, each with `replies` as a tree
(`reply_ids` at the top, `child_ids` below); pages and topics carry
`attachments`, pages and posts carry `comments` (threaded by
`parent_comment_id`).

Two rules the format insists on, and each of the three writers shows how:

- **Nothing is dropped silently.** An asset with `present: false` was
  referenced and not captured; write a visible marker, never nothing.
- **In-export links become links in the target.** `link.scope ==
  "in_export"` and `link.target_page_id` name the item it points at; map that
  to wherever the target put that item. Build one id→location map across
  every container of every app first, so a post can link to a page.

## Working without a deployment

The synthetic deployment is the whole test bed:

```python
from connections_export.gui.demo import run_demo
from connections_export.gui.model_source import ModelSource

run_demo((lambda e: None), archive_dir="archive", delay=0)   # 3 wikis, 2 blogs, 2 forums
source = ModelSource.from_archive("archive")
model = source.get_model()
```

Or from the shell: `uv run connections-export serve --open`, capture the
demo community from the console, then
`uv run connections-export ingest --format <target> --archive <dir> --output <out>`.

Tests live in `tests/ingest/`. `test_ingest_takes_what_a_capture_writes.py`
is the end-to-end pattern: a real demo capture, the command a person types,
assertions on the files it wrote. `test_obsidian.py` builds a small model by
hand for the fine-grained cases (a missing asset, a nested reply, a link
between apps). Do both. Then add your writer to
`test_exporters_untrusted_content.py`: captured content is written by other
people, and that battery checks nothing in it becomes live HTML, a script
link, or a command the target runs when it builds.

## Building it in the open

The repository builds every push to a branch on GitHub's free runners — CI
across Linux, macOS and Windows, plus the four executables as artifacts — and
a branch can never publish: releases happen only on a `v*` tag. So develop on
a branch, push, and read the results; nothing reaches PyPI or the release
page until a maintainer tags.

`uv sync && uv run pytest tests/ingest -q` is the local loop. `uv run ruff
check . && uv run ruff format .` before pushing — CI runs the same two.
