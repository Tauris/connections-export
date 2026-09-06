(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Point vendored PDF.js at its vendored worker (both served from /vendor,
  // never a CDN). Set here rather than an inline <script> so the HTML stays
  // script-free (the split-assets guarantee).
  // The worker is wired up in vendor/pdfjs-boot.mjs, which is where pdf.js is
  // imported: a module script is deferred, so it has not run yet at this line.

  let items = [], byId = new Map(), clockTimer = null, startTs = 0, openId = null;
  //: `ok` is what came over the wire; `reused` is what the archive already
  //: held and an update did not have to ask for. Counting the second as the
  //: first made an update that correctly skipped 162 of 196 items report
  //: "Fetched · saved: 196" -- a working feature that looked broken, and a
  //: number that could never show a regression because it never moved.
  let disc = 0, ok = 0, reused = 0, retry = 0, fail = 0, blobs = 0, stopped = 0;
  let warnings = [], notes = [], failures = [], fetchTimes = [], spark = [], peak = 0;
  let kindCounts = {};  // per-type tally of fetched items, for the live breakdown

  // Content kinds worth counting separately, in display order (feed/body/
  // version fetches are plumbing, not content, so they're omitted).
  const TYPE_LABELS = {
    page: "pages", post: "posts", topic: "topics", reply: "replies",
    comments: "comments", rich_content: "highlights pages",
    file: "files", asset: "assets",
  };

  // Assets are the one type the live strip cannot count the way the verdict
  // does. The verdict counts assets present in the derived archive; live can
  // only count what has been fetched, and some of those belong to entities
  // pruning later drops -- 31 fetched against 23 archived in a demo run. So
  // the live strip says what it actually measured rather than quietly
  // disagreeing with the verdict under a shared label.
  const LIVE_TYPE_LABELS = Object.assign({}, TYPE_LABELS, { asset: "assets fetched" });

  // Render a per-type count strip into `elId` from a {kind: n} map; hides it
  // when everything is zero.
  // These are an INVENTORY -- what the archive holds -- not a tally of work
  // done. A comment count comes from the item's own `comment_count`, so an
  // update that re-read nothing still says "243 comments", and someone who
  // knows nothing changed overnight reads that as 243 comments re-fetched.
  // What was actually asked of the deployment is the Fetched/Reused pair
  // above. The strip says which of the two it is.
  function typeCountsHtml(counts, labels) {
    const parts = [];
    for (const kind of Object.keys(labels)) {
      const n = counts[kind] || 0;
      if (n > 0) parts.push('<span class="tc"><b>' + n + "</b> " + labels[kind] + "</span>");
    }
    if (!parts.length) return "";
    return '<span class="tc-lead">this archive holds</span>' + parts.join("");
  }

  function renderTypeCounts(counts, elId) {
    const el = $(elId);
    if (!el) return;
    const labels = elId === "type-counts" ? LIVE_TYPE_LABELS : TYPE_LABELS;
    const html = typeCountsHtml(counts, labels);
    el.hidden = !html;
    el.innerHTML = html;
  }

  // What each community contributed, when a run captured more than one.
  // A single total for a set answers "how much" and not "from where", and
  // "from where" is the question a set makes possible to get wrong.
  function renderCommunityBreakdown(model, elId) {
    const el = $(elId);
    if (!el) return;
    const communities = communitiesIn(model).filter((community) => community.uuid);
    if (communities.length < 2) {
      el.hidden = true;
      el.innerHTML = "";
      return;
    }
    el.hidden = false;
    el.innerHTML = communities
      .map((community) => {
        const counts = modelTypeCounts(scopeForCommunity(model, community.uuid));
        return '<div class="v-community"><span class="v-community-name">' +
          escapeHtml(community.title || community.uuid) + "</span>" +
          '<span class="type-counts">' + typeCountsHtml(counts, TYPE_LABELS) + "</span></div>";
      })
      .join("");
  }

  // Per-type counts of what's actually KEPT in the derived (filtered) model --
  // the honest "what you got", unlike the raw fetch count which, with an author
  // filter, still counts the text of pruned items.
  function modelTypeCounts(model) {
    const c = { page: 0, post: 0, topic: 0, reply: 0, comments: 0, attachments: 0,
                rich_content: 0, file: 0, asset: 0 };
    const present = (arr) => (arr || []).filter((a) => a && a.present).length;
    for (const w of model.wikis || []) {
      for (const id in (w.pages || {})) {
        const p = w.pages[id];
        if (p.is_context) continue;  // ancestors kept only for the tree path
        c.page++;
        c.comments += (p.comments || []).length;
        c.attachments += (p.attachments || []).length;
        c.asset += present((p.assets || []).map((a) => a.asset || a));
        c.asset += present((p.attachments || []).map((a) => a.asset || a));
      }
    }
    for (const b of model.blogs || []) {
      for (const id in (b.posts || {})) {
        const p = b.posts[id];
        c.post++; c.comments += (p.comments || []).length;
        c.asset += present((p.assets || []).map((a) => a.asset || a));
      }
    }
    for (const f of model.forums || []) {
      for (const id in (f.topics || {})) {
        const t = f.topics[id];
        c.topic++; c.reply += Object.keys(t.replies || {}).length;
        c.asset += present((t.assets || []).map((a) => a.asset || a));
        c.asset += present((t.attachments || []).map((a) => a.asset || a));
        for (const reply of Object.values(t.replies || {})) {
          c.asset += present((reply.assets || []).map((a) => a.asset || a));
          c.asset += present((reply.attachments || []).map((a) => a.asset || a));
        }
      }
    }
    for (const rc of model.rich_content || []) {
      for (const id of rc.page_ids || []) {
        const p = (rc.pages || {})[id];
        if (!p) continue;
        c.rich_content++;
        c.asset += present((p.assets || []).map((a) => a.asset || a));
      }
    }
    for (const lib of model.file_libraries || []) {
      // Counted as files, not as assets: a community document is a thing the
      // user asked for by name, not an image that happened to be embedded in
      // something else. Only files whose bytes actually arrived are counted --
      // this strip is "what you got".
      for (const id of lib.file_ids || []) {
        const f = (lib.files || {})[id];
        if (f && f.asset && f.asset.present) c.file++;
      }
    }
    return c;
  }
  const nodeEls = new Map();
  // Distinct asset URLs seen this run -- see the note in `liveFetched`.
  const assetUrls = new Set();
  // Live tree entities are keyed by kind AND id, never by id alone. Two apps
  // can and do use the same id -- a run of the demo community had six ids
  // claimed twice (a blog post and a community file sharing a UUID, a file
  // and a forum topic, a file and a highlights resource) -- and with a single
  // id-keyed map the second arrival was silently dropped: no node, no count,
  // no trace. `it.id` stays the real entity id, because that is what the
  // reader matches against the derived model; only the map key is qualified.
  const _liveKey = (kind, id) => kind + ":" + id;
  // Raw id -> map key, for the events that can only name an entity by its raw
  // id (`pruned`, and a warning's `ref`). First writer wins, which is what
  // those events meant before ids were qualified.
  const keyByRawId = new Map();

  // ---- data-source mode: "connecting" | "live" (real SSE events from
  // hcl-serve --demo) ----
  let mode = "connecting";
  // The body last POSTed to `/api/start` (setup screen, "Start
  // import"/"Run the demo"): `/events` no longer
  // auto-starts a run, so "Restart" now re-POSTs this instead of just
  // reconnecting to a stream nothing is feeding anymore.
  //: The last run's request body. Defaults to a demo shape so the pieces that
  //: describe a run have something to read before one exists -- which is why
  //: `runHasStarted` is separate: the default is a placeholder, not a claim
  //: that this session is a demo session.
  let lastStartBody = { demo: true };
  let runHasStarted = false;
  let liveSource = null;
  let liveGotEvent = false;
  let liveRunning = false;
  let livePendingPageId = null;
  // in-export links on the entity currently open in the reader, keyed by their
  // original body href -> target page id, so the sandboxed (script-less) body
  // frame's own <a> elements (prose AND inside inline SVG) can be wired by the
  // PARENT to navigate the reader on click ("the SVG link works in the PDF but
  // not in the reader"). Rebuilt on every reader body render.
  let readerNavLinks = new Map();
  const liveWikiGroups = new Set();   // wiki labels that already have a header node in the tree
  const liveCommunityGroups = new Set();  // community uuids that already have a header node
  // How many communities this run was asked to capture, known before it starts
  // because the console is what asked. A single-community run reads as it
  // always did -- wiki -> pages -- with no heading above it repeating what the
  // title bar already says.
  let liveCommunityCount = 0;
  // "wiki:eng-handbook" -> {uuid, title}. Built from the request the console
  // itself sent: every component was ticked under a community in the picker,
  // so the mapping is already known before the first event arrives. The crawl
  // cannot supply it -- its events name a wiki or a blog, and the community
  // marker lives on a feed a scoped crawl never fetches.
  let liveCommunityByComponent = new Map();
  //: uuid -> the last row placed for that community, so the next one lands
  //: under the right heading. A demo run crawls app-by-app across the whole
  //: set, so community B's wiki arrives after community A's and appending
  //: would file it under A.
  const liveCommunityTail = new Map();
  // One extra indent for everything under a community heading, applied in
  // `addTreeNode` so the twenty places that create a node keep saying what
  // their own depth means and none of them has to know about grouping.
  let liveDepthOffset = 0;
  const livePageDepth = new Map();    // page id -> tree depth, for nesting pages under their parent

  const pad = (n) => String(n).padStart(2, "0");
  const fmtClock = (s) => pad(Math.floor(s/60)) + ":" + pad(s%60);
  const nowStamp = () => "+" + ((Date.now()-startTs)/1000).toFixed(1) + "s";
  // Inline icon markup for strings that become innerHTML. Mirrors the sprite
  // in console.html; `currentColor` means it inherits the surrounding style.
  const icon = (name) => '<svg class="icon" aria-hidden="true"><use href="#i-' + name + '"/></svg>';

  const escapeHtml = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));

  // ================= READER-PURE-BEGIN =================
  // Pure, DOM-free reader logic (Front-end reader
  // (static/console.html)): tree order, comment threading, link
  // routing, blob-hash validation, inline-image rewriting, the
  // sandboxed body render, and search. Kept dependency-free -- no
  // reference to `document`/`window`/outer closures beyond this block
  // -- specifically so it can be exercised directly outside a browser
  //. Nothing here is a testing-only shim: this is the actual
  // production logic the reader UI below calls.

  const BLOB_HASH_RE = /^[0-9a-f]{64}$/;

  function isValidBlobHash(hash) {
    return typeof hash === "string" && BLOB_HASH_RE.test(hash);
  }

  // `/api/blob/{hash}`: accepts either the
  // model's `"sha256:<hex>"` form or a bare hex digest; anything that
  // isn't a validly-shaped hash resolves to no URL at all (never a
  // malformed request).
  function blobUrl(blobHash) {
    if (!blobHash) return null;
    const hex = blobHash.includes(":") ? blobHash.split(":").pop() : blobHash;
    return isValidBlobHash(hex) ? "/api/blob/" + hex : null;
  }

  // Tag-stripping for the search index: body
  // HTML -> plain text, common entities decoded, whitespace collapsed.
  function stripTags(html) {
    return String(html || "")
      .replace(/<[^>]*>/g, " ")
      .replace(/&nbsp;/gi, " ")
      .replace(/&amp;/gi, "&")
      .replace(/&lt;/gi, "<")
      .replace(/&gt;/gi, ">")
      .replace(/&quot;/gi, '"')
      .replace(/&#39;/gi, "'")
      .replace(/\s+/g, " ")
      .trim();
  }

  // The model's own pre-ordered hierarchy (Build the nav
  // tree from wikis[].root_page_ids + each page's child_ids (pre-
  // ordered -- never re-sort)). Depth-first, in exactly the order the
  // model lists ids in -- no `.sort()` anywhere here, deliberately: a
  // test asserts order is taken from the model, not recomputed.
  function preorderWikiPages(wiki) {
    const out = [];
    const pages = (wiki && wiki.pages) || {};
    const walk = (id, depth) => {
      const page = pages[id];
      if (!page) return; // a dangling id is skipped, never fabricated
      out.push({ id, page, depth });
      (page.child_ids || []).forEach((childId) => walk(childId, depth + 1));
    };
    ((wiki && wiki.root_page_ids) || []).forEach((id) => walk(id, 0));
    return out;
  }

  // The forum topic's reply tree, flattened depth-first with depth
  //. Mirrors `preorderWikiPages`: a topic keeps
  // its replies id-keyed (`reply_ids` -> `replies[id]` -> each reply's own
  // `child_ids`), so an arbitrary-depth thread is walked without the JSON
  // ever nesting. Order is taken verbatim from the model's id lists -- no
  // `.sort()`, deliberately -- and a dangling id is skipped, never
  // fabricated.
  function preorderForumReplies(topic) {
    const out = [];
    const replies = (topic && topic.replies) || {};
    const walk = (id, depth) => {
      const reply = replies[id];
      if (!reply) return; // a dangling id is skipped, never fabricated
      out.push({ id, reply, depth });
      (reply.child_ids || []).forEach((childId) => walk(childId, depth + 1));
    };
    ((topic && topic.reply_ids) || []).forEach((id) => walk(id, 0));
    return out;
  }

  // Threads comments by `parent_comment_id` (comments
  // threaded via parent_comment_id): top-level comments keep their
  // original (model) order, each immediately followed by its own
  // replies, recursively, also in original order. A comment whose
  // declared parent isn't one of this page's own comments is treated
  // as top-level -- flat, never dropped.
  function threadComments(comments) {
    comments = comments || [];
    const ids = new Set(comments.map((c) => c.id));
    const byParent = new Map();
    comments.forEach((c) => {
      const parent = c.parent_comment_id && ids.has(c.parent_comment_id) ? c.parent_comment_id : null;
      if (!byParent.has(parent)) byParent.set(parent, []);
      byParent.get(parent).push(c);
    });
    const out = [];
    const walk = (parent, depth) => {
      (byParent.get(parent) || []).forEach((c) => {
        out.push({ comment: c, depth });
        walk(c.id, depth + 1);
      });
    };
    walk(null, 0);
    return out;
  }

  // Routes one `LinkRef` by `scope`.
  function classifyLink(link) {
    if (link.scope === "in_export" && link.target_page_id) {
      return { kind: "in_export", targetPageId: link.target_page_id, href: link.original_href };
    }
    // A link naming a captured DOCUMENT. Marking it `in_export` is
    // bookkeeping; the promise is that it opens the file from the archive
    // alone, so it is routed to the bytes rather than to a page that is not
    // there.
    if (link.scope === "in_export" && link.target_file_id) {
      return {
        kind: "in_export_file",
        targetFileId: link.target_file_id,
        href: link.original_href,
      };
    }
    if (link.scope === "hcl_deployment") {
      return {
        kind: "hcl_deployment",
        href: link.resolved_url || link.original_href,
        label: "original system",
      };
    }
    return { kind: "external", href: link.resolved_url || link.original_href };
  }

  // Rewrites inline `<img src>` references that resolve to a present
  // archived blob to `/api/blob/{hash}` (Inline images
  // inside the body are rewritten... via the page's assets map, or
  // left as the recorded reference if unresolved).
  function resolveBodyImages(bodyHtml, assets) {
    if (!bodyHtml) return bodyHtml;
    const byHref = new Map();
    (assets || []).forEach((a) => {
      if (!a.present || !a.blob_hash) return;
      const url = blobUrl(a.blob_hash);
      if (!url) return;
      byHref.set(a.original_href, url);
      if (a.resolved_url) byHref.set(a.resolved_url, url);
    });
    if (byHref.size === 0) return bodyHtml;
    return bodyHtml.replace(/(<img\b[^>]*?\bsrc\s*=\s*)(["'])(.*?)\2/gi, (whole, prefix, quote, src) => {
      const resolved = byHref.get(src);
      return resolved ? prefix + quote + resolved + quote : whole;
    });
  }

  function escapeAttr(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
    ));
  }

  // ---- THE SECURITY-SENSITIVE RENDER (Sandboxed body
  // rendering) ----
  // `body_html` is untrusted author content. It is injected via
  // `srcdoc` into an iframe carrying a bare `sandbox` attribute --
  // deliberately with NO `allow-scripts` token (an embedded <script>
  // never executes) and NO `allow-top-navigation` (it can never
  // navigate the app). Author CSS (inline styles, <style> blocks)
  // still applies *inside* that sandboxed frame, so fidelity is kept
  // while the content is inert. This function must never be changed
  // to add sandbox tokens beyond what the design calls for.
  function sandboxedBodyMarkup(bodyHtml) {
    // Inject color-scheme + background matching the outer theme so the iframe
    // body doesn't show white in dark mode. Reads the current effective theme
    // from the root data-theme attribute or prefers-color-scheme.
    // DOM-free-safe (this fn is unit-tested under Node): default to light
    // when there is no document/window to read a theme from.
    const _root = (typeof document !== "undefined") ? document.documentElement : null;
    const _theme = _root ? _root.getAttribute("data-theme") : null;
    const isDark = _theme === "dark"
      || (!_theme && typeof window !== "undefined" && !!window.matchMedia
          && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const bg  = isDark ? "#16212e" : "#ffffff";
    const fg  = isDark ? "#e7eef5" : "#16202b";
    const themeStyle = (
      '<meta name="color-scheme" content="' + (isDark ? "dark" : "light") + '">' +
      '<style>' +
      'html,body{margin:0;padding:8px 12px;background:' + bg + '!important;}' +
      // Do NOT set a body text color — author CSS (code blocks, etc.) must
      // control its own contrast. Only the background is ours to override.
      '' +
      '</style>'
    );
    const doc = '<!doctype html>' + themeStyle + '<body>' + (bodyHtml || "") + "</body>";
    // `allow-same-origin` (and NOTHING else — crucially NO `allow-scripts`) so
    // author <script> stays inert while the *parent* can still read the frame's
    // scrollHeight to auto-size it (see the `load` handler after READER-PURE-END).
    // Adding `allow-scripts` here would execute untrusted captured HTML — never do it.
    return '<iframe class="body-frame" sandbox="allow-same-origin" srcdoc="' + escapeAttr(doc) + '"></iframe>';
  }

  // Search over the fetched model: titles + body
  // text (tags stripped) + comment text. Covers every page of every wiki
  // and every blog post and forum topic too.
  // Each result carries a `kind` (`page`/`post`/`topic`) plus the ids the
  // renderer needs to route a click; wiki results keep their original
  // shape (a `page` result), so existing callers/tests are unaffected.
  function _scoreHit(term, title, bodyText, commentText) {
    const inTitle = title.toLowerCase().includes(term);
    const inBody = bodyText.toLowerCase().includes(term);
    const inComments = commentText.toLowerCase().includes(term);
    if (!inTitle && !inBody && !inComments) return null;
    const loc = [];
    let score = 0;
    if (inTitle) { score += 5; loc.push("title"); }
    if (inBody) { score += 2; loc.push("body"); }
    if (inComments) { score += 1; loc.push("comments"); }
    return { loc, score };
  }
  function searchReaderModel(model, query) {
    const term = String(query || "").trim().toLowerCase();
    if (term.length < 2 || !model) return [];
    const results = [];
    (model.wikis || []).forEach((wiki) => {
      Object.keys(wiki.pages || {}).forEach((pageId) => {
        const page = wiki.pages[pageId];
        const title = page.title || page.label || "";
        const bodyText = stripTags(page.content_html);
        const commentText = (page.comments || []).map((c) => stripTags(c.content_html)).join(" ");
        const hit = _scoreHit(term, title, bodyText, commentText);
        if (!hit) return;
        results.push({ kind: "page", wikiId: wiki.id, pageId, title, wikiTitle: wiki.title, loc: hit.loc, score: hit.score, bodyText, commentText });
      });
    });
    (model.blogs || []).forEach((blog) => {
      Object.keys(blog.posts || {}).forEach((postId) => {
        const post = blog.posts[postId];
        const title = post.title || "";
        const bodyText = stripTags(post.content_html);
        const commentText = (post.comments || []).map((c) => stripTags(c.content_html)).join(" ");
        const hit = _scoreHit(term, title, bodyText, commentText);
        if (!hit) return;
        results.push({ kind: "post", blogId: blog.id, postId, title, wikiTitle: blog.title, loc: hit.loc, score: hit.score, bodyText, commentText });
      });
    });
    (model.forums || []).forEach((forum) => {
      Object.keys(forum.topics || {}).forEach((topicId) => {
        const topic = forum.topics[topicId];
        const title = topic.title || "";
        const bodyText = stripTags(topic.content_html);
        const replyText = Object.values(topic.replies || {}).map((r) => stripTags(r.content_html)).join(" ");
        const hit = _scoreHit(term, title, bodyText, replyText);
        if (!hit) return;
        results.push({ kind: "topic", forumId: forum.id, topicId, title, wikiTitle: forum.title, loc: hit.loc, score: hit.score, bodyText, commentText: replyText });
      });
    });
    results.sort((a, b) => b.score - a.score);
    return results;
  }
  // ================= READER-PURE-END =================

  // Auto-size each sandboxed body-frame from its own content, measured by the
  // PARENT — there is no in-frame script (the frame has no `allow-scripts`). The
  // srcdoc frame is same-origin (`allow-same-origin`), so the parent can read its
  // scrollHeight on load and keep it in sync via a ResizeObserver for late layout
  // (e.g. images finishing). `load` doesn't bubble, so we listen in the capture
  // phase. (Outside the READER-PURE markers: a load-time DOM side effect, not
  // pure logic, so the DOM-free Node unit tests never trip over `window`.)
  document.addEventListener("load", (e) => {
    const f = e.target;
    if (!(f instanceof HTMLIFrameElement) || !f.classList.contains("body-frame")) return;
    const size = () => {
      try {
        const d = f.contentDocument;
        if (d) f.style.height = (d.documentElement.scrollHeight + 24) + "px";
      } catch (_) {}
    };
    size();
    try {
      const d = f.contentDocument;
      if (d && d.body && window.ResizeObserver) new ResizeObserver(size).observe(d.body);
    } catch (_) {}
    // Reader only: make the body's own in-export links clickable. The frame has
    // no allow-scripts, but it IS same-origin, so the parent wires the click
    // and navigates the reader (an in-frame `<a href="#p-..">` would otherwise
    // do nothing). Covers <a> in prose AND inside inline SVG.
    if (f.closest && f.closest("#r-article")) {
      try {
        const d = f.contentDocument;
        if (d) {
          d.querySelectorAll("a").forEach((a) => {
            const href = a.getAttribute("href") || a.getAttribute("xlink:href");
            const target = href && readerNavLinks.get(href);
            if (!target) return;
            a.style.cursor = "pointer";
            a.addEventListener("click", (ev) => {
              ev.preventDefault();
              navigateToPageId(target);
            });
          });
        }
      } catch (_) {}
    }
  }, true);

  // ---------- motion (docs/design/gui-motion.md) ----------
  // Nothing below is 1:1 with an SSE event. The stream delivers tens of
  // events a second; emphasis that fires per event is a strobe, not a
  // signal, so every helper here coalesces before it touches the DOM.
  const EMPHASIS_MS = 250;    // colour decay, per target
  const BEAM_MS = 1000;       // coalesced pipeline pulse
  const FLOW_IDLE_MS = 1500;  // "events are still arriving" window
  const lastEmphasis = new WeakMap();
  let lastBeamPulse = 0, flowTimer = null;

  // Colour lift that decays. Never touches a digit's opacity or position:
  // the number itself must stay readable at any event rate.
  function emphasize(el, cls) {
    if (!el) return;
    const now = performance.now();
    if (now - (lastEmphasis.get(el) || 0) < EMPHASIS_MS) return;
    lastEmphasis.set(el, now);
    el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
  }
  // Counters update instantly and truthfully; only the emphasis is throttled.
  function setNum(el, v) { if (!el) return; el.textContent = v; emphasize(el, "bump"); }

  function pulseBeam() {
    if (reduce) return;
    const b = $("beam"); if (!b) return;
    const now = performance.now();
    if (now - lastBeamPulse < BEAM_MS) return;
    lastBeamPulse = now;
    b.classList.remove("pulse"); void b.offsetWidth; b.classList.add("pulse");
  }
  // Ambient "the pipeline is streaming" state, independent of event rate.
  function setBeamRunning(on) {
    const b = $("beam"); if (b) b.classList.toggle("breathe", !!on && !reduce);
  }

  // Gates the progress sheen on real event flow. A bar stalled on one large
  // asset must not look like a hung run -- and must not pretend to move when
  // nothing is arriving either, which is why this is driven by events rather
  // than left running.
  function markFlow() {
    const el = document.querySelector(".overall"); if (!el) return;
    el.dataset.flow = "on";
    clearTimeout(flowTimer);
    flowTimer = setTimeout(() => { el.dataset.flow = "off"; }, FLOW_IDLE_MS);
  }
  function stopFlow() {
    clearTimeout(flowTimer); flowTimer = null;
    const el = document.querySelector(".overall"); if (el) el.dataset.flow = "off";
  }

  // One-shot ring when a node reaches a terminal state -- the eye catches a
  // completion in peripheral vision without the row ever moving.
  function settleNode(node, cls) {
    if (!node || reduce) return;
    const dot = node.querySelector(".sdot"); if (!dot) return;
    dot.classList.remove("settle-ok", "settle-bad"); void dot.offsetWidth; dot.classList.add(cls);
  }

  // The only count-up in the app, and only because the verdict is terminal:
  // a live counter that eases toward its value is lying about the value.
  function countUp(el, to) {
    if (!el) return;
    const target = Number(to) || 0;
    if (reduce || target <= 0) { el.textContent = target; return; }
    const start = performance.now();
    (function step(now) {
      const p = Math.min(1, (now - start) / 600);
      el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    })(start);
  }

  function addTreeNode(it) {
    const row = document.createElement("div");
    const nodeKey = it.key || it.id;
    row.className = it.kind === "communitygroup" ? "node reveal community-row" : "node reveal";
    row.dataset.st = "pending"; row.dataset.id = nodeKey;
    row.setAttribute("role", "treeitem"); row.tabIndex = 0;
    const indent = it.kind === "communitygroup" ? it.depth : it.depth + liveDepthOffset;
    row.style.paddingLeft = (8 + indent * 18) + "px";
    const kind = it.kindLabel || (it.kind === "feed" ? "feed" : (it.depth <= 1 ? "page" : "child"));
    row.innerHTML =
      '<span class="rail"><span class="sdot"></span></span>' +
      '<span class="label"><span class="tt">' + escapeHtml(it.title) + '</span><span class="kind">' + kind + '</span></span>' +
      '<span class="cmt">' + (it.comments ? ('<b>'+it.comments+'</b> cmt') : (it.kind==="feed" ? 'index' : '—')) + '<span class="open-i" aria-hidden="true">⤢</span></span>';
    row.addEventListener("click", () => openDrawer(nodeKey));
    row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDrawer(nodeKey); } });
    // Under its own community's heading, not at the end of the tree.
    const tail = it.community ? liveCommunityTail.get(it.community) : null;
    $("tree").insertBefore(row, tail ? tail.nextSibling : null);
    if (it.community) liveCommunityTail.set(it.community, row);
    nodeEls.set(nodeKey, row);
    if (!keyByRawId.has(it.id)) keyByRawId.set(it.id, nodeKey);
    $("tree-count") && ($("tree-count").textContent = nodeEls.size);
    return row;
  }

  // Rows arrive in batches rather than one DOM insert per event: prepending
  // an animated row 30 times a second makes the whole column swim and
  // nothing in it can be read. Buffer, flush at most every LOG_FLUSH_MS, and
  // animate only the newest batch -- existing rows never move.
  //
  // Above ~20 rows/s successful fetches of the same kind collapse into one
  // row that counts up in place ("37 assets fetched" as one legible fact).
  // Warnings and failures never collapse: a failure always earns its own
  // line. (A sustained retry storm therefore still stacks rows -- see the
  // open point in docs/design/gui-motion.md B2.)
  // Collapse is gated on the *rate*, not on how many rows a single flush
  // happened to catch. A run delivers rows in a metronome-steady rhythm --
  // around 2 rows per 150ms window -- so a per-flush threshold never fires
  // and the collapse would be dead code. 8 rows/s is roughly where a log
  // stops being readable line by line, so that is where the aggregation
  // starts.
  const LOG_FLUSH_MS = 150, LOG_COLLAPSE_RATE = 8, LOG_REANCHOR_MS = 2500, LOG_MAX = 40;
  let logBuffer = [], logFlushTimer = null, logArrivals = [];
  const logAgg = new Map();  // collapse key -> { el, n, since, lastBump }

  function logRow(tag, tagCls, url, extra, key) {
    // Stamped on arrival, not on flush, so the batching never backdates a row.
    logBuffer.push({ tag, tagCls, url, extra, key, t: nowStamp() });
    logArrivals.push(performance.now());
    if (!logFlushTimer) logFlushTimer = setTimeout(flushLog, LOG_FLUSH_MS);
  }
  // An aggregate row names the *group*, not one of its members: showing one
  // URL next to "× 7" would claim that URL was fetched seven times. The
  // stamp stays the one the group started at, so the column remains ordered
  // newest-first -- re-anchoring, not restamping, is what keeps it honest.
  function logMarkup(r, n) {
    const label = n > 1 ? '<b>' + escapeHtml(r.key || "fetch") + '</b>' : r.url;
    const right = n > 1 ? '<span class="xn">× ' + n + '</span>' : (r.extra || "");
    return '<span class="t">'+r.t+'</span><span class="tag '+r.tagCls+'">'+r.tag+'</span>' +
      '<span class="u">'+label+'</span><span class="z">'+right+'</span>';
  }
  function resetLog() {
    if (logFlushTimer) { clearTimeout(logFlushTimer); logFlushTimer = null; }
    logBuffer = []; logArrivals = []; logAgg.clear();
  }
  function flushLog() {
    logFlushTimer = null;
    const batch = logBuffer; logBuffer = [];
    const log = $("log");
    if (!batch.length || !log) return;
    // Collapse only while rows genuinely outrun reading; at a few a second
    // every event keeps its own line, which is the readable thing to do.
    const now = performance.now();
    logArrivals = logArrivals.filter((t) => t > now - 1000);
    const collapsing = logArrivals.length > LOG_COLLAPSE_RATE;
    if (!collapsing) logAgg.clear();
    batch.forEach((r) => {
      const key = collapsing && r.tagCls === "ok" && r.key ? r.key : null;
      const agg = key ? logAgg.get(key) : null;
      // Re-anchored periodically so a long-lived aggregate row's timestamp
      // never claims to be older than what it is counting.
      if (agg && agg.el.isConnected && now - agg.since < LOG_REANCHOR_MS) {
        agg.n += 1;
        agg.el.innerHTML = logMarkup({ t: agg.t, tag: r.tag, tagCls: r.tagCls, url: r.url, extra: r.extra, key: r.key }, agg.n);
        if (now - agg.lastBump >= EMPHASIS_MS) {
          agg.lastBump = now;
          const chip = agg.el.querySelector(".xn");
          if (chip) chip.classList.add("bump");
        }
        return;
      }
      const row = document.createElement("div");
      row.className = "row fresh";
      row.innerHTML = logMarkup(r, 1);
      log.insertBefore(row, log.firstChild);
      if (key) logAgg.set(key, { el: row, n: 1, since: now, lastBump: now, t: r.t });
    });
    while (log.children.length > LOG_MAX) log.removeChild(log.lastChild);
  }

  function pushSpark(v) {
    spark.push(v); if (spark.length > 48) spark.shift(); peak = Math.max(peak, v);
    const W=320,H=92,max=Math.max(peak,1),n=spark.length;
    const x=(i)=> n<=1 ? W : (i/(n-1))*W, y=(val)=> H-6-(val/max)*(H-16);
    let d=""; spark.forEach((val,i)=>{ d += (i?"L":"M")+x(i).toFixed(1)+" "+y(val).toFixed(1)+" "; });
    $("spark-line").setAttribute("d", d.trim());
    $("spark-area").setAttribute("d", (d+"L"+W+" "+H+" L0 "+H+" Z").trim());
    const ex=x(n-1), ey=y(spark[n-1]);
    $("spark-end").setAttribute("cx",ex); $("spark-end").setAttribute("cy",ey);
    $("spark-halo").setAttribute("cx",ex); $("spark-halo").setAttribute("cy",ey);
    $("spark-peak").textContent = peak;
  }

  function raiseAlert(kind, h, dHtml, forId) {
    // A note is not a warning: it never reaches `warnings`, so it cannot
    // inflate the verdict's count, flip a run to "needs attention", or badge
    // a tree node with a severity it does not have.
    if (kind === "note") notes.push({ kind, h, forId });
    else warnings.push({ kind, h, forId });
    const box = $("alerts"); const empty = $("alerts-empty"); if (empty) empty.remove();
    const SEV_TEXT = { note: "note", warn: "warning", crit: "critical" };
    const el = document.createElement("div"); el.className = "alert " + kind;
    el.innerHTML = '<div class="ico">'+(kind === "crit" ? "⚠" : kind === "warn" ? "!" : "i")+'</div>' +
      '<div class="body"><div class="h">'+h+'</div><div class="d">'+dHtml+'</div></div>' +
      '<div class="sev">'+(SEV_TEXT[kind] || kind)+'</div>';
    box.appendChild(el);
  }

  function tickClock() {
    const el = Math.floor((Date.now()-startTs)/1000);
    $("elapsed").textContent = fmtClock(el);
    const done = ok + reused + fail + stopped, total = items.length;
    if (done > 2 && done < total) { const rate = done/Math.max(el,1); $("eta").textContent = fmtClock(Math.ceil((total-done)/Math.max(rate,0.01))); }
  }

  // ---------- inspector drawer ----------
  let drawerOpenTimer = null;
  function warningFor(id) { return warnings.find(w => w.forId === id); }

  // Every tree node is now live (the offline synthetic-record simulator
  // is gone) -- the drawer always renders from the real derived model.
  function renderDrawer(it) {
    renderLiveDrawer(it);
  }

  const m = (k,v) => '<div class="m"><div class="k">'+k+'</div><div class="v">'+v+'</div></div>';
  const section = (title, inner) => '<div class="dsection"><div class="sh">'+title+'</div>'+inner+'</div>';
  const pr = (k,v) => '<div class="pr"><span class="pk">'+k+'</span><span class="pv">'+escapeHtml(v)+'</span></div>';

  function openDrawer(id) {
    openId = id; const it = byId.get(id); if (!it) return;
    nodeEls.forEach((el, k) => el.classList.toggle("sel", k === id));
    renderDrawer(it);
    $("scrim").classList.add("show");
    const dr = $("drawer");
    dr.classList.add("show");
    if (!reduce) {
      dr.classList.remove("opening"); void dr.offsetWidth; dr.classList.add("opening");
      clearTimeout(drawerOpenTimer);
      drawerOpenTimer = setTimeout(() => dr.classList.remove("opening"), 500);
    }
    dr.focus();
  }
  function closeDrawer() {
    openId = null; stopDrawerContent(); $("scrim").classList.remove("show");
    clearTimeout(drawerOpenTimer);
    $("drawer").classList.remove("show", "opening");
    nodeEls.forEach(el => el.classList.remove("sel"));
  }
  function openReaderAt(id) {
    // A live page/post/topic node's id IS its model id (the id the crawler
    // itself emits), so it deep-links exactly (`findRealEntityByLiveNode`,
    // #20/#21). A group header (wikigroup/group) or feed row has no reader
    // entry point of its own.
    const it = byId.get(id);
    // The reader has no per-file page, so a file's "Open reader" lands on the
    // library that holds it. `openReaderFilesReal` clears the reader selection
    // itself, so the default view `enterReader` opens first is replaced, not
    // layered on.
    if (it && it.kind === "file") {
      const match = REAL_MODEL ? findRealEntityByLiveNode(REAL_MODEL, it) : null;
      if (!match) return;
      closeDrawer();
      enterReader();
      openReaderFilesReal(match.libraryId);
      return;
    }
    if (!it || !(it.kind === "page" || it.kind === "post" || it.kind === "topic" || it.kind === "rich_content")) return;
    closeDrawer();
    showSection("reader");
    enterReaderReal(it, { reveal: true });
  }
  $("d-reader").addEventListener("click", () => { if (openId) openReaderAt(openId); });
  $("d-close").addEventListener("click", closeDrawer);
  $("scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && openId) closeDrawer(); });

  // Highlights `term` in `text` (HTML-escaped first) -- shared by the real
  // reader's page/post/topic views and its search results.
  function hl(text, term) {
    const e = escapeHtml(text);
    const t = (term || "").trim();
    if (t.length < 2) return e;
    try {
      const re = new RegExp("(" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi");
      return e.replace(re, "<mark>$1</mark>");
    } catch (_) { return e; }
  }
  function clearSearchInput() { const si = $("r-search-input"); if (si) si.value = ""; }

  // A short, highlighted excerpt of `text` around the first match of
  // `term` -- shared by the real reader's search results list.
  function snippet(text, term) {
    const i = text.toLowerCase().indexOf(term.toLowerCase());
    if (i < 0) return hl(text.slice(0, 130), term) + (text.length > 130 ? "…" : "");
    const start = Math.max(0, i - 55), end = Math.min(text.length, i + term.length + 75);
    return (start > 0 ? "…" : "") + hl(text.slice(start, end), term) + (end < text.length ? "…" : "");
  }

  // ---------- reader on real data:
  // fetches /api/model and renders it through the pure functions above
  // ----------
  let REAL_MODEL = null;
  let realWikiId = null, realPageId = null;
  const readerComponentOpen = new Set();
  // Per-container (one wiki / one blog / one forum) open state, keyed by
  // `kind:id`. The nav is rebuilt on every open, so without this each
  // rebuild would silently re-close whatever the reader had expanded.
  const readerContainerOpen = new Set();
  let openArchiveName = null;
  // The reader browses three apps. The wiki
  // selection is (realWikiId, realPageId); a blog post is (realBlogId,
  // realPostId); a forum topic is (realForumId, realTopicId). Exactly one
  // pair is set at a time -- `clearReaderSelection` resets all before an
  // open function sets its own, and `reopenCurrentReal` re-opens whichever
  // is current (used by the live refresh + the search-cleared path).
  let realBlogId = null, realPostId = null, realForumId = null, realTopicId = null;
  // The open file library. A library is a single view (one listing), so
  // unlike the others it needs no second id for an item within it.
  let realLibraryId = null;
  // The open Highlights page: a container plus one page, like a wiki.
  let realRichContentId = null, realRichPageId = null;
  let realPollTimer = null;
  let realModelState = null;   // "partial" while importing, "complete" when done
  let liveRefreshTimer = null;

  function stopRealPolling() { if (realPollTimer) { clearTimeout(realPollTimer); realPollTimer = null; } }
  function stopLiveRefresh() { if (liveRefreshTimer) { clearTimeout(liveRefreshTimer); liveRefreshTimer = null; } }

  // While a run is still importing (`X-HCL-Model-State: partial`), keep
  // refreshing /api/model so newly-imported pages appear in the reader's
  // nav without disturbing the page currently being read.
  // Stops as soon as a fetch reports `complete`.
  function scheduleLiveRefresh() {
    stopLiveRefresh();
    if (realModelState !== "partial") return;
    liveRefreshTimer = setTimeout(liveRefresh, 1200);
  }
  function liveRefresh() {
    if ($("panel-reader").hidden) { scheduleLiveRefresh(); return; }  // not looking; check back
    fetch("/api/model").then((res) => {
      if (!res.ok) { scheduleLiveRefresh(); return; }
      realModelState = res.headers.get("X-HCL-Model-State") || "complete";
      return res.json().then((model) => {
        REAL_MODEL = model;
        const searching = $("r-search-input") && $("r-search-input").value.trim().length >= 2;
        if (!searching) {
          // Refresh the nav so new pages/posts/topics show up; only
          // re-render the open article when the run just completed
          // (skeletons may now be full).
          buildReaderNavReal();
          if (realModelState === "complete") {
            reopenCurrentReal();
          }
        }
        scheduleLiveRefresh();
      });
    }).catch(() => scheduleLiveRefresh());
  }

  // Link a live tree node to its page in the derived model. The live
  // `page_id` IS the model's page id (the nav-node id the crawler now
  // emits), so this is an exact O(1) id match -- never a title guess that
  // could collide. A wiki-label + title match remains only as a defensive
  // fallback for an older/foreign event that carried some other id.
  function findRealPageByLiveNode(model, node) {
    if (!node) return null;
    const wikis = (model && model.wikis) || [];
    for (const wiki of wikis) {
      if (wiki.pages && Object.prototype.hasOwnProperty.call(wiki.pages, node.id)) {
        return { wikiId: wiki.id, pageId: node.id };
      }
    }
    for (const wiki of wikis) {
      if (node.wiki && wiki.label && wiki.label !== node.wiki) continue;
      for (const [pid, page] of Object.entries(wiki.pages || {})) {
        if ((page.title || page.label || pid) === node.title) return { wikiId: wiki.id, pageId: pid };
      }
    }
    return null;
  }

  // Same idea as `findRealPageByLiveNode`, generalised across all three
  // apps (#20/#21 -- live ingest content parity for blog posts and forum
  // topics, not just wiki pages). A live node's id IS its model id
  // (page_id/post_id/topic_id), so each branch is an exact O(1) match.
  // Returns `{ app: "wiki", wikiId, pageId }`, `{ app: "blog", blogId,
  // postId }`, `{ app: "forum", forumId, topicId }`, or null when the
  // node isn't (yet) in the derived snapshot -- needed both to render a
  // node's live content inline and to route "Open reader" at the right
  // entity.
  function findRealEntityByLiveNode(model, node) {
    if (!node) return null;
    if (node.kind === "post") {
      for (const blog of (model && model.blogs) || []) {
        if (blog.posts && Object.prototype.hasOwnProperty.call(blog.posts, node.id)) {
          return { app: "blog", blogId: blog.id, postId: node.id };
        }
      }
      return null;
    }
    if (node.kind === "topic") {
      for (const forum of (model && model.forums) || []) {
        if (forum.topics && Object.prototype.hasOwnProperty.call(forum.topics, node.id)) {
          return { app: "forum", forumId: forum.id, topicId: node.id };
        }
      }
      return null;
    }
    if (node.kind === "rich_content") {
      for (const container of (model && model.rich_content) || []) {
        if (container.pages && Object.prototype.hasOwnProperty.call(container.pages, node.id)) {
          return { app: "rich_content", containerId: container.id, pageId: node.id };
        }
      }
      return null;
    }
    if (node.kind === "file") {
      // Searched rather than read off the node: the live event names the
      // library, but the derived model is the authority on which library
      // actually holds the file.
      for (const library of (model && model.file_libraries) || []) {
        if (library.files && Object.prototype.hasOwnProperty.call(library.files, node.id)) {
          return { app: "files", libraryId: library.id, fileId: node.id };
        }
      }
      return null;
    }
    const page = findRealPageByLiveNode(model, node);
    return page ? { app: "wiki", wikiId: page.wikiId, pageId: page.pageId } : null;
  }

  // Reader empty-state: a `text`/`spin` message as before, plus an
  // optional "Open an archive…" CTA for when there's genuinely nothing to
  // wait for -- no run in progress, nothing imported. It routes back to the
  // Overview's recent-archives list (#ov-recent is the single source for
  // "open a past export"; the reader never keeps its own copy of that list).
  function renderReaderMessage(text, spin, offerOpenArchive) {
    $("r-nav").innerHTML = ""; $("r-crumb").innerHTML = "<b>Reconstructed archive</b>";
    const cta = offerOpenArchive
      ? '<div style="margin-top:14px;"><button class="btn" id="r-empty-open-archive" type="button">Open an archive…</button></div>'
      : "";
    $("r-article").innerHTML = '<div class="r-empty">' + (spin ? '<span class="spin" style="display:inline-block;vertical-align:-1px;margin-right:8px;"></span>' : "") + escapeHtml(text) + cta + '</div>';
    if (offerOpenArchive) {
      $("r-empty-open-archive")?.addEventListener("click", () => showSection("overview"));
    }
  }

  function fetchReaderModel(onReady) {
    fetch("/api/model").then((res) => {
      if (res.status === 503) {
        // 503 = no derivable model yet. Only keep polling while a run is
        // actually importing (waiting for the first page). When idle (no run,
        // nothing opened), show the empty-state ONCE and stop -- otherwise the
        // reader polls /api/model forever and spams 503s (finding #29).
        if (liveRunning) {
          renderReaderMessage("Waiting for the first imported page…", true, true);
          realPollTimer = setTimeout(() => fetchReaderModel(onReady), 1200);
        } else {
          renderReaderMessage(
            "Nothing loaded yet — run an ingest, or open an existing archive.",
            false,
            true
          );
        }
        return;
      }
      if (!res.ok) { renderReaderMessage("The reconstructed archive isn't available (server returned " + res.status + ")."); return; }
      realModelState = res.headers.get("X-HCL-Model-State") || "complete";
      return res.json().then((model) => {
        REAL_MODEL = model;
        onReady(model);
        // Keep the reader current while the import is still running.
        scheduleLiveRefresh();
      });
    }).catch(() => {
      renderReaderMessage("The reconstructed archive isn't reachable right now — retrying…", true);
      realPollTimer = setTimeout(() => fetchReaderModel(onReady), 1200);
    });
  }

  function firstRealPage(model) {
    for (const wiki of (model && model.wikis) || []) {
      const ordered = preorderWikiPages(wiki);
      if (ordered.length) return { wikiId: wiki.id, pageId: ordered[0].id };
    }
    return null;
  }

  function clearReaderSelection() {
    realWikiId = realPageId = realBlogId = realPostId = realForumId = realTopicId = null;
    realLibraryId = null;
    realRichContentId = realRichPageId = null;
  }

  // Re-open whichever entity is the current reader selection -- used when
  // the live refresh finishes (skeletons may now be full) and when a
  // search box is cleared.
  function reopenCurrentReal() {
    if (realLibraryId) return openReaderFilesReal(realLibraryId);
    if (realRichContentId && realRichPageId)
      return openReaderRichContentReal(realRichContentId, realRichPageId);
    if (realWikiId && realPageId) openReaderPageReal(realWikiId, realPageId);
    else if (realBlogId && realPostId) openReaderPostReal(realBlogId, realPostId);
    else if (realForumId && realTopicId) openReaderTopicReal(realForumId, realTopicId);
  }

  // Small helper: a nav link with the shared behaviour (clear the search
  // box, run `go`, keyboard-activatable). `cur` marks the open entity.
  function _navLink(label, depth, cur, go, key) {
    const aEl = document.createElement("a");
    aEl.className = "depth" + Math.min(depth, 2) + (cur ? " cur" : "");
    aEl.dataset.readerKey = key || "";
    aEl.textContent = label; aEl.tabIndex = 0;
    const run = (event) => { event.stopPropagation(); clearSearchInput(); go(); };
    aEl.addEventListener("click", run);
    aEl.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); run(); } });
    return aEl;
  }

  function updateReaderNavSelection(key) {
    document.querySelectorAll("#r-nav a[data-reader-key]").forEach((link) => {
      link.classList.toggle("cur", link.dataset.readerKey === key);
    });
  }

  // The reader nav: wiki groups first (unchanged), then a group per blog
  // (its posts) and per forum (its topics), each with a group header --
  // Highlighting reads the module-level
  // selection, so every caller just rebuilds and the open entity stays lit.
  // One collapsible group per individual wiki / blog / forum, nested inside
  // its section -- plain `.wk` headers instead would mean expanding FORUMS
  // dumps every topic of every forum into the nav at once.
  // ---- Date orientation in a long entry list ----
  //
  // Two different jobs, and they were conflated. Knowing WHEN you are while
  // scrolling is continuous and passive: month markers ride along with the
  // list and stick under the container header, so the answer is always on
  // screen without asking for it. Narrowing to a window is occasional and
  // deliberate, so it hides behind a filter button rather than taking a
  // block of the nav from every blog and forum permanently.

  const DATE_FILTER_MIN_ENTRIES = 3;

  function _monthLabel(ms) {
    return new Date(ms).toLocaleDateString(undefined, { year: "numeric", month: "long" });
  }

  // Markers between entries wherever the month changes. Sticky, so the month
  // you are currently inside stays visible at the top of the list while you
  // scroll through it -- that is the "where am I in time" answer.
  function addReaderDateMarkers(container, entries) {
    let last = null;
    entries.forEach((entry) => {
      const t = Date.parse(entry.published || "");
      if (isNaN(t)) return;
      const label = _monthLabel(t);
      if (label === last) return;
      last = label;
      const marker = document.createElement("div");
      marker.className = "r-datemark";
      marker.textContent = label;
      marker.dataset.month = label;
      container.insertBefore(marker, entry.el);
    });
  }

  // The range narrowing, now opt-in. Either handle may be dragged past the
  // other; the pair is read as a range rather than the drag being refused.
  function addReaderDateFilter(container, entries, headerEl) {
    const dated = entries
      .map((e) => ({ el: e.el, t: Date.parse(e.published || "") }))
      .filter((e) => !isNaN(e.t));
    if (dated.length < DATE_FILTER_MIN_ENTRIES) return;
    const lo = Math.min(...dated.map((e) => e.t));
    const hi = Math.max(...dated.map((e) => e.t));
    const DAY = 86400000;
    if (hi - lo < DAY) return;

    const wrap = document.createElement("div");
    wrap.className = "r-daterange";
    wrap.hidden = true;
    const fmt = (ms) => new Date(ms).toLocaleDateString(undefined,
      { year: "numeric", month: "short", day: "numeric" });
    wrap.innerHTML =
      '<div class="rd-sliders">' +
      '<input type="range" class="rd-from" min="' + lo + '" max="' + hi + '" value="' + lo + '" aria-label="Earliest date to show">' +
      '<input type="range" class="rd-to" min="' + lo + '" max="' + hi + '" value="' + hi + '" aria-label="Latest date to show">' +
      "</div>" +
      '<div class="rd-readout"><span class="rd-span"></span>' +
      '<button type="button" class="rd-reset" hidden>Reset</button></div>';

    const from = wrap.querySelector(".rd-from");
    const to = wrap.querySelector(".rd-to");
    const span = wrap.querySelector(".rd-span");
    const reset = wrap.querySelector(".rd-reset");

    const apply = () => {
      const a = Math.min(+from.value, +to.value);
      const b = Math.max(+from.value, +to.value);
      let shown = 0;
      dated.forEach((e) => {
        const hit = e.t >= a && e.t <= b + DAY - 1;
        e.el.hidden = !hit;
        if (hit) shown += 1;
      });
      span.textContent = fmt(a) + " – " + fmt(b) + " · " + shown + " of " + dated.length;
      const narrowed = a > lo || b < hi;
      reset.hidden = !narrowed;
      // A month marker whose entries are all filtered out would otherwise sit
      // there labelling nothing.
      container.querySelectorAll(".r-datemark").forEach((mark) => {
        let el = mark.nextElementSibling, any = false;
        while (el && !el.classList.contains("r-datemark")) {
          if (el.tagName === "A" && !el.hidden) { any = true; break; }
          el = el.nextElementSibling;
        }
        mark.hidden = !any;
      });
      if (headerEl) headerEl.classList.toggle("is-filtered", narrowed);
    };
    from.addEventListener("input", apply);
    to.addEventListener("input", apply);
    reset.addEventListener("click", () => { from.value = lo; to.value = hi; apply(); });
    container.insertBefore(wrap, container.firstChild);

    // The toggle lives on the container header, beside its chevron.
    if (headerEl) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "rd-toggle";
      btn.title = "Filter this list by date";
      btn.setAttribute("aria-label", "Filter by date");
      btn.setAttribute("aria-expanded", "false");
      btn.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-filter"/></svg>';
      btn.addEventListener("click", (event) => {
        event.stopPropagation();  // never collapse the container
        wrap.hidden = !wrap.hidden;
        btn.setAttribute("aria-expanded", wrap.hidden ? "false" : "true");
      });
      headerEl.appendChild(btn);
    }
    apply();
  }

  function addReaderContainer(parent, key, label, selected, sole) {
    if (selected) readerContainerOpen.add(key);
    // The only container in its category opens with the category: two rows
    // that always had the same answer are one decision, not two. Its header
    // stays -- it carries the wiki's or forum's actual name -- and can still
    // be collapsed on its own.
    if (sole && !parent.hidden) readerContainerOpen.add(key);
    const open = readerContainerOpen.has(key);
    const head = document.createElement("button");
    head.className = "r-container-section";
    head.type = "button";
    head.setAttribute("aria-expanded", open ? "true" : "false");
    head.innerHTML = '<span class="r-container-chevron" aria-hidden="true">' +
      (open ? "\u25be" : "\u25b8") + '</span><span>' + escapeHtml(label) + '</span>';
    const content = document.createElement("div");
    content.className = "r-container-content";
    content.hidden = !open;
    head.addEventListener("click", (event) => {
      event.stopPropagation();
      const wasOpen = head.getAttribute("aria-expanded") === "true";
      head.setAttribute("aria-expanded", wasOpen ? "false" : "true");
      content.hidden = wasOpen;
      head.querySelector(".r-container-chevron").textContent = wasOpen ? "\u25b8" : "\u25be";
      if (wasOpen) readerContainerOpen.delete(key);
      else { readerContainerOpen.add(key); revealExpanded(head, content); }
    });
    if (sole && parent.expandHooks) {
      parent.expandHooks.push(() => {
        if (head.getAttribute("aria-expanded") === "true") return;
        head.setAttribute("aria-expanded", "true");
        content.hidden = false;
        head.querySelector(".r-container-chevron").textContent = "\u25be";
        readerContainerOpen.add(key);
      });
    }
    parent.appendChild(head);
    parent.appendChild(content);
    content.headerEl = head;
    return content;
  }

  // Blog posts and forum topics are READ newest first: an archive is opened
  // to see what a community was last saying, and the feed order cannot be
  // relied on either way -- the demo's fake server feeds oldest first, a real
  // deployment's Atom feed usually feeds newest first. The derived model keeps
  // whichever order the source gave, because that is provenance; this is the
  // view deciding how to present it.
  let readerNewestFirst = true;
  try {
    readerNewestFirst = (localStorage.getItem("hcl-export-reader-order") || "newest") !== "oldest";
  } catch (_) {}

  function setReaderOrder(newestFirst) {
    readerNewestFirst = !!newestFirst;
    try {
      localStorage.setItem("hcl-export-reader-order", readerNewestFirst ? "newest" : "oldest");
    } catch (_) {}
    buildReaderNavReal();
  }

  // Undated entries keep their feed order and follow the dated ones, rather
  // than being sorted to an arbitrary end by a date nobody recorded.
  function readerOrdered(ids, lookup) {
    const entries = (ids || []).map((id, position) => {
      const item = lookup(id) || {};
      return { id, position, t: Date.parse(item.created || item.modified || "") };
    });
    const dated = entries.filter((e) => !isNaN(e.t));
    const undated = entries.filter((e) => isNaN(e.t));
    dated.sort((a, b) => (readerNewestFirst ? b.t - a.t : a.t - b.t) || a.position - b.position);
    return dated.concat(undated).map((e) => e.id);
  }

  function readerOrderToggle() {
    const wrap = document.createElement("div");
    wrap.className = "r-order";
    // Named, sentence case, and carrying a swap glyph. An uppercase grey
    // pill reading "NEWEST FIRST" would borrow the visual language of the
    // WIKIS/BLOGS section captions, which are not buttons --
    // so it would read as a statement of fact rather than an offer to change it,
    // and went unfound.
    const label = document.createElement("span");
    label.className = "r-order-label";
    label.textContent = "Order";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "r-order-btn";
    button.id = "r-order-toggle";
    button.innerHTML = '<span class="r-order-value">' +
      (readerNewestFirst ? "Newest first" : "Oldest first") +
      '</span><span class="r-order-swap" aria-hidden="true">\u21c5</span>';
    button.title = "Order blog posts and forum topics by date — click to flip";
    button.setAttribute("aria-label",
      "Reading order: " + (readerNewestFirst ? "newest first" : "oldest first") + ". Click to flip.");
    button.addEventListener("click", () => setReaderOrder(!readerNewestFirst));
    wrap.appendChild(label);
    wrap.appendChild(button);
    return wrap;
  }

  // Which communities this archive holds, in a stable order, including a
  // final "no community" bucket for containers that belong to none -- a
  // standalone wiki is a real thing and hiding it would be worse than an
  // extra heading.
  function communitiesIn(model) {
    if (!model) return [];
    const seen = new Map();
    let loose = false;
    const note = (container) => {
      if (!container) return;
      const uuid = container.community_uuid || null;
      if (!uuid) { loose = true; return; }
      if (!seen.has(uuid)) seen.set(uuid, container.community_title || uuid);
    };
    (model.wikis || []).forEach(note);
    (model.blogs || []).forEach(note);
    (model.forums || []).forEach(note);
    (model.rich_content || []).forEach(note);
    (model.file_libraries || []).forEach(note);
    const communities = Array.from(seen, ([uuid, title]) => ({ uuid, title }));
    if (loose && communities.length) communities.push({ uuid: null, title: "Not in a community" });
    return communities;
  }

  // The model, narrowed to one community's containers. Files libraries are
  // addressed BY community uuid, which is the same field here.
  function scopeForCommunity(model, uuid) {
    const mine = (container) => (container.community_uuid || null) === uuid;
    return {
      wikis: (model.wikis || []).filter(mine),
      blogs: (model.blogs || []).filter(mine),
      forums: (model.forums || []).filter(mine),
      rich_content: (model.rich_content || []).filter(mine),
      file_libraries: (model.file_libraries || []).filter(mine),
    };
  }


  function buildReaderNavReal() {
    const nav = $("r-nav"); nav.innerHTML = "";
    nav.appendChild(readerOrderToggle());
    const addSection = (label, selected, keyPrefix) => {
      // Two communities both have a "WIKIS" section; without the prefix they
      // would open and close together.
      const openKey = (keyPrefix || "") + label;
      const section = document.createElement("button");
      section.className = "r-component-section";
      section.type = "button";
      // Closed by default: an archive of any size filled the nav with every
      // page, post, and topic at once. The section holding whatever is
      // currently open is expanded explicitly, so the reader can always see
      // where it is.
      if (selected) readerComponentOpen.add(openKey);
      const initiallyOpen = readerComponentOpen.has(openKey);
      section.setAttribute("aria-expanded", initiallyOpen ? "true" : "false");
      section.innerHTML = '<span class="r-component-chevron" aria-hidden="true">' +
        (initiallyOpen ? "▾" : "▸") + '</span><span>' + label + '</span>';
      const content = document.createElement("div");
      content.className = "r-component-content";
      content.hidden = !initiallyOpen;
      // A category holding exactly one container registers that container
      // here, so opening "WIKIS" opens the one wiki inside it in the same
      // click instead of asking for a second one at a fork with one road.
      content.expandHooks = [];
      section.addEventListener("click", () => {
        const open = section.getAttribute("aria-expanded") === "true";
        section.setAttribute("aria-expanded", open ? "false" : "true");
        content.hidden = open;
        section.querySelector(".r-component-chevron").textContent = open ? "▸" : "▾";
        if (open) readerComponentOpen.delete(openKey);
        else {
          readerComponentOpen.add(openKey);
          content.expandHooks.forEach((fn) => fn());
          revealExpanded(section, content);
        }
      });
      nav.appendChild(section);
      nav.appendChild(content);
      return content;
    };
    // The app sections for ONE scope: either the whole model, or the
    // containers of a single community when the archive holds several.
    // `keyPrefix` keeps two communities' identically-named sections from
    // sharing one open/closed state -- both are called "WIKIS".
    function renderAppSections(scope, keyPrefix) {
        const wikiContent = (scope.wikis || []).length
          ? addSection("WIKIS", !!realWikiId && !!realPageId, keyPrefix) : null;
        (scope.wikis || []).forEach((wiki) => {
          const group = addReaderContainer(
            wikiContent, "wiki:" + wiki.id, wiki.title || wiki.id, wiki.id === realWikiId,
            (scope.wikis || []).length === 1);
          preorderWikiPages(wiki).forEach(({ id, page, depth }) => {
            const cur = wiki.id === realWikiId && id === realPageId;
            group.appendChild(_navLink(page.title || page.label || id, depth, cur, () => openReaderPageReal(wiki.id, id), "wiki:" + wiki.id + ":" + id));
          });
        });
        const blogContent = (scope.blogs || []).length
          ? addSection("BLOGS", !!realBlogId && !!realPostId, keyPrefix) : null;
        (scope.blogs || []).forEach((blog) => {
          const group = addReaderContainer(
            blogContent, "blog:" + blog.id,
            "Blog · " + (blog.title || blog.handle || blog.id), blog.id === realBlogId,
            (scope.blogs || []).length === 1);
          const blogEntries = [];
          readerOrdered(blog.post_ids, (id) => blog.posts && blog.posts[id]).forEach((postId) => {
            const post = blog.posts && blog.posts[postId];
            if (!post) return;
            const cur = blog.id === realBlogId && postId === realPostId;
            const link = _navLink(post.title || postId, 0, cur, () => openReaderPostReal(blog.id, postId), "blog:" + blog.id + ":" + postId);
            group.appendChild(link);
            blogEntries.push({ el: link, published: post.created });
          });
          addReaderDateMarkers(group, blogEntries);
          addReaderDateFilter(group, blogEntries, group.headerEl);
        });
        const forumContent = (scope.forums || []).length
          ? addSection("FORUMS", !!realForumId && !!realTopicId, keyPrefix) : null;
        (scope.forums || []).forEach((forum) => {
          // A synthetic single-thread group (`id` starts "topic-") means the
          // real forum was never identified/named -- label it "Thread" with no
          // uuid/title suffix instead of "Forum · <title>", which (when the
          // forum name is unknown) would otherwise fall back to the topic's
          // OWN title, making the header read as a duplicate of the one
          // entry below it.
          const isStandaloneThread = (forum.id || "").indexOf("topic-") === 0;
          const label = isStandaloneThread ? "Thread" : "Forum";
          const suffix = forum.title || (isStandaloneThread ? "" : forum.id);
          const group = addReaderContainer(
            forumContent, "forum:" + forum.id,
            label + (suffix ? " · " + suffix : ""), forum.id === realForumId,
            (scope.forums || []).length === 1);
          const forumEntries = [];
          readerOrdered(forum.topic_ids, (id) => forum.topics && forum.topics[id]).forEach((topicId) => {
            const topic = forum.topics && forum.topics[topicId];
            if (!topic) return;
            const cur = forum.id === realForumId && topicId === realTopicId;
            const link = _navLink(topic.title || topicId, 0, cur, () => openReaderTopicReal(forum.id, topicId), "forum:" + forum.id + ":" + topicId);
            group.appendChild(link);
            forumEntries.push({ el: link, published: topic.created });
          });
          addReaderDateMarkers(group, forumEntries);
          addReaderDateFilter(group, forumEntries, group.headerEl);
        });
        const rcContent = (scope.rich_content || []).length
          ? addSection("HIGHLIGHTS", !!realRichContentId, keyPrefix) : null;
        (scope.rich_content || []).forEach((container) => {
          const group = addReaderContainer(
            rcContent, "rc:" + container.id, container.title || "Highlights",
            container.id === realRichContentId, (scope.rich_content || []).length === 1);
          (container.page_ids || []).forEach((pageId) => {
            const page = container.pages && container.pages[pageId];
            if (!page) return;
            const cur = container.id === realRichContentId && pageId === realRichPageId;
            group.appendChild(_navLink(
              page.title || pageId, 0, cur,
              () => openReaderRichContentReal(container.id, pageId),
              "rc:" + container.id + ":" + pageId));
          });
          // Widgets placed and never written in have no page to link to. Saying so
          // here is the only place a reader learns the difference between "three
          // pages" and "three pages and a space nobody used".
          const empty = (container.placed || 0) - (container.initialized || 0);
          if (empty > 0) {
            const note = document.createElement("div");
            note.className = "r-datemark foot-note";
            note.textContent = empty + (empty === 1 ? " area was" : " areas were") +
              " placed but never written in";
            group.appendChild(note);
          }
        });
        const filesContent = (scope.file_libraries || []).length
          ? addSection("FILES", !!realLibraryId, keyPrefix) : null;
        const soleLibrary = (scope.file_libraries || []).length === 1;
        (scope.file_libraries || []).forEach((library) => {
          // One link per library, straight to its listing -- no container group,
          // because a library IS the listing rather than a set of documents to
          // open one at a time.
          filesContent.appendChild(_navLink(
            library.title || "Files", 0, library.id === realLibraryId,
            () => openReaderFilesReal(library.id), "files:" + library.id));
          // The same principle as a category with one container, applied to the
          // one shape that has no second expander to link: with a single library
          // the link's click is a foregone conclusion -- and the category reads
          // "FILES" above a link reading "Files". Opening the category opens the
          // listing. Guarded on the current selection so expanding a category you
          // are already reading does not re-render it under you.
          if (soleLibrary && filesContent.expandHooks) {
            filesContent.expandHooks.push(() => {
              if (realLibraryId === library.id) return;
              openReaderFilesReal(library.id);
            });
          }
        });
    }
    const communities = communitiesIn(REAL_MODEL);
    // Grouping is decided by how many NAMED communities there are. A single
    // community plus containers that record none is still one community's
    // archive, and heading it "Platform Engineering" / "Not in a community"
    // would make an attribution gap look like a second community.
    const namedCommunities = communities.filter((community) => community.uuid);
    if (namedCommunities.length > 1) {
      // A set of communities reads as a pile unless the pile is sorted by
      // the thing that distinguishes it. One community stays flat: the
      // heading would be a level with one child everywhere.
      communities.forEach((community) => {
        const head = document.createElement('div');
        head.className = 'r-community-head';
        head.textContent = community.title || community.uuid || 'Not in a community';
        nav.appendChild(head);
        renderAppSections(scopeForCommunity(REAL_MODEL, community.uuid), (community.uuid || 'none') + ':');
      });
    } else {
      renderAppSections(REAL_MODEL, '');
    }
    // Every reader open funnels through here after setting the selection, so
    // this is the one place to keep the export-scope dropdown in step with it.
    refreshPdfScopeOption();
  }

  function renderAttachmentsReal(page) {
    if (!page.attachments || !page.attachments.length) return "";
    const rows = page.attachments.map((att) => {
      const url = att.asset && att.asset.present ? blobUrl(att.asset.blob_hash) : null;
      const nameEl = url
        ? '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(att.filename || att.id) + '</a>'
        : escapeHtml(att.filename || att.id);
      const okBadge = url ? '<div class="fok">✓ archived</div>' : '<div class="fok" style="color:var(--warn)">not present</div>';
      return '<div class="file-row"><div class="fi">' + escapeHtml(((att.content_type || "file").split("/").pop() || "file").slice(0, 3).toUpperCase()) + '</div>' +
        '<div class="fn"><div class="fnm">' + nameEl + '</div><div class="fmeta"><span>' + escapeHtml(att.content_type || "") + '</span></div></div>' + okBadge + '</div>';
    }).join("");
    return '<div class="r-attach"><div class="sh">Attachments</div><div class="files-list">' + rows + '</div></div>';
  }

  function renderDownloadAssetsReal(assets) {
    const rows = (assets || []).filter((asset) =>
      asset.present && asset.blob_hash && /\/download(?:\/|\?)/i.test(asset.original_href || "")
    ).map((asset) => {
      const url = blobUrl(asset.blob_hash);
      if (!url) return "";
      const label = asset.filename || "Archived file (filename not provided by feed)";
      const nodeId = (asset.original_href || "").split("nodeId=")[1] || "";
      return '<div class="file-row"><div class="fi">FILE</div><div class="fn"><div class="fnm">' +
        escapeHtml(label) + '</div><div class="fmeta"><span>' +
        escapeHtml(asset.content_type || "download") + '</span>' +
        (nodeId ? '<span>node ' + escapeHtml(nodeId) + '</span>' : "") +
        '</div></div>' +
        '<a class="btn" href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer">Open</a></div>';
    }).join("");
    return rows ? '<div class="r-attach inline-downloads"><div class="sh">Attachments</div><div class="files-list">' + rows + '</div></div>' : "";
  }

  // Render an entity body for the reader: the sandboxed body frame, PLUS a
  // refresh of `readerNavLinks` so the frame's own in-export <a> links become
  // clickable reader navigation (wired by the frame `load` handler).
  function readerBody(bodyHtml, assets, links) {
    readerNavLinks = new Map();
    for (const link of links || []) {
      const r = classifyLink(link);
      if (r.kind === "in_export" && r.targetPageId) {
        readerNavLinks.set(link.original_href, r.targetPageId);
      }
    }
    return sandboxedBodyMarkup(resolveBodyImages(bodyHtml, assets));
  }

  function renderLinksReal(page) {
    // Only show in-export navigation links (they become clickable buttons).
    // hcl_deployment and external links are archived for provenance but
    // distracting in the reader — especially localhost/dev-server artifacts.
    const links = page.links || [];
    const inExport = links.filter((link) => {
      const kind = classifyLink(link).kind;
      return kind === "in_export" || kind === "in_export_file";
    });
    if (!inExport.length) return "";
    const rows = inExport.map((link) => {
      const r = classifyLink(link);
      if (r.kind === "in_export_file") {
        // Straight to the bytes. A captured document has no page to navigate
        // to, and sending the reader to one would be a dead end wearing the
        // same badge as a working link.
        const file = fileById(r.targetFileId);
        const url = file && file.asset && file.asset.present ? blobUrl(file.asset.blob_hash) : null;
        const name = escapeHtml((file && file.name) || r.targetFileId);
        const body = url
          ? '<a href="' + escapeHtml(url) + '" download="' + name + '">' + name + "</a>"
          : name;
        return '<div class="result"><div class="rt">' + body + '</div><div class="rc"><span class="rloc">' +
          (url
            ? "file in this archive"
            : (file && file.excluded_by_author_filter
                ? "file recorded, outside this archive's author filter"
                : "file recorded, bytes not captured")) + "</span></div></div>";
      }
      return '<button class="result" data-nav-page="' + escapeHtml(r.targetPageId) + '"><div class="rt">' + escapeHtml(link.original_href) + '</div><div class="rc"><span class="rloc">in this archive</span></div></button>';
    }).join("");
    return '<div class="r-attach"><div class="sh">Links in this archive</div><div class="files-list">' + rows + '</div></div>';
  }

  // A captured document by its id, wherever in the export it lives -- a body
  // routinely links a file held by a different community than the one it was
  // written in.
  // A document without bytes is not always a document that failed. When the
  // capture was filtered to one person, the documents outside that filter
  // were never asked for -- saying "not captured" about those told the reader
  // something was missing when it had only been left out on purpose.
  function absenceLabel(file) {
    return file && file.excluded_by_author_filter ? "outside the filter" : "not captured";
  }

  function fileById(fileId) {
    if (!fileId || !REAL_MODEL) return null;
    for (const library of REAL_MODEL.file_libraries || []) {
      const found = (library.files || {})[fileId];
      if (found) return found;
    }
    return null;
  }

  // Shared threaded-comment markup for wiki page comments AND blog post
  // comments: both thread by `parent_comment_id`
  // (via the pure `threadComments`); a page comment dates from `created`, a
  // blog comment from `published`, so either is shown. Comment text is
  // stripped to plain text -- a comment body is never a sandboxed render.
  // Render reply/comment body HTML inline (not sandboxed -- one iframe per
  // reply in a thread is impractical). Content comes from our own archive so
  // it's trusted; images are resolved to blob: URLs like the main body.
  function inlineBody(html, assets) {
    if (!html) return '<em style="color:var(--muted)">no content</em>';
    return '<div class="prose inline-body">' + resolveBodyImages(html, assets) + '</div>';
  }

  function commentThreadHtml(comments, emptyText) {
    const threaded = threadComments(comments);
    if (!threaded.length) return '<div class="drawer-hint">' + escapeHtml(emptyText || "No comments.") + '</div>';
    return '<div class="thread">' + threaded.map(({ comment, depth }) =>
      '<div class="cmt-item' + (depth > 0 ? " nested" : "") + '"><div class="ch"><span class="who">' + escapeHtml(comment.author || "Unknown") + '</span>' +
      whenHtml(comment.created, "when") +
      (depth > 0 ? '<span class="reply">↳ reply</span>' : "") + '</div><div class="ctext">' + inlineBody(comment.content_html, comment.assets) + '</div></div>'
    ).join("") + '</div>';
  }

  function renderCommentsReal(page) {
    const count = threadComments(page.comments).length;
    return '<div class="r-comments"><h2>Comments · ' + count + '</h2>' +
      commentThreadHtml(page.comments, "No comments on this page.") + '</div>';
  }

  // A forum topic's reply thread rendered from the id-keyed tree
  //: `preorderForumReplies` flattens it
  // depth-first with a depth, and each row is indented by that depth
  // (arbitrary depth -- not the single `.nested` step page/blog comments
  // use) and shows the reply's author, date, and any `sn/flags` (e.g.
  // `answer`). Reply text is stripped to plain text, never a live render.
  function renderReplyThread(topic) {
    const flat = preorderForumReplies(topic);
    if (!flat.length) return '<div class="r-comments"><h2>Replies · 0</h2><div class="drawer-hint">No replies on this topic.</div></div>';
    const items = flat.map(({ reply, depth }) =>
      '<div class="cmt-item' + (depth > 0 ? " nested" : "") + '" style="margin-left:' + (depth * 18) + 'px">' +
      '<div class="ch"><span class="who">' + escapeHtml(reply.author || "Unknown") + '</span>' +
      whenHtml(reply.created, "when") +
      ((reply.flags || []).map((f) => '<span class="reply">' + escapeHtml(f) + '</span>').join("")) +
      '</div><div class="ctext">' + inlineBody(reply.content_html, reply.assets) +
      '</div>' + renderDownloadAssetsReal(reply.assets) +
      (reply.attachments && reply.attachments.length ? renderAttachmentsReal(reply) : "") +
      '</div>'
    ).join("");
    return '<div class="r-comments"><h2>Replies · ' + flat.length + '</h2><div class="thread">' + items + '</div></div>';
  }

  // Navigate the reader to a target page id, resolving its wiki by searching
  // every wiki for the id -- so a link works from a wiki page, a blog post, or
  // a forum topic.
  // Every container the model can hold, not just wikis. `crosslink` resolves
  // links to posts, topics and Highlights pages too, and each of those became
  // a button that said "in this archive" and did nothing at all when clicked.
  const NAVIGABLE = [
    ["wikis", "pages", (container, id) => openReaderPageReal(container.id, id)],
    ["blogs", "posts", (container, id) => openReaderPostReal(container.id, id)],
    ["forums", "topics", (container, id) => openReaderTopicReal(container.id, id)],
    ["rich_content", "pages", (container, id) => openReaderRichContentReal(container.id, id)],
  ];

  // Opening a section near the bottom of the nav's own scroll area unfolds
  // its contents INTO the part below the fold: the header stays put, nothing
  // visible changes, and it reads as a click that did nothing. Scroll far
  // enough that a few entries show, and only when they would not have.
  const REVEALED_ENTRIES = 4;

  function revealExpanded(head, content) {
    const nav = head.closest(".r-nav");
    if (!nav || !content || content.hidden) return;
    const entry = content.querySelector("a, .r-container-section");
    const entryHeight = (entry && entry.getBoundingClientRect().height) || 28;
    const navBox = nav.getBoundingClientRect();
    const box = content.getBoundingClientRect();
    // Whichever is smaller: a few entries, or all there is to show.
    const wanted = Math.min(box.height, entryHeight * REVEALED_ENTRIES);
    const hiddenBelow = box.top + wanted - navBox.bottom;
    if (hiddenBelow > 0) {
      nav.scrollTop += hiddenBelow;
      // Never past the header itself: losing what you just opened is the
      // same complaint from the other side.
      const headTop = head.getBoundingClientRect().top - nav.getBoundingClientRect().top;
      if (headTop < 0) nav.scrollTop += headTop;
    }
  }

  // Exposed so a browser test can drive it directly: the behaviour depends on
  // real layout, and building a nav deep enough to reproduce it through the
  // UI takes a full archive and a lot of clicking.
  window.__revealExpandedProbe = revealExpanded;

  function navigateToPageId(pageId) {
    if (!REAL_MODEL) return;
    for (const [collection, bucket, open] of NAVIGABLE) {
      for (const container of (REAL_MODEL[collection] || [])) {
        const items = container[bucket];
        if (items && Object.prototype.hasOwnProperty.call(items, pageId)) {
          clearSearchInput();
          open(container, pageId);
          return;
        }
      }
    }
  }

  // Bind in-export link buttons (`data-nav-page`) to open the target page.
  function bindInExportLinks(container) {
    container.querySelectorAll("[data-nav-page]").forEach((btn) => {
      btn.addEventListener("click", () => navigateToPageId(btn.dataset.navPage));
    });
  }

  function originalPageLink(url) {
    if (!url) return "";
    return '<a class="original-link" href="' + escapeHtml(url) +
      '" target="_blank" rel="noopener noreferrer">Open original</a>';
  }

  function openReaderPageReal(wikiId, pageId, highlight) {
    if (!REAL_MODEL) return;
    const wiki = (REAL_MODEL.wikis || []).find((w) => w.id === wikiId);
    const page = wiki && wiki.pages && wiki.pages[pageId];
    if (!wiki || !page) return;
    clearReaderSelection();
    realWikiId = wikiId; realPageId = pageId;
    updateReaderNavSelection("wiki:" + wikiId + ":" + pageId);
    $("r-crumb").innerHTML = '<b>' + escapeHtml(wiki.title) + '</b><span>›</span><span>' + escapeHtml(page.title || page.label || pageId) + '</span>';
    let html = '<h1>' + hl(page.title || page.label || pageId, highlight) + '</h1>';
    html += '<div class="ameta">' +
      (page.author ? '<span>' + escapeHtml(page.author) + '</span>' : "") +
      (page.modified ? '<span>updated ' + escapeHtml(page.modified) + '</span>' : "") +
      '<span>' + (page.comments ? page.comments.length : 0) + ' comments</span>' +
      originalPageLink(page.alternate_url) + '</div>';
    html += '<div class="prose">' + readerBody(page.content_html, page.assets, page.links) + '</div>';
    html += renderAttachmentsReal(page);
    html += renderLinksReal(page);
    html += renderCommentsReal(page);
    $("r-article").innerHTML = html;
    bindInExportLinks($("r-article"));
    window.scrollTo(0, 0);
  }

  // A blog post: title + author/date, then its body through the SAME
  // sandboxed render path as a wiki page (author CSS preserved, scripts
  // inert), then its threaded comments.
  function openReaderPostReal(blogId, postId, highlight) {
    if (!REAL_MODEL) return;
    const blog = (REAL_MODEL.blogs || []).find((b) => b.id === blogId);
    const post = blog && blog.posts && blog.posts[postId];
    if (!blog || !post) return;
    clearReaderSelection();
    realBlogId = blogId; realPostId = postId;
    updateReaderNavSelection("blog:" + blogId + ":" + postId);
    $("r-crumb").innerHTML = '<b>' + escapeHtml(blog.title || blog.handle || blogId) + '</b><span>›</span><span>' + escapeHtml(post.title || postId) + '</span>';
    let html = '<h1>' + hl(post.title || postId, highlight) + '</h1>';
    html += '<div class="ameta">' +
      (post.author ? '<span>' + escapeHtml(post.author) + '</span>' : "") +
      whenHtml(post.created) +
      (post.modified ? '<span>updated ' + escapeHtml(post.modified) + '</span>' : "") +
      (post.tags || []).map((t) => '<span class="rloc">' + escapeHtml(t) + '</span>').join("") +
      '<span>' + (post.comments ? post.comments.length : 0) + ' comments</span>' +
      originalPageLink(post.alternate_url) + '</div>';
    html += '<div class="prose">' + readerBody(post.content_html, post.assets, post.links) + '</div>';
    html += renderLinksReal(post);
    html += '<div class="r-comments"><h2>Comments · ' + threadComments(post.comments).length + '</h2>' +
      commentThreadHtml(post.comments, "No comments on this post.") + '</div>';
    $("r-article").innerHTML = html;
    bindInExportLinks($("r-article"));
    window.scrollTo(0, 0);
  }

  // A forum topic: title + flags, then its body through the SAME sandboxed
  // render path, then its reply thread rendered from the id-keyed tree with
  // per-depth indentation.
  function openReaderTopicReal(forumId, topicId, highlight) {
    if (!REAL_MODEL) return;
    const forum = (REAL_MODEL.forums || []).find((f) => f.id === forumId);
    const topic = forum && forum.topics && forum.topics[topicId];
    if (!forum || !topic) return;
    clearReaderSelection();
    realForumId = forumId; realTopicId = topicId;
    updateReaderNavSelection("forum:" + forumId + ":" + topicId);
    $("r-crumb").innerHTML = '<b>' + escapeHtml(forum.title || forumId) + '</b><span>›</span><span>' + escapeHtml(topic.title || topicId) + '</span>';
    let html = '<h1>' + hl(topic.title || topicId, highlight) + '</h1>';
    html += '<div class="ameta">' +
      (topic.author ? '<span>' + escapeHtml(topic.author) + '</span>' : "") +
      whenHtml(topic.created) +
      (topic.flags || []).map((f) => '<span class="rloc">' + escapeHtml(f) + '</span>').join("") +
      (topic.tags || []).map((t) => '<span class="rloc">' + escapeHtml(t) + '</span>').join("") +
      originalPageLink(topic.alternate_url) + '</div>';
    html += '<div class="prose">' + readerBody(topic.content_html, topic.assets, topic.links) + '</div>';
    html += renderAttachmentsReal(topic);
    html += renderLinksReal(topic);
    html += renderReplyThread(topic);
    $("r-article").innerHTML = html;
    bindInExportLinks($("r-article"));
    window.scrollTo(0, 0);
  }

  //: Sizes as a person reads them. The exact byte count stays available in
  //: the package metadata; a listing wants "2.0 MB".
  function humanSize(bytes) {
    if (bytes === null || bytes === undefined || !isFinite(bytes)) return "";
    const units = ["bytes", "KB", "MB", "GB", "TB"];
    let value = Number(bytes), unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
    return (unit === 0 ? value : value.toFixed(1)) + " " + units[unit];
  }

  // A community's files, as a listing. Deliberately NOT an attempt to render
  // the documents: a spreadsheet or a zip has no reader view, and pretending
  // otherwise would be worse than saying plainly what the archive holds. Each
  // captured file links to its bytes; each missing one says so.
  function openReaderFilesReal(libraryId) {
    if (!REAL_MODEL) return;
    const library = (REAL_MODEL.file_libraries || []).find((l) => l.id === libraryId);
    if (!library) return;
    clearReaderSelection();
    realLibraryId = libraryId;
    updateReaderNavSelection("files:" + libraryId);
    const title = library.title || "Files";
    $("r-crumb").innerHTML = "<b>" + escapeHtml(title) + "</b>";

    const folderNames = {};
    (library.folders || []).forEach((folder) => {
      (folder.file_ids || []).forEach((id) => {
        folderNames[id] = (folderNames[id] ? folderNames[id] + ", " : "") + (folder.name || "");
      });
    });

    const rows = (library.file_ids || []).map((id) => {
      const file = (library.files || {})[id];
      if (!file) return "";
      const url = file.asset && file.asset.present ? blobUrl(file.asset.blob_hash) : null;
      // The name is the file's own, shown exactly as the deployment had it --
      // any renaming the package had to do to make it safe on disk is recorded
      // in files.json, not applied to what the reader is told the file is called.
      const name = escapeHtml(file.name || id);
      return "<tr>" +
        "<td>" + (url
          ? '<a href="' + escapeHtml(url) + '" download="' + name + '">' + name + "</a>"
          : name) + "</td>" +
        "<td>" + escapeHtml(folderNames[id] || "") + "</td>" +
        "<td>" + escapeHtml(humanSize(file.size)) + "</td>" +
        "<td>" + escapeHtml(file.version_label || "") + "</td>" +
        "<td>" + escapeHtml(file.author || "") + "</td>" +
        '<td class="' + (url ? "fl-have" : "fl-missing") + '">' +
          (url ? "in archive" : escapeHtml(absenceLabel(file))) + "</td>" +
        "</tr>";
    }).join("");

    const count = (library.file_ids || []).length;
    $("r-article").innerHTML =
      "<h1>" + escapeHtml(title) + "</h1>" +
      '<div class="ameta"><span>' + count + " file" + (count === 1 ? "" : "s") + "</span></div>" +
      (count
        ? '<table class="fl-table"><thead><tr><th>Name</th><th>Folder</th><th>Size</th>' +
          "<th>Version</th><th>Author</th><th>Content</th></tr></thead><tbody>" +
          rows + "</tbody></table>"
        : '<div class="prose"><p>This library holds no files.</p></div>');
    window.scrollTo(0, 0);
  }

  // A community's Highlights page. Unlike a file library this HAS a body --
  // prose, tables and images -- so it renders like a wiki page, through the
  // same sandboxed body path as every other captured document.
  function openReaderRichContentReal(containerId, pageId, highlight) {
    if (!REAL_MODEL) return;
    const container = (REAL_MODEL.rich_content || []).find((c) => c.id === containerId);
    const page = container && container.pages && container.pages[pageId];
    if (!container || !page) return;
    clearReaderSelection();
    realRichContentId = containerId; realRichPageId = pageId;
    updateReaderNavSelection("rc:" + containerId + ":" + pageId);
    $("r-crumb").innerHTML = "<b>" + escapeHtml(container.title || "Highlights") +
      "</b><span>\u203a</span><span>" + escapeHtml(page.title || pageId) + "</span>";
    let html = "<h1>" + hl(page.title || pageId, highlight) + "</h1>";
    html += '<div class="ameta">' +
      (page.author ? "<span>" + escapeHtml(page.author) + "</span>" : "") +
      whenHtml(page.created) +
      (page.version_label
        ? '<span class="rloc">version ' + escapeHtml(page.version_label) + "</span>"
        : "") +
      originalPageLink(page.alternate_url) + "</div>";
    html += '<div class="prose">' + readerBody(page.content_html, page.assets, page.links) + "</div>";
    $("r-article").innerHTML = html;
    bindInExportLinks($("r-article"));
    window.scrollTo(0, 0);
  }

  function renderResultsReal(q) {
    const results = searchReaderModel(REAL_MODEL, q);
    $("r-crumb").innerHTML = '<b>Search</b><span>›</span><span>“' + escapeHtml(q) + '”</span>';
    const head = '<div class="results-head">' + results.length + ' result' + (results.length === 1 ? "" : "s") + ' for “' + escapeHtml(q) + '”' + (results.length ? "" : " — nothing matched") + '</div>';
    // A result routes by its `kind`: a `page`
    // opens the wiki reader, a `post` the blog reader, a `topic` the forum
    // reader. The kind + ids ride on data-attributes read back on click.
    const dataFor = (r) => r.kind === "post"
      ? 'data-kind="post" data-blog="' + escapeHtml(r.blogId) + '" data-post="' + escapeHtml(r.postId) + '"'
      : r.kind === "topic"
        ? 'data-kind="topic" data-forum="' + escapeHtml(r.forumId) + '" data-topic="' + escapeHtml(r.topicId) + '"'
        : 'data-kind="page" data-wiki="' + escapeHtml(r.wikiId) + '" data-page="' + escapeHtml(r.pageId) + '"';
    const groupFor = (r) => r.kind === "post" ? "blog" : r.kind === "topic" ? "forum" : "wiki";
    $("r-article").innerHTML = head + results.map((r) =>
      '<button class="result" ' + dataFor(r) + '><div class="rt">' + hl(r.title, q) + '</div>' +
      '<div class="rc"><span class="rloc">' + groupFor(r) + '</span><span>' + escapeHtml(r.wikiTitle) + '</span>' + r.loc.map((l) => '<span class="rloc">' + l + '</span>').join("") + '</div>' +
      '<div class="rsnip">' + snippet(r.loc.includes("body") ? r.bodyText : r.loc.includes("comments") ? r.commentText : r.title, q) + '</div></button>'
    ).join("");
    $("r-article").querySelectorAll(".result").forEach((btn) => btn.addEventListener("click", () => {
      const k = btn.dataset.kind;
      if (k === "post") openReaderPostReal(btn.dataset.blog, btn.dataset.post, q);
      else if (k === "topic") openReaderTopicReal(btn.dataset.forum, btn.dataset.topic, q);
      else openReaderPageReal(btn.dataset.wiki, btn.dataset.page, q);
    }));
    window.scrollTo(0, 0);
  }

  // The first blog post, then the first forum topic -- the fallback entry
  // point for an archive that has no wiki pages.
  function firstPostOrTopic(model) {
    for (const blog of (model && model.blogs) || []) {
      const pid = readerOrdered(blog.post_ids, (id) => blog.posts && blog.posts[id])
        .find((id) => blog.posts && blog.posts[id]);
      if (pid) return () => openReaderPostReal(blog.id, pid);
    }
    for (const forum of (model && model.forums) || []) {
      const tid = readerOrdered(forum.topic_ids, (id) => forum.topics && forum.topics[id])
        .find((id) => forum.topics && forum.topics[id]);
      if (tid) return () => openReaderTopicReal(forum.id, tid);
    }
    return null;
  }

  // #15: show WHICH archive/source the reader is showing, in the header badge.
  function setReaderSource(label) {
    const el = document.getElementById("r-source");
    if (el) el.textContent = label || "reconstructed archive";
  }

  // Show which archive is currently in use, in BOTH the ingest topbar and the
  // reader header -- so you always know what you're looking at. Reads the
  // server's current-archive so it survives navigation, reload, and open-archive.
  async function refreshCurrentArchive() {
    let j;
    try {
      const r = await fetch("/api/current-archive");
      if (!r.ok) return;
      j = await r.json();
    } catch (_) { return; }
    const name = j && j.name;
    const stateTag = j && j.state === "partial" ? " · live" : "";
    const ia = $("ingest-archive");
    if (ia) {
      if (name) { ia.hidden = false; ia.innerHTML = icon("package") + " " + escapeHtml(name) + escapeHtml(stateTag); }
      else { ia.hidden = true; }
    }
    const rs = $("r-source");
    if (rs && name) rs.innerHTML = icon("package") + " " + escapeHtml(name) + escapeHtml(stateTag);
  }

  // Expand the nav down to one entity and rebuild, so arriving at a
  // particular thing shows where it sits. Opening an entity only moves the
  // `.cur` class (`updateReaderNavSelection`); it never rebuilt the nav, so
  // the collapsed state computed before anything was selected is what stuck.
  function revealReaderPath(kind, containerId) {
    if (!containerId) return;
    const section = kind === "wiki" ? "WIKIS"
      : kind === "blog" ? "BLOGS"
        : kind === "forum" ? "FORUMS" : "HIGHLIGHTS";
    readerComponentOpen.add(section);
    readerContainerOpen.add(kind === "rich_content" ? "rc:" + containerId : kind + ":" + containerId);
    buildReaderNavReal();
  }

  // `reveal` distinguishes arriving WITH something in mind -- an "Open
  // reader" button after a run, a live node, a chosen archive -- from
  // browsing in generically via the sidebar, where everything stays shut.
  function enterReaderReal(targetNode, opts) {
    const reveal = !!(opts && opts.reveal);
    stopRealPolling();
    clearSearchInput();
    const openBest = (model) => {
      buildReaderNavReal();
      // #20/#21: a post/topic node deep-links into the reader at the same
      // entity (`openReaderPostReal`/`openReaderTopicReal`), not the first
      // page -- `findRealEntityByLiveNode` covers all three apps.
      const deep = findRealEntityByLiveNode(model, targetNode);
      if (deep) {
        if (deep.app === "blog") { openReaderPostReal(deep.blogId, deep.postId); revealReaderPath("blog", deep.blogId); return; }
        if (deep.app === "forum") { openReaderTopicReal(deep.forumId, deep.topicId); revealReaderPath("forum", deep.forumId); return; }
        if (deep.app === "rich_content") { openReaderRichContentReal(deep.containerId, deep.pageId); revealReaderPath("rich_content", deep.containerId); return; }
        openReaderPageReal(deep.wikiId, deep.pageId); revealReaderPath("wiki", deep.wikiId); return;
      }
      // Honour whatever was last open across a re-entry (page, post, or
      // topic) -- that is something in mind by definition, so reveal it.
      if (realWikiId && realPageId) { openReaderPageReal(realWikiId, realPageId); revealReaderPath("wiki", realWikiId); return; }
      if (realBlogId && realPostId) { openReaderPostReal(realBlogId, realPostId); revealReaderPath("blog", realBlogId); return; }
      if (realForumId && realTopicId) { openReaderTopicReal(realForumId, realTopicId); revealReaderPath("forum", realForumId); return; }
      // Nothing specific was asked for. Content still opens so the pane is
      // not blank, but the nav only unfolds when the reader was entered
      // FROM something -- a run, a node, a chosen archive.
      const firstPage = firstRealPage(model);
      if (firstPage) {
        openReaderPageReal(firstPage.wikiId, firstPage.pageId);
        if (reveal) revealReaderPath("wiki", firstPage.wikiId);
        return;
      }
      const openFallback = firstPostOrTopic(model);
      if (openFallback) {
        openFallback();
        if (reveal) {
          if (realBlogId) revealReaderPath("blog", realBlogId);
          else if (realForumId) revealReaderPath("forum", realForumId);
        }
        return;
      }
      for (const container of (model && model.rich_content) || []) {
        const pageId = (container.page_ids || []).find((id) => container.pages && container.pages[id]);
        if (pageId) {
          openReaderRichContentReal(container.id, pageId);
          if (reveal) revealReaderPath("rich_content", container.id);
          return;
        }
      }
      renderReaderMessage("No pages imported yet — they'll appear here as the import runs.", true, true);
    };
    if (REAL_MODEL) { openBest(REAL_MODEL); scheduleLiveRefresh(); return; }
    renderReaderMessage("Loading imported pages…", true);
    fetchReaderModel(openBest);
  }

  // ---------- reader entry point: always the real, server-backed reader
  // ----------
  function enterReader(opts) {
    showSection("reader");
    loadShellIdentity();
    setReaderSource(
      lastStartBody && lastStartBody.demo
        ? "Demo (fake data)"
        : (lastStartBody && lastStartBody.base_url) || "This run"
    );
    enterReaderReal(undefined, opts);
  }
  function exitReader() {
    stopRealPolling();
    stopLiveRefresh();
    showSection("ingest");
  }
  // Enable/disable both reader entry points together (the prominent top
  // CTA and the footer button). Enabled as soon as the first page is
  // imported: before that /api/model is 503 and there
  // is nothing to browse.
  function setReaderEnabled(on) {
    ["open-reader", "open-reader-top"].forEach((id) => {
      const b = $(id); if (!b) return;
      b.disabled = !on;
      b.title = on ? "Browse the reconstructed archive — updates live as pages arrive"
                   : "Available as soon as the first page is imported";
    });
    const lp = $("live-pdf-preview");
    if (lp) {
      lp.disabled = !on;
      lp.title = on ? "Watch the export PDF build up as pages are imported"
                    : "Available as soon as the first page is imported";
    }
  }
  $("open-reader").addEventListener("click", () => enterReader({ reveal: true }));
  $("open-reader-top").addEventListener("click", () => enterReader({ reveal: true }));
  $("live-pdf-preview") && $("live-pdf-preview").addEventListener("click", startLivePdfPreview);
  $("r-exit").addEventListener("click", exitReader);
  // The export menu is a <details>; without this it stays open behind the
  // render it just started, and over whatever the reader shows next.
  (function wireExportMenu() {
    const menu = document.getElementById("r-export-menu");
    if (!menu) return;
    ["r-pdf", "r-preview", "r-live-pdf"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("click", () => { menu.open = false; });
    });
    document.addEventListener("click", (e) => {
      if (menu.open && !menu.contains(e.target)) menu.open = false;
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") menu.open = false; });
  })();

  // What single unit is open in the reader right now (for a scoped export) --
  // null when we're at a search/overview with nothing specific selected. The
  // label is what the "Current item" option shows so the choice reads plainly.
  function currentReaderScope() {
    if (realWikiId && realPageId) return { kind: "page", id: realPageId, label: "This page" };
    if (realBlogId && realPostId) return { kind: "post", id: realPostId, label: "This post" };
    if (realForumId && realTopicId) return { kind: "topic", id: realTopicId, label: "This thread" };
    if (realRichContentId && realRichPageId)
      return { kind: "rich_content_page", id: realRichPageId, label: "This page" };
    return null;
  }

  // ---- Fine-grained export picker ----
  // Builds a checkable tree of what the archive holds: every wiki with its
  // top-level pages as subtree roots, every blog and every forum. A wiki page
  // selects the page AND everything under it, which is the shape a wiki
  // hierarchy actually has -- picking a section should not mean picking its
  // children one at a time.
  function renderPdfPicker() {
    const tree = $("pdf-pick-tree");
    if (!tree || !REAL_MODEL) return;
    tree.innerHTML = "";
    const row = (value, text, kindLabel, isPage) => {
      const label = document.createElement("label");
      if (isPage) label.className = "is-page";
      label.innerHTML = '<input type="checkbox" value="' + escapeAttr(value) + '" checked>' +
        "<span>" + escapeHtml(text) + "</span>" +
        (kindLabel ? '<span class="pk-kind">' + escapeHtml(kindLabel) + "</span>" : "");
      tree.appendChild(label);
    };
    (REAL_MODEL.wikis || []).forEach((wiki) => {
      row("wiki:" + wiki.id, wiki.title || wiki.label || wiki.id, "wiki", false);
      (wiki.root_page_ids || []).forEach((pid) => {
        const page = wiki.pages && wiki.pages[pid];
        if (page) row("subtree:" + pid, "↳ " + (page.title || page.label || pid), "section", true);
      });
    });
    (REAL_MODEL.blogs || []).forEach((blog) => {
      row("blog:" + blog.id, blog.title || blog.handle || blog.id, "blog", false);
    });
    (REAL_MODEL.forums || []).forEach((forum) => {
      row("forum:" + forum.id, forum.title || forum.id, "forum", false);
    });
    (REAL_MODEL.rich_content || []).forEach((container) => {
      row("rich_content:" + container.id, container.title || "Highlights", "highlights", false);
      // Individual pages as well as the whole area: a community front page is
      // a handful of pages, and picking one of them is a reasonable ask that
      // the container row alone cannot express.
      (container.page_ids || []).forEach((pid) => {
        const page = container.pages && container.pages[pid];
        if (page) {
          row("rich_content_page:" + pid, "\u21b3 " + (page.title || pid), "page", true);
        }
      });
    });
    // Whole-wiki and subtree rows overlap; ticking the wiki is the common
    // case, so its sections start unticked to avoid asking for both.
    tree.querySelectorAll('input[value^="subtree:"]').forEach((i) => { i.checked = false; });
    // Same overlap as wiki/subtree: ticking the whole Highlights area is the
    // common case, so its individual pages start unticked.
    tree.querySelectorAll('input[value^="rich_content_page:"]').forEach((i) => { i.checked = false; });
  }

  function pdfIncludeParams() {
    const tree = $("pdf-pick-tree");
    if (!tree) return [];
    return Array.from(tree.querySelectorAll("input:checked"))
      .map((i) => "include=" + encodeURIComponent(i.value));
  }

  function wirePdfPicker() {
    const sel = $("r-pdf-scope");
    const pick = $("pdf-pick");
    if (!sel || !pick) return;
    sel.addEventListener("change", () => {
      const choosing = sel.value === "choose";
      pick.hidden = !choosing;
      if (choosing) renderPdfPicker();
    });
    pick.addEventListener("click", (event) => {
      const action = event.target.closest("button")?.dataset.pick;
      if (!action) return;
      pick.querySelectorAll("input").forEach((i) => { i.checked = action === "all"; });
    });
  }

  // Keep the scope dropdown honest: enable + relabel "Current item" to match
  // what's open; disable it (and fall back to Whole archive) when nothing is.
  function refreshPdfScopeOption() {
    const sel = $("r-pdf-scope");
    if (!sel) return;
    const itemOpt = sel.querySelector('option[value="item"]');
    if (!itemOpt) return;
    const scope = currentReaderScope();
    if (scope) {
      itemOpt.textContent = scope.label;
      itemOpt.disabled = false;
    } else {
      itemOpt.textContent = "Current item";
      itemOpt.disabled = true;
      if (sel.value === "item") sel.value = "all";
    }
  }

  // Export the reconstructed archive to a PDF (the `pdf` capability, served
  // via GET /api/pdf). Renders server-side (Chromium) and downloads the
  // bytes; a 503 carries a clear reason (model not ready, or no browser).
  async function exportPdf() {
    const btn = $("r-pdf");
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "⏳ Rendering…";
    try {
      // Comments are always in the archive; the checkbox only omits them from
      // this export (?comments=0).
      const withComments = $("r-pdf-comments") ? $("r-pdf-comments").checked : true;
      const params = [];
      if (!withComments) params.push("comments=0");
      // Scoped export: "one thread vs the whole". Only when the dropdown
      // is on "Current item" AND something is actually open.
      const sel = $("r-pdf-scope");
      const scope = currentReaderScope();
      if (sel && sel.value === "choose") {
        const chosen = pdfIncludeParams();
        if (!chosen.length) {
          notify("Tick at least one component to export.", "Nothing selected");
          return;
        }
        params.push(...chosen);
      } else if (sel && sel.value === "item" && scope) {
        params.push(scope.kind === "page" ? "scope=tile" : "scope=item");
        params.push("kind=" + encodeURIComponent(scope.kind), "id=" + encodeURIComponent(scope.id));
        if (scope.kind === "page") params.push("chrome=0");
      }
      const pdfUrl = "/api/pdf" + (params.length ? "?" + params.join("&") : "");
      let res = await fetch(pdfUrl);
      // A server reload can discard the attached archive while this reader
      // still has its cached model. Reattach the remembered archive once so
      // archive-backed print can recover from that stale 503.
      if (res.status === 503) {
        if (openArchiveName) {
          try {
            await fetch("/api/open-archive", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ name: openArchiveName }),
            });
          } catch (_) {}
        }
        res = await fetch(pdfUrl);
      }
      if (!res.ok) {
        let msg = "PDF export failed (" + res.status + ").";
        try {
          const j = await res.json();
          if (j.detail) msg = j.detail;
          else if (j.status === "pending") msg = "The model isn't ready yet — let the import finish first.";
        } catch (_) {}
        notify(msg);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "export.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (_) {
      notify("PDF export isn't reachable right now.");
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }
  $("r-pdf").addEventListener("click", exportPdf);
  // Accumulating, abortable export preview (page by page). Same viewer the
  // dashboard "Live PDF preview" uses; here it renders the reader's model.
  if ($("r-preview")) $("r-preview").addEventListener("click", startLivePdfPreview);

  // Bind the "Failed" KPI once: showFailures() no-ops when empty, so it's safe
  // even before any failure, and markFailKpiOpenable() lights up the affordance.
  (function bindFailKpi() {
    const kpi = $("k-fail") && $("k-fail").closest(".kpi");
    if (!kpi) return;
    const open = () => showFailures();
    kpi.addEventListener("click", open);
    kpi.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
  })();

  // An inline PDF viewer overlay (built once, reused): shows a rendered PDF in
  // an <iframe> with the native viewer, plus Download and Close. This is what
  // "PDF viewing" means -- see the result without a blind download first. The
  // object URL is revoked when the overlay closes so blobs don't accumulate.
  let pdfViewerObjUrl = null;
  function ensurePdfViewer() {
    let ov = $("pdf-viewer");
    if (ov) return ov;
    ov = document.createElement("div");
    ov.id = "pdf-viewer";
    ov.className = "pdf-viewer";
    ov.innerHTML =
      '<div class="pv-bar">' +
      '<span class="pv-title" id="pv-title"></span>' +
      '<span class="spacer"></span>' +
      '<a class="btn" id="pv-download" download hidden>' + icon('download') + ' Download</a>' +
      '<button class="btn" id="pv-close">✕ Close</button>' +
      '</div><iframe class="pv-frame" id="pv-frame" title="PDF preview"></iframe>';
    document.body.appendChild(ov);
    const close = () => {
      ov.style.display = "none";
      $("pv-frame").src = "about:blank";
      if (pdfViewerObjUrl) { URL.revokeObjectURL(pdfViewerObjUrl); pdfViewerObjUrl = null; }
    };
    // Closing the viewer must also stop a running live-preview loop, so we
    // don't keep re-rendering into a hidden overlay.
    const closeAll = () => { stopLivePdfPreview(); close(); };
    $("pv-close").addEventListener("click", closeAll);
    ov.addEventListener("click", (e) => { if (e.target === ov) closeAll(); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && ov.style.display === "flex") closeAll();
    });
    return ov;
  }

  // Open the viewer immediately with a spinner + message -- so a slow render
  // gives instant feedback instead of a frozen button and a viewer that only
  // appears once everything is finished.
  function showPdfViewerLoading(title, message) {
    const ov = ensurePdfViewer();
    $("pv-title").textContent = title || "Rendering…";
    $("pv-download").hidden = true;
    $("pv-frame").removeAttribute("src");
    $("pv-frame").srcdoc =
      '<body style="margin:0;font-family:system-ui,sans-serif;color:#8a9bad;background:#0a0f16;' +
      'display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:24px;">' +
      '<div><div style="font-size:28px;margin-bottom:12px;">⏳</div>' +
      (message || "Rendering…").replace(/</g, "&lt;") + "</div></body>";
    ov.style.display = "flex";
  }

  function closePdfViewer() {
    const ov = $("pdf-viewer");
    if (!ov) return;
    ov.style.display = "none";
    const frame = $("pv-frame");
    if (frame) { frame.removeAttribute("srcdoc"); frame.src = "about:blank"; }
    if (pdfViewerObjUrl) { URL.revokeObjectURL(pdfViewerObjUrl); pdfViewerObjUrl = null; }
  }

  function showPdfViewer(blob, filename, title) {
    const ov = ensurePdfViewer();
    if (pdfViewerObjUrl) URL.revokeObjectURL(pdfViewerObjUrl);
    pdfViewerObjUrl = URL.createObjectURL(blob);
    $("pv-title").textContent = title || "PDF preview";
    const dl = $("pv-download");
    dl.hidden = false;
    dl.href = pdfViewerObjUrl;
    dl.download = filename || "export.pdf";
    // srcdoc (a prior "rendering…" message) wins over src if left set -- clear it.
    $("pv-frame").removeAttribute("srcdoc");
    $("pv-frame").src = pdfViewerObjUrl;
    ov.style.display = "flex";
  }

  // --- Accumulating PDF preview (page-by-page, abortable) ----------------
  // The intent: a window where PDF pages ACCUMULATE as the export renders, so
  // you can eyeball formatting/content and STOP early if it's wrong. So we
  // render one entity at a time (`/api/pdf?scope=tile&chrome=0`) and rasterise
  // its page(s) onto canvases appended to a scrollable strip -- each entity
  // rendered exactly once, kept in view. A Stop button aborts the loop; the
  // pages already rendered stay up for review + download. While an import is
  // still live, new entities are picked up as they arrive.
  let livePreviewActive = false;
  let livePreviewTimer = null;
  let lpBusy = false;
  let lpSeen = new Set();      // entity keys already rendered
  let lpPageCount = 0;
  let lpOnly = null;           // {kind,id,label} to preview just one item, else null
  const LP_INTERVAL = 800;

  function stopLivePdfPreview() {
    livePreviewActive = false;
    if (livePreviewTimer) { clearTimeout(livePreviewTimer); livePreviewTimer = null; }
  }

  function ensureLivePdfViewer() {
    let ov = $("livepdf-viewer");
    if (ov) return ov;
    ov = document.createElement("div");
    ov.id = "livepdf-viewer";
    ov.className = "pdf-viewer";
    ov.innerHTML =
      '<div class="pv-bar">' +
        '<span class="pv-title">PDF preview</span>' +
        '<span class="lp-status" id="lp-status"></span>' +
        '<span class="spacer"></span>' +
        '<button class="btn crit" id="lp-stop" title="Abort the preview render — pages already shown stay up">' + icon('stop') + ' Stop</button>' +
        '<a class="btn" id="lp-download" href="/api/pdf?scope=all" download="export.pdf" ' +
          'title="Download the full export PDF (cover + TOC)">' + icon('download') + ' Full PDF</a>' +
        '<button class="btn" id="lp-close">✕ Close</button>' +
      '</div><div class="lp-strip" id="lp-strip"></div>';
    document.body.appendChild(ov);
    const close = () => { stopLivePdfPreview(); ov.style.display = "none"; };
    // Stop aborts the render loop but leaves the viewer open for review.
    $("lp-stop").addEventListener("click", () => {
      stopLivePdfPreview();
      lpStatus("stopped · " + lpPageCount + " page" + (lpPageCount === 1 ? "" : "s") + " rendered");
      const s = $("lp-stop"); if (s) s.disabled = true;
    });
    $("lp-close").addEventListener("click", close);
    ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && ov.style.display === "flex") close();
    });
    return ov;
  }

  function lpStatus(text) { const el = $("lp-status"); if (el) el.textContent = text; }

  // Entities in derive order: wiki pages (preorder), then posts, then topics.
  function lpEntityOrder(model) {
    const out = [];
    for (const wiki of (model.wikis || [])) {
      for (const { id, page } of preorderWikiPages(wiki)) {
        out.push({ kind: "page", id, label: "Page · " + (page.title || page.label || id) });
      }
    }
    for (const blog of (model.blogs || [])) {
      for (const pid of (blog.post_ids || [])) {
        const p = blog.posts && blog.posts[pid];
        if (p) out.push({ kind: "post", id: pid, label: "Post · " + (p.title || pid) });
      }
    }
    for (const forum of (model.forums || [])) {
      for (const tid of (forum.topic_ids || [])) {
        const t = forum.topics && forum.topics[tid];
        if (t) out.push({ kind: "topic", id: tid, label: "Thread · " + (t.title || tid) });
      }
    }
    for (const container of (model.rich_content || [])) {
      for (const pid of (container.page_ids || [])) {
        const p = container.pages && container.pages[pid];
        if (p) out.push({ kind: "rich_content_page", id: pid, label: "Highlights · " + (p.title || pid) });
      }
    }
    return out;
  }

  // Render one entity's bare PDF and append its page(s) as canvases. Returns
  // false (leave it unseen, retry next tick) if it isn't derivable yet.
  async function lpRenderTile(entity) {
    let res;
    try {
      res = await fetch("/api/pdf?scope=tile&kind=" + entity.kind +
        "&id=" + encodeURIComponent(entity.id) + "&chrome=0");
    } catch (_) { return false; }
    if (res.status === 503) {
      let status = "pending";
      try { status = (await res.json()).status || "pending"; } catch (_) {}
      if (status === "no_browser") {
        lpStatus("no browser to render — install Edge/Chrome or run `playwright install chromium`");
        stopLivePdfPreview();
      }
      return false;
    }
    if (!res.ok) return true;  // 422 empty etc. -> treat as done, don't spin
    const buf = await res.arrayBuffer();
    if (!livePreviewActive) return true;
    let doc;
    try { doc = await window.pdfjsLib.getDocument({ data: buf, isEvalSupported: false }).promise; }
    catch (_) { return true; }
    const strip = $("lp-strip");
    const targetW = Math.max(280, Math.min(900, strip.clientWidth - 32));
    const dpr = window.devicePixelRatio || 1;
    const cap = document.createElement("div");
    cap.className = "lp-cap"; cap.textContent = entity.label;
    strip.appendChild(cap);
    for (let n = 1; n <= doc.numPages; n++) {
      if (!livePreviewActive) break;
      const page = await doc.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const vp = page.getViewport({ scale: (targetW / base.width) * dpr });
      const canvas = document.createElement("canvas");
      canvas.className = "lp-page";
      canvas.width = vp.width; canvas.height = vp.height;
      canvas.style.width = (vp.width / dpr) + "px";
      canvas.style.height = (vp.height / dpr) + "px";
      await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp, canvas }).promise;
      const nearBottom = strip.scrollHeight - strip.scrollTop - strip.clientHeight < 160;
      strip.appendChild(canvas);
      lpPageCount += 1;
      if (nearBottom) strip.scrollTop = strip.scrollHeight;  // follow, but don't fight manual scroll
    }
    return true;
  }

  // Tick: render every entity not yet shown, one at a time (never two renders
  // at once). While the import is still live, reschedule to pick up new ones.
  async function livePreviewTick() {
    if (!livePreviewActive || lpBusy) return;
    lpBusy = true;
    try {
      const res = await fetch("/api/model");
      if (livePreviewActive && res.ok) {
        // Whole model, or just the one item the reader scope selector names.
        const order = lpOnly ? [lpOnly] : lpEntityOrder(await res.json());
        if (!order.length) lpStatus("waiting for the first page…");
        for (const ent of order) {
          if (!livePreviewActive) break;
          const key = ent.kind + ":" + ent.id;
          if (lpSeen.has(key)) continue;
          const ok = await lpRenderTile(ent);
          if (ok) {
            lpSeen.add(key);
            lpStatus((liveRunning ? "rendering · " : "") + lpPageCount +
              " page" + (lpPageCount === 1 ? "" : "s") + " · " + lpSeen.size + " item" +
              (lpSeen.size === 1 ? "" : "s"));
          }
        }
      } else if (livePreviewActive && res.status === 503) {
        lpStatus("waiting for the first page…");
      }
    } catch (_) { /* transient -- retry next tick */ }
    lpBusy = false;
    if (!livePreviewActive) return;
    // Keep polling while the import is live (new entities may arrive); once the
    // run is done and everything is rendered, finish.
    if (liveRunning) {
      livePreviewTimer = setTimeout(livePreviewTick, LP_INTERVAL);
    } else {
      lpStatus("complete · " + lpPageCount + " page" + (lpPageCount === 1 ? "" : "s"));
      const s = $("lp-stop"); if (s) s.disabled = true;
      stopLivePdfPreview();
    }
  }

  function startLivePdfPreview() {
    stopLivePdfPreview();
    if (!window.pdfjsLib) { notify("The PDF preview library didn't load — reload the page and retry."); return; }
    // Honour the reader scope selector: "Current item" previews just the open
    // page/post/thread; otherwise the whole model accumulates.
    const sel = $("r-pdf-scope");
    const rscope = currentReaderScope();
    lpOnly = (sel && sel.value === "item" && rscope)
      ? { kind: rscope.kind, id: rscope.id, label: rscope.label }
      : null;
    lpBusy = false; lpSeen = new Set(); lpPageCount = 0; livePreviewActive = true;
    const ov = ensureLivePdfViewer();
    $("lp-strip").innerHTML = "";
    const s = $("lp-stop"); if (s) s.disabled = false;
    lpStatus("rendering…");
    ov.style.display = "flex";
    livePreviewTick();
  }

  // Stop button: hides itself after clicking, signals server to stop gracefully.
  const stopBtnEl = document.getElementById("stop-btn");
  if (stopBtnEl) {
    stopBtnEl.addEventListener("click", async () => {
      stopBtnEl.disabled = true;
      stopBtnEl.innerHTML = icon("stop") + " Stopping…";
      try { await fetch("/api/stop", { method: "POST" }); } catch(_) {}
    });
  }
  // Live Browser PDF — loads pages from the original HCL system via Playwright.
  // Uses the auth session from the last import. Fails gracefully if the system
  // is unreachable or no import has been done yet.
  async function exportLivePdf() {
    const btn = $("r-live-pdf");
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "⏳ Rendering…";
    // #4: obey the SAME scope dropdown the archive export uses, so live and
    // export agree on "one item vs the whole" instead of the live path always
    // scoping to whatever page happened to be open. "Current item" -> render
    // just that item's live URL; "Whole archive" -> every live URL.
    const sel = $("r-pdf-scope");
    const rscope = currentReaderScope();
    const wantItem = sel && sel.value === "item" && rscope;
    const scope = wantItem ? "page" : "all";
    const itemId = wantItem ? rscope.id : "";
    let livePdfApiUrl = "/api/live-pdf?scope=" + scope
      + (itemId ? "&page_id=" + encodeURIComponent(itemId) : "");
    // If REAL_MODEL is null (no import done yet), pass the current entity's
    // live URL so the server can render it without a prior archive.
    if (!REAL_MODEL) {
      let fallbackUrl = "";
      const base = $("field-base-url") ? $("field-base-url").value.trim() : "";
      if (realTopicId && base) {
        fallbackUrl = base.replace(/\/$/, "") + "/forums/html/threadTopic?id=" + encodeURIComponent(realTopicId);
      } else if (realPostId && base) {
        // Blog post: alternate_url is in the model; without a model use base URL
        fallbackUrl = base;
      } else if (base) {
        fallbackUrl = base;
      }
      if (fallbackUrl) livePdfApiUrl += "&url=" + encodeURIComponent(fallbackUrl);
    }
    const url = livePdfApiUrl;
    // Open the viewer immediately with a spinner so there's instant feedback --
    // a whole-archive live render fetches + renders every live page and can
    // take a while, and a scoped "current item" render is much quicker.
    showPdfViewerLoading(
      scope === "page" ? "Live PDF · this item" : "Live PDF · whole archive",
      scope === "page"
        ? "Rendering this item from the live system…"
        : "Rendering every page from the live system — this can take a while for a whole archive. Tip: pick “Current item” in the scope selector for a quick render."
    );
    try {
      const res = await fetch(url);
      if (!res.ok) {
        let msg = "Live PDF failed (" + res.status + ").";
        try {
          const j = await res.json();
          if (j.detail) msg = j.detail;
          else if (j.status === "no_model") msg = "Run an import first.";
          else if (j.status === "no_browser") msg = "No browser found. Install Edge/Chrome or run `playwright install chromium`.";
          else if (j.status === "no_live_urls") msg = j.detail || "No live URLs in this model.";
          else if (j.status === "unavailable") msg = "Source system unreachable — it may be offline or you may not have access right now.";
        } catch (_) {}
        closePdfViewer();
        notify(msg);
        return;
      }
      const blob = await res.blob();
      // #5: view it inline (native PDF viewer) rather than a blind download;
      // the overlay carries its own Download button.
      const name = (scope === "page" ? "live-page" : "live-export") + ".pdf";
      showPdfViewer(blob, name, scope === "page" ? "Live PDF · this item" : "Live PDF · whole archive");
    } catch (_) {
      closePdfViewer();
      notify("Live PDF is not reachable. Make sure hcl-serve is running.");
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  }
  $("r-live-pdf").addEventListener("click", exportLivePdf);
  // Drop target: drag any wiki/blog page URL onto the button to render
  // it without having done an archive import first.
  const livePdfBtn = $("r-live-pdf");
  livePdfBtn.addEventListener("dragover", (e) => { e.preventDefault(); livePdfBtn.style.outline = "2px solid var(--accent)"; });
  livePdfBtn.addEventListener("dragleave", () => { livePdfBtn.style.outline = ""; });
  livePdfBtn.addEventListener("drop", async (e) => {
    e.preventDefault();
    livePdfBtn.style.outline = "";
    const droppedUrl = (e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain") || "").trim();
    if (!droppedUrl) return;
    const label = livePdfBtn.textContent;
    livePdfBtn.disabled = true;
    livePdfBtn.textContent = "⏳ Rendering…";
    try {
      const res = await fetch("/api/live-pdf?scope=all&url=" + encodeURIComponent(droppedUrl),
                               {headers: {"Accept": "application/pdf, application/json"}});
      if (!res.ok) {
        let msg = "Live PDF failed (" + res.status + ").";
        try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (_) {}
        notify(msg); return;
      }
      const blob = await res.blob();
      showPdfViewer(blob, "live-export.pdf", "Live PDF · dropped URL");
    } catch (_) { notify("Live PDF failed — check that hcl-serve is running."); }
    finally { livePdfBtn.disabled = false; livePdfBtn.textContent = label; }
  });
  $("r-search-input").addEventListener("input", (e) => {
    const q = e.target.value;
    if (q.trim().length < 2) {
      reopenCurrentReal();
      return;
    }
    renderResultsReal(q);
  });

  $("restart").addEventListener("click", () => { beginRun(lastStartBody); });

  // ---------- live driver: consumes the real SSE event stream from
  // hcl-serve --demo (Front end (static/console.html)) ----------

  function setBanner(newMode, text) {
    mode = newMode;
    const el = $("live-banner");
    if (el) { el.className = "live-banner " + newMode; }
    const t = $("live-banner-text");
    if (t) t.textContent = text;
    const badge = $("mode-badge");
    if (badge) {
      const prefix = (lastStartBody && lastStartBody.demo) ? "Demo" : "Import";
      badge.textContent = newMode === "live" ? "● " + prefix + " — live" : "◆ " + prefix + " — connecting…";
    }
  }

  // Shown when the server-backed pipeline is unreachable -- the POST to
  // `/api/start` failed, or the SSE stream never delivered a single event.
  // A user-facing error, never a client-side simulated substitute. Reuses
  // the live-banner's warn-styled "offline" CSS class purely for the visual
  // treatment; this is deliberately NOT a data-source `mode` transition
  // (`mode` stays "connecting"/"live" -- there is no simulated data behind
  // this banner, just a status message).
  function showUnreachableBanner(text) {
    const el = $("live-banner");
    if (el) { el.className = "live-banner offline"; }
    const t = $("live-banner-text");
    if (t) t.textContent = text;
    const badge = $("mode-badge");
    if (badge) badge.textContent = "◆ " + ((lastStartBody && lastStartBody.demo) ? "Demo" : "Import") + " — unreachable";
  }

  //: The deployment this run is reading, as a host. What every request in
  //: the log is expected to have gone to.
  function deploymentHost() {
    const url =
      (lastStartBody && lastStartBody.base_url) ||
      identifiedBaseUrl ||
      ($("field-base-url") ? $("field-base-url").value : "");
    try {
      return new URL(url).host;
    } catch (_) {
      return "";
    }
  }

  function shortLabel(url) {
    // Only a real address is parsed as one. `new URL(value, location.href)`
    // resolves anything else against the console's own origin, so an id
    // handed to this came back wearing `127.0.0.1` as its host -- and the
    // host is shown precisely so that a request answered by the wrong
    // machine is noticeable. Spending that signal on something that never
    // happened is worse than not having it.
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(String(url || ""))) return String(url);
    try {
      const u = new URL(url);
      const parts = u.pathname.split("/").filter(Boolean);
      // Keep enough path context to identify the page behind a generic
      // `feed` endpoint; showing only the final segment made every comment
      // request look identical and hid where pagination was stuck.
      const tail = parts.slice(-4).map((part) => decodeURIComponent(part)).join("/");
      const path = (tail || u.pathname) + (u.search ? u.search : "");
      // The host, but only when it is not the one this run is against.
      // Built from the path alone, a request answered by a machine nobody
      // meant to ask reads exactly like a correct one -- and there are
      // hundreds of them, all plausible. Repeating the expected host on
      // every line would bury the one that differs among the ones that do
      // not, which is the same failure with more ink.
      const expectedHost = deploymentHost();
      return u.host && u.host !== expectedHost ? u.host + " \u00b7 " + path : path;
    } catch (_) {
      return String(url);
    }
  }

  function resetSharedState() {
    clearInterval(clockTimer); clockTimer = null; closeDrawer();
    disc = ok = reused = retry = fail = blobs = peak = stopped = 0;
    if ($("kpi-stopped")) $("kpi-stopped").hidden = true;
    if ($("k-stopped")) $("k-stopped").textContent = "0";
    authorIdentities = []; authorKept = 0; authorTotal = 0;
    fetchStarted.clear(); latencies = [];
    if ($("kpi-latency")) $("kpi-latency").hidden = true;
    if ($("k-latency")) $("k-latency").textContent = "—";
    items = []; fetchTimes = []; spark = []; warnings = []; notes = []; failures = []; prunedCount = 0; kindCounts = {}; nodeEls.clear(); byId.clear(); keyByRawId.clear(); assetUrls.clear();
    if ($("type-counts")) { $("type-counts").hidden = true; $("type-counts").innerHTML = ""; }
    if ($("v-breakdown")) $("v-breakdown").innerHTML = "";
    if ($("vg-pruned")) $("vg-pruned").hidden = true;
    if ($("kpi-pruned")) $("kpi-pruned").hidden = true;
    if ($("k-pruned")) $("k-pruned").textContent = "0";
    if ($("author-picker")) { $("author-picker").hidden = true; $("author-picker").innerHTML = ""; }
    livePendingPageId = null; liveWikiGroups.clear(); livePageDepth.clear();
    liveCommunityGroups.clear(); liveCommunityTail.clear(); liveDepthOffset = 0;
    const failKpi = $("k-fail") && $("k-fail").closest(".kpi");
    if (failKpi) { failKpi.classList.remove("openable"); failKpi.removeAttribute("data-openable"); failKpi.removeAttribute("role"); failKpi.removeAttribute("tabindex"); failKpi.removeAttribute("title"); }
    $("tree").innerHTML = ""; $("log").innerHTML = ""; resetLog();
    setBeamRunning(false); stopFlow();
    $("ovfill").classList.remove("done", "ended-bad");
    $("statuspill").classList.remove("settle");
    const ovWrap = document.querySelector(".overall"); if (ovWrap) ovWrap.classList.remove("armed");
    $("alerts").innerHTML = '<div class="alerts-empty" id="alerts-empty"><span class="ok-dot"></span> No data-loss warnings. Watching for truncation &amp; orphans…</div>';
    $("verdict").className = "verdict";
    ["k-disc", "k-ok", "k-reused", "k-retry", "k-fail", "k-tp"].forEach((i) => {
      if ($(i)) $(i).textContent = "0";
    });
    if ($("kpi-reused")) $("kpi-reused").hidden = true;
    $("ovfill").style.transform = "scaleX(0)"; $("ovpct").textContent = "0%"; $("ovdone").textContent = "0"; $("ovtotal").textContent = "0";
    $("spark-now").textContent = "0"; $("spark-peak").textContent = "0"; $("spark-line").setAttribute("d", ""); $("spark-area").setAttribute("d", "");
    $("eta").textContent = "—"; $("elapsed").textContent = "00:00";
    updateRunStatus(null);
  }

  function liveUpdateOverall() {
    // Everything that reached a conclusion, fetched or reused -- otherwise a
    // finished update reads as 17% done.
    const done = ok + reused + fail + stopped, total = Math.max(disc, done, 1);
    const pct = Math.round((done / total) * 100);
    $("ovfill").style.transform = "scaleX(" + (pct / 100) + ")"; $("ovpct").textContent = pct + "%";
    // The sheen is clipped to the filled portion, so it needs the width the
    // fill only expresses as a transform.
    const bar = $("ovfill").parentNode; if (bar) bar.style.setProperty("--pct", pct + "%");
    $("ovdone").textContent = done; $("ovtotal").textContent = Math.max(disc, done);
    updateRunStatus({ state: "running", done, total: Math.max(disc, done) });
  }

  // #21: which archive/target this run writes to, shown in the ingest
  // topbar -- a demo run's own `base_url` is the in-process fake server
  // ("https://fake"), which would read as a real deployment if shown
  // verbatim, so a demo run is always labelled "Demo (fake data)" instead
  // (matching the reader's `setReaderSource`); a real run shows its actual
  // `base_url`. Refines the label `setRoutePath` already set in
  // `startLive()` with the confirmed run id once `run_started` lands.
  function liveRunStarted(evt) {
    const isDemo = !!(lastStartBody && lastStartBody.demo);
    const target = isDemo ? "Demo (fake data)" : (evt.base_url || describeRunTarget(lastStartBody));
    setRoutePath(
      '<span>' + escapeHtml(target) + '</span><span class="arrow" aria-hidden="true">→</span>' +
      '<b>' + escapeHtml(evt.run_id) + '</b><span aria-hidden="true">·</span><span>' + (isDemo ? "demo pipeline" : "live import") + '</span>'
    );
    setBeamRunning(true);
    pulseBeam();
  }

  function liveDiscovered(evt) {
    disc += 1; setNum($("k-disc"), disc);
    liveUpdateOverall();
    // The hierarchy tree is built from `page_derived` events (see
    // livePageDerived) so it shows real wiki -> page structure with titles
    // and nesting. Feeds and assets stay in the activity log, not the tree.
  }

  //: How long the source system takes to answer, kept per in-flight URL and
  //: turned into a median once it does.
  const fetchStarted = new Map();
  let latencies = [];
  //: Long enough that the rate has resolution -- a six-second window gave
  //: readings only in steps of ten per minute.
  const THROUGHPUT_WINDOW_MS = 30000;

  // A request the run gave up on because it was asked to stop. Counted so
  // that discovered = saved + reused + failed + not attempted; without it the
  // totals never balanced and could not show a real loss.
  function liveStopped(evt) {
    stopped += 1;
    setNum($("k-stopped"), stopped);
    const tile = $("kpi-stopped");
    if (tile) tile.hidden = false;
    fetchStarted.delete(evt.url);
    logRow("STOP", "warn", shortLabel(evt.url), (evt.kind || "request") + " · not attempted");
  }

  function liveFetching(evt) {
    fetchStarted.set(evt.url, Date.now());
    const it = byId.get(evt.url); if (!it) return;
    it.state = "active";
    const node = nodeEls.get(it.key || it.id); if (node) node.dataset.st = "active";
    if (openId === (it.key || it.id)) renderDrawer(it);
    pulseBeam();
  }

  function liveFetched(evt) {
    // Archive-served responses are accounted for by the Reused counter, but
    // are not network activity and should not make an unchanged update look
    // like it is reading every cached body or attachment.
    if (!evt.from_cache) {
      logRow(
        evt.status != null ? String(evt.status) : "OK",
        evt.status != null && evt.status >= 400 ? "warn" : "ok",
        shortLabel(evt.url),
        evt.kind || "fetch",
        // Collapse key: same kind of fetch, counted in one row under load.
        evt.kind || "fetch"
      );
    }
    markFlow();
    const success = evt.status == null || evt.status < 400;
    if (success && evt.from_cache) {
      // Served from the archive: an update deciding it already had this is
      // the good news, and it is not a fetch.
      reused += 1; setNum($("k-reused"), reused);
      // Shown only once there is something to show -- a fresh capture reuses
      // nothing, and a permanent zero is noise.
      if ($("kpi-reused")) $("kpi-reused").hidden = false;
    } else if (success) {
      ok += 1; setNum($("k-ok"), ok);
      // Tally by type as items are fetched (topics/replies/comments/images/…).
      // A raw `topic` fetch is a topic endpoint/feed request, not necessarily
      // one forum thread. ForumTopicDerived is the authoritative thread event.
      // The type strip counts ENTITIES, and every entity is counted in
      // exactly one place: the derive event that announces it. This used to
      // tally fetch kinds as well, which meant the strip mixed two different
      // quantities under one set of labels -- "comments" was the number of
      // comment *requests* (16) while the archive held 120, "posts" was the
      // number of post endpoint requests (1) against 8 posts, and
      // `rich_content` and `reply` were counted twice over because their
      // derive handlers counted them too.
      //
      // Assets are the one type with no derive event of their own, so they
      // are counted here -- deduplicated by URL, because the same asset
      // fetched from two pages is one asset in the archive, and the verdict
      // counts blobs.
      if (evt.kind === "asset" || evt.kind === "attachments") {
        if (evt.url && !assetUrls.has(evt.url)) {
          assetUrls.add(evt.url);
          kindCounts.asset = assetUrls.size;
          renderTypeCounts(kindCounts, "type-counts");
        }
      }
      // A rate, not a count times ten. Counting fetches in a six-second
      // window and multiplying by 10 could only ever produce multiples of 10,
      // which is why a real deployment read "always 60 or 70, never in
      // between". A longer window divided by its own elapsed time gives a
      // figure that can move.
      const now = Date.now();
      fetchTimes.push(now);
      fetchTimes = fetchTimes.filter((t) => t >= now - THROUGHPUT_WINDOW_MS);
      const span = Math.max(1000, now - Math.min(fetchTimes[0] || now, now));
      const perMin = Math.round((fetchTimes.length / span) * 60000);
      setNum($("k-tp"), perMin); $("spark-now").textContent = perMin;

      // How long the deployment itself took. Without it, "my delay is being
      // ignored" and "the source system is slow" look identical from here --
      // and the second is usually the answer.
      const started = fetchStarted.get(evt.url);
      if (started) {
        fetchStarted.delete(evt.url);
        latencies.push(now - started);
        if (latencies.length > 25) latencies.shift();
        const sorted = [...latencies].sort((a, b) => a - b);
        const median = sorted[Math.floor(sorted.length / 2)];
        const tile = $("kpi-latency");
        if (tile) tile.hidden = false;
        if ($("k-latency")) {
          $("k-latency").textContent =
            median >= 1000 ? (median / 1000).toFixed(1) + "s" : Math.round(median) + "ms";
        }
      }
    }
    const it = byId.get(evt.url);
    if (it && success) {
      it.state = "ok";
      if (it.kind === "page") livePendingPageId = it.id;
      const node = nodeEls.get(it.key || it.id); if (node) { node.dataset.st = "ok"; settleNode(node, "settle-ok"); }
      if (openId === (it.key || it.id)) renderDrawer(it);
    }
    // A >=400 status is followed by its own `failed` event (the
    // crawler always emits both -- see crawler/crawl.py's
    // `get_content`), which marks the node failed; nothing to do here
    // for that case.
    liveUpdateOverall();
  }

  function liveRetried(evt) {
    retry += 1; setNum($("k-retry"), retry);
    logRow("RETRY", "warn", shortLabel(evt.url), "attempt " + evt.attempt);
  }

  function liveFailed(evt) {
    fail += 1; setNum($("k-fail"), fail);
    logRow("FAIL", "fail", shortLabel(evt.url), evt.kind + " · " + evt.error);
    // Keep the full failure so the "Failed" KPI can be opened and reviewed --
    // the log row scrolls away, but the manifest (and this list) do not.
    failures.push({ url: evt.url, kind: evt.kind || "", error: evt.error || "" });
    markFailKpiOpenable();
    const it = byId.get(evt.url);
    if (it) {
      it.state = "failed";
      const node = nodeEls.get(it.key || it.id); if (node) { node.dataset.st = "failed"; settleNode(node, "settle-bad"); }
      if (openId === (it.key || it.id)) renderDrawer(it);
    }
    liveUpdateOverall();
  }

  // The "Failed" KPI becomes a button once there's something to show, so the
  // count the user sees ("18 failed") is inspectable, not a dead number. The
  // click/keydown handlers are bound once at init (see below); this only
  // toggles the affordance so listeners never stack across runs.
  function markFailKpiOpenable() {
    const kpi = $("k-fail") && $("k-fail").closest(".kpi");
    if (!kpi || kpi.dataset.openable === "1") return;
    kpi.dataset.openable = "1";
    kpi.classList.add("openable");
    kpi.setAttribute("role", "button");
    kpi.tabIndex = 0;
    kpi.title = "Click to see which fetches failed";
  }

  // Overlay listing every recorded failure (url · kind · error). Reuses the
  // PDF viewer's scrim look. Each row notes it's a manifest failure row, not a
  // silent gap, and retry-eligible on a resumed run.
  function showFailures() {
    if (!failures.length) return;
    let ov = $("fail-viewer");
    if (!ov) {
      ov = document.createElement("div");
      ov.id = "fail-viewer";
      ov.className = "pdf-viewer";
      ov.innerHTML =
        '<div class="pv-bar">' +
        '<span class="pv-title" id="fv-title"></span>' +
        '<span class="spacer"></span>' +
        '<button class="btn" id="fv-close">✕ Close</button>' +
        '</div><div class="fv-list" id="fv-list"></div>';
      document.body.appendChild(ov);
      const close = () => { ov.style.display = "none"; };
      $("fv-close").addEventListener("click", close);
      ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && ov.style.display === "flex") close();
      });
    }
    $("fv-title").textContent = failures.length + " failed fetch" + (failures.length === 1 ? "" : "es") + " — recorded in the manifest, retry-eligible on a resumed run";
    $("fv-list").innerHTML = failures.map((f) =>
      '<div class="fv-row"><div class="fv-url">' + escapeHtml(f.url) + '</div>' +
      '<div class="fv-meta"><span class="rloc">' + escapeHtml(f.kind || "fetch") + '</span>' +
      '<span class="fv-err">' + escapeHtml(f.error || "failed") + '</span></div></div>'
    ).join("");
    ov.style.display = "flex";
  }

  // Which community a ticked component belongs to, or null when this run
  // covers one community -- in which case a heading above the wiki would only
  // repeat what the title bar already says.
  function liveCommunityOf(componentKey) {
    if (liveCommunityCount <= 1) return null;
    return liveCommunityByComponent.get(componentKey) || null;
  }

  // The heading a container goes under, created the first time one of its
  // containers appears. Returns the community's uuid so the caller can tag
  // the container and its children with it.
  function ensureCommunityGroup(componentKey) {
    const community = liveCommunityOf(componentKey);
    if (!community) return null;
    if (!liveCommunityGroups.has(community.uuid)) {
      liveCommunityGroups.add(community.uuid);
      const node = {
        id: "community:" + community.uuid, kind: "communitygroup", kindLabel: "COMMUNITY",
        title: community.title || community.uuid, depth: 0, comments: null,
        state: "ok", isGroup: true, community: community.uuid,
      };
      items.push(node); byId.set(node.id, node); addTreeNode(node);
      liveDepthOffset = 1;
    }
    return community.uuid;
  }

  function livePageDerived(evt) {
    // A group header per wiki (once), labelled by the real wiki title, so
    // the tree reads as wiki -> pages rather than a flat list -- and, when the
    // run covers several communities, under the heading of the one this wiki
    // was ticked under.
    const community = ensureCommunityGroup("wiki:" + evt.wiki);
    if (!liveWikiGroups.has(evt.wiki)) {
      liveWikiGroups.add(evt.wiki);
      const w = {
        id: "wiki:" + evt.wiki, kind: "wikigroup", kindLabel: "WIKI",
        title: evt.wiki_title || evt.wiki, depth: 0, comments: null, state: "ok",
        isGroup: true, community,
      };
      items.push(w); byId.set(w.id, w); addTreeNode(w);
    }
    // Nest each page one level below its parent (pages arrive in the
    // crawler's depth-first order, so appending with this indent gives the
    // real hierarchy).
    const depth = (evt.parent && livePageDepth.has(evt.parent)) ? livePageDepth.get(evt.parent) + 1 : 1;
    livePageDepth.set(evt.page_id, depth);
    const pageKey = _liveKey("page", evt.page_id);
    if (byId.has(pageKey)) return;
    const it = {
      id: evt.page_id, key: pageKey, kind: "page", kindLabel: depth <= 1 ? "PAGE" : "CHILD",
      title: evt.title, depth, comments: evt.comment_count, state: "ok", wiki: evt.wiki, url: null,
      community,
      author: evt.author, modified: evt.modified,
      pageCounts: { comments: evt.comment_count, versions: evt.version_count, attachments: evt.attachment_count },
    };
    kindCounts.page = (kindCounts.page || 0) + 1;
    kindCounts.comments = (kindCounts.comments || 0) + (evt.comment_count || 0);
    renderTypeCounts(kindCounts, "type-counts");
    items.push(it); byId.set(it.key || it.id, it); addTreeNode(it);
    livePendingPageId = null;
    // The first imported page means /api/model now derives a partial
    // snapshot -- open the reader on it.
    setReaderEnabled(true);
  }

  // Blogs/Forums live tree nodes: each app's
  // derived event streams a group header (once, per blog/forum) plus a
  // child node per post/topic, exactly the way `livePageDerived` builds
  // the wiki tree. The reader (post-derive) is the must-have; these keep
  // the live tree honest when the events do arrive.
  function _liveGroupNode(groupId, kindLabel, title) {
    const community = ensureCommunityGroup(groupId);
    if (liveWikiGroups.has(groupId)) return community;
    liveWikiGroups.add(groupId);
    const g = { id: groupId, kind: "group", kindLabel, title, depth: 0, comments: null, state: "ok", isGroup: true, community };
    items.push(g); byId.set(g.id, g); addTreeNode(g);
    return community;   // so the caller files this container's children with it
  }

  function liveBlogPostDerived(evt) {
    const community = _liveGroupNode("blog:" + evt.blog, "BLOG", evt.blog_title || evt.blog);
    const postKey = _liveKey("post", evt.post_id);
    if (byId.has(postKey)) return;
    kindCounts.post = (kindCounts.post || 0) + 1;
    kindCounts.comments = (kindCounts.comments || 0) + (evt.comment_count || 0);
    renderTypeCounts(kindCounts, "type-counts");
    const it = { id: evt.post_id, key: postKey, kind: "post", kindLabel: "POST", title: evt.title, author: evt.author, published: evt.published, depth: 1, comments: evt.comment_count, state: "ok", url: null, community };
    items.push(it); byId.set(it.key || it.id, it); addTreeNode(it);
    setReaderEnabled(true);
  }

  function liveForumTopicDerived(evt) {
    const community = _liveGroupNode("forum:" + evt.forum, "FORUM", evt.forum_title || evt.forum);
    const topicKey = _liveKey("topic", evt.topic_id);
    if (byId.has(topicKey)) return;
    kindCounts.topic = (kindCounts.topic || 0) + 1;
    kindCounts.reply = (kindCounts.reply || 0) + (evt.reply_count || 0);
    renderTypeCounts(kindCounts, "type-counts");
    const it = { id: evt.topic_id, key: topicKey, kind: "topic", kindLabel: "TOPIC", title: evt.title, author: evt.author, published: evt.published, depth: 1, comments: evt.reply_count, state: "ok", url: null, flags: evt.flags || [], community };
    items.push(it); byId.set(it.key || it.id, it); addTreeNode(it);
    setReaderEnabled(true);
  }

  function liveRichContentDerived(evt) {
    const community = _liveGroupNode("rich_content:" + evt.community_uuid, "HIGHLIGHTS", "Highlights");
    const rcKey = _liveKey("rich_content", evt.resource_id);
    if (byId.has(rcKey)) return;
    const it = {
      id: evt.resource_id,
      key: rcKey,
      kind: "rich_content",
      kindLabel: "HIGHLIGHT",
      title: evt.title || evt.resource_id,
      depth: 1,
      comments: null,
      state: "ok",
      url: null,
      community_uuid: evt.community_uuid,
      community,
    };
    items.push(it); byId.set(it.key || it.id, it); addTreeNode(it);
    kindCounts.rich_content = (kindCounts.rich_content || 0) + 1;
    renderTypeCounts(kindCounts, "type-counts");
    setReaderEnabled(true);
  }

  // Files never had a case in `handleLiveEvent`, so a community's Files
  // component was crawled, archived and derived -- it shows up in the
  // verdict breakdown, which reads the derived model -- while the live
  // console showed nothing at all: no group, no nodes, no count. Every other
  // app has had one of these since it landed; this is the missing fifth.
  function liveCommunityFileDerived(evt) {
    const community = _liveGroupNode("files:" + (evt.library || "community"), "FILES", evt.library_title || "Files");
    const fileKey = _liveKey("file", evt.file_id);
    if (byId.has(fileKey)) return;
    const it = {
      id: evt.file_id,
      key: fileKey,
      kind: "file",
      kindLabel: "FILE",
      title: evt.name || evt.file_id,
      author: evt.author,
      depth: 1,
      comments: null,
      // `bytes_captured: null` is the difference between having a file and
      // merely knowing of one, and 0 is a real empty file -- so the state
      // says "archived" only when the bytes actually arrived.
      state: evt.bytes_captured === null ? "warn" : "ok",
      url: null,
      size: evt.size,
      versionLabel: evt.version_label,
      bytesCaptured: evt.bytes_captured,
      community,
    };
    items.push(it); byId.set(it.key || it.id, it); addTreeNode(it);
    kindCounts.file = (kindCounts.file || 0) + 1;
    renderTypeCounts(kindCounts, "type-counts");
    setReaderEnabled(true);
  }

  // "note" is a third tier below crit and warn: a difference worth recording
  // that is not a defect. It shows in the alerts panel, and it does NOT count
  // as a warning -- nobody needs to review a rich content area an owner never
  // typed in, and a verdict that demands review for one teaches people to
  // ignore the verdict.
  const WARNING_SEVERITY = {
    truncation: "crit", orphan: "warn", external_asset_skipped: "warn",
    pagination_cap: "crit", placed_not_written: "note",
  };
  const WARNING_TITLE = {
    truncation: "Possible silent truncation", orphan: "Orphaned page",
    external_asset_skipped: "External asset skipped", pagination_cap: "Pagination safety cap hit",
    placed_not_written: "Placed, never written in",
  };

  function liveWarning(evt) {
    const sev = WARNING_SEVERITY[evt.kind] || "warn";
    const forKey = evt.ref ? keyByRawId.get(evt.ref) : null;
    const forId = forKey ? evt.ref : null;
    const dHtml = escapeHtml(evt.detail) + (evt.ref ? ' — <code>' + escapeHtml(evt.ref) + '</code>' : '');
    raiseAlert(sev, WARNING_TITLE[evt.kind] || evt.kind, dHtml, forId);
    if (forKey && openId === forKey) renderDrawer(byId.get(forKey));
  }

  // How an author-filtered forum capture chose the topics it read. Without
  // this the capture is inexplicable from here: a community holding ten
  // thousand topics shows a few hundred and nothing on screen says that
  // Search picked them, out of how many hits -- or, the case that matters,
  // that the answer was cut short and there may be more.
  function liveTopicSelection(evt) {
    const clean = evt.used && evt.complete === true;
    raiseAlert(
      clean ? "note" : "warn",
      evt.used
        ? "Search chose " + (evt.selected || 0) + " thread(s) to read"
        : "Reading the forums in full",
      escapeHtml(evt.detail || "") + (evt.ref ? ' — <code>' + escapeHtml(evt.ref) + "</code>" : ""),
      null
    );
  }

  function liveRunComplete(evt) {
    // Each COMPONENT's crawl ends with one of these, and a run can be made of
    // five. Taking the first as the end of the run announced "Complete",
    // re-enabled Start, and left the ingest running behind it -- which is
    // what made a Stop look ignored: the screen said finished while the work
    // went on. The server marks the one that means the run is over.
    if (evt && evt.final === false) return;
    liveRunning = false;
    setSelectRunning(false);
    clearInterval(clockTimer); clockTimer = null; tickClock(); $("eta").textContent = "done";
    const report = evt.report || {};
    const warnCount = warnings.length;
    const attention = warnCount > 0 || fail > 0;
    const pill = $("statuspill"); pill.className = "status-pill " + (attention ? "attention" : "done");
    $("statustext").textContent = attention ? "Complete · needs attention" : "Complete · clean";
    const v = $("verdict");
    $("verdict-badge").textContent = attention ? "REVIEW" : "PASS";
    $("verdict-title").textContent = attention
      ? "Archived with " + warnCount + " warning" + (warnCount === 1 ? "" : "s") + " to review"
      : "Complete archive — nothing flagged";
    const totalBlobs = Object.values(report.counts || {}).reduce((a, b) => a + b, 0);
    const blobs_ = totalBlobs || (ok + reused);

    // The one authored moment in the console (docs/design/gui-motion.md A5):
    // hours of work resolving into pass/attention, in four beats over ~1.1s.
    // A run that ended with failures gets the same shape without the settle
    // overshoot and with the bar in --crit -- finality, not reward.
    const failed = fail > 0;
    setBeamRunning(false); stopFlow();
    $("ovfill").classList.add(failed ? "ended-bad" : "done");
    ["v-ok", "v-fail", "v-warn", "v-blobs"].forEach((id) => { if ($(id)) $(id).textContent = "0"; });
    const beat = (ms, fn) => { if (reduce) { fn(); return; } setTimeout(fn, ms); };
    if (!failed) beat(150, () => pill.classList.add("settle"));
    beat(300, () => v.classList.add("show", attention ? "attention" : "pass"));
    beat(400, () => {
      countUp($("v-ok"), ok); countUp($("v-fail"), fail);
      countUp($("v-warn"), warnCount); countUp($("v-blobs"), blobs_);
      if (prunedCount > 0) countUp($("v-pruned"), prunedCount);
    });
    // ~1.15s: the sequence is over and the panel is just a panel again.
    beat(1150, () => v.classList.add("settled"));
    // Pruned-by-filter count (author filter): make it explicit so the raw
    // "Fetched" number isn't mistaken for what was kept.
    if (prunedCount > 0) {
      $("vg-pruned").hidden = false;
      $("v-pruned").textContent = "0";
    }
    // Per-type breakdown of what was actually KEPT (from the derived, filtered
    // model) -- the honest counts of pages/posts/topics/replies/comments/images.
    fetch("/api/model").then((r) => r.ok ? r.json() : null).then((model) => {
      if (model) {
        renderTypeCounts(modelTypeCounts(model), "v-breakdown");
        renderCommunityBreakdown(model, "v-communities");
      }
    }).catch(() => {});
    $("foot-note").textContent = "Run finished · " + $("elapsed").textContent + " elapsed · click any node to inspect";
    liveUpdateOverall();
    updateRunStatus({ state: attention ? "review" : "done", done: ok + fail, total: Math.max(disc, ok + fail) });
    if (liveSource) { try { liveSource.close(); } catch (_) {} liveSource = null; }
    // Reset the stop button so it's ready for the next run.
    const stopBtnEl = document.getElementById("stop-btn");
    if (stopBtnEl) { stopBtnEl.disabled = false; stopBtnEl.innerHTML = icon("stop") + " Stop"; stopBtnEl.style.display = "none"; }
    // Update the Restart button label to match whether this was a demo or real run.
    const restartBtn = document.getElementById("restart");
    if (restartBtn) restartBtn.innerHTML = icon("restart") + ((lastStartBody && lastStartBody.demo) ? " Restart demo" : " Start again");
    // If a live PDF preview is open, render the finished archive one last time
    // (liveRunning is now false, so this tick paints the complete PDF and stops).
    if (livePreviewActive) {
      if (livePreviewTimer) { clearTimeout(livePreviewTimer); livePreviewTimer = null; }
      livePreviewTick();
    }
    refreshCurrentArchive();  // reflect the now-complete state
  }

  function handleLiveEvent(evt) {
    if (!evt || typeof evt.type !== "string") return;
    switch (evt.type) {
      case "run_started": return liveRunStarted(evt);
      case "discovered": return liveDiscovered(evt);
      case "fetching": return liveFetching(evt);
      case "fetched": return liveFetched(evt);
      case "retried": return liveRetried(evt);
      case "failed": return liveFailed(evt);
      case "stopped": return liveStopped(evt);
      case "page_derived": return livePageDerived(evt);
      case "blog_post_derived": return liveBlogPostDerived(evt);
      case "forum_topic_derived": return liveForumTopicDerived(evt);
      case "rich_content_derived": return liveRichContentDerived(evt);
      case "community_file_derived": return liveCommunityFileDerived(evt);
      case "warning": return liveWarning(evt);
      case "pruned": return livePruned(evt);
      case "author_filter_summary": return liveAuthorSummary(evt);
      case "topic_selection": return liveTopicSelection(evt);
      case "run_complete":
        // A real run has just told the server which deployment it is
        // talking to, so the identity is resolvable now in a way it
        // was not at boot.
        loadShellIdentity();
        return liveRunComplete(evt);
      default: return;
    }
  }

  let prunedCount = 0;
  // Why an entity was pruned -- the author filter (the common case) or,
  // for a search-driven ingest restricted to one identified forum, a hit
  // from a DIFFERENT forum the person also posted in (`reason` on the
  // `Pruned` event, crawler/events.py; default "author-filter" for
  // backward-compat with events that don't set it).
  const PRUNE_REASON_TEXT = {
    "author-filter": "not authored — skipped",
    "wrong-forum": "different forum — outside this import's scope",
  };
  // An entity the crawler pruned (see PRUNE_REASON_TEXT). When filtering by
  // author, the user doesn't want pruned items cluttering the hierarchy at
  // all -- so REMOVE the node entirely (it was shown live during Pass 1,
  // before authorship/scope was known). A running count + log keeps it
  // honest (seen, not kept -- never a truly silent drop).
  function livePruned(evt) {
    prunedCount += 1;
    // Live, not only in the verdict. A filtered run must read every thread
    // to find out whether you are in it, so it fetches steadily while the
    // kept tree stands still -- which reads as a stall unless something
    // says what the reading is for.
    if ($("kpi-pruned")) $("kpi-pruned").hidden = false;
    if ($("k-pruned")) $("k-pruned").textContent = String(prunedCount);
    const prunedKey = keyByRawId.get(evt.id) || evt.id;
    const node = nodeEls.get(prunedKey);
    if (node) { node.remove(); nodeEls.delete(prunedKey); keyByRawId.delete(evt.id); }
    const idx = items.findIndex((i) => i.id === evt.id);
    if (idx >= 0) items.splice(idx, 1);
    $("tree-count") && ($("tree-count").textContent = nodeEls.size);
    const why = PRUNE_REASON_TEXT[evt.reason] || PRUNE_REASON_TEXT["author-filter"];
    // An id, formatted as one. It names an item that was read and not kept,
    // not somewhere a request went.
    logRow("PRUNE", "warn", String(evt.id || "item"), (evt.kind || "item") + " · " + why);
  }

  // End-of-run author-filter summary: how many were kept, and (crucially when
  // NONE matched) the actual author identities present, so the user can copy
  // the exact stored form into the filter.
  //: Every identity this run has seen, across components. Wikis, blogs and
  //: forums each end with their own summary. Re-rendering from scratch on
  //: each one leaves a community import showing only the last component's
  //: authors, which is a fraction of the people in it.
  //:
  //: A chip is not decoration: clicking one re-imports that person's content
  //: with the exact stored form of their name, which is a filter nobody can
  //: type from memory. An identity missing from the list is unreachable.
  let authorIdentities = [];
  let authorKept = 0;
  let authorTotal = 0;

  function liveAuthorSummary(evt) {
    const author = (evt.author || "").trim();
    authorKept += evt.kept || 0;
    authorTotal += evt.total || 0;
    (evt.identities || []).forEach((identity) => {
      if (!authorIdentities.includes(identity)) authorIdentities.push(identity);
    });
    renderAuthorPicker(author, authorIdentities, authorKept, authorTotal);
    const kept = authorKept, total = authorTotal;
    if (author && kept === 0) {
      raiseAlert("crit",
        "Author filter “" + escapeHtml(author) + "” matched 0 of " + total + " items",
        "The value didn't match the stored author name or user id. Pick your exact name " +
          "from the authors below — one click re-imports only their content.",
        null);
    }
  }

  // The filter value carried by an identity chip: the name if present (the
  // "regular form" the user just needs verbatim), else the bare user id.
  function chipValue(label) {
    const m = String(label).match(/^(.*?)\s*\(uid:\s*([^)]+)\)\s*$/);
    if (m) return (m[1].trim() || m[2].trim());
    return String(label).trim();
  }

  // Show the distinct authors this import saw as clickable chips. Clicking one
  // re-imports the same scope filtered to that author, using the exact stored
  // form -- so the user never has to guess the name format.
  //: Above this many, scanning stops working and a filter earns its place.
  const AUTHOR_FILTER_THRESHOLD = 12;

  //: Whether the list is expanded. Remembered, because someone who does not
  //: care about author filtering should not have to close it every run.
  function authorPickerOpen(force) {
    if (force) return true;
    try { return localStorage.getItem("hcl-export-authors-open") === "1"; } catch (_) { return false; }
  }

  function authorCountLabel(identities, total) {
    const n = identities.length;
    return n + (n === 1 ? " author" : " authors") + (total ? " · " + total + " items" : "");
  }

  // Show the distinct authors this import saw as clickable chips. Clicking one
  // re-imports the same scope filtered to that author, using the exact stored
  // form -- so the user never has to guess the name format.
  //
  // Collapsed by default: most people are not here to filter by author, and a
  // list that can now hold two hundred names is in the way if you do not want
  // it. It opens itself when a filter matched nothing, because that is the one
  // case where the list is the fix rather than a curiosity.
  function renderAuthorPicker(currentAuthor, identities, kept, total) {
    const box = $("author-picker");
    if (!box) return;
    if (!identities.length) { box.hidden = true; box.innerHTML = ""; return; }
    const matchedNothing = !!currentAuthor && kept === 0;
    const head = currentAuthor
      ? "Kept " + kept + " of " + total + " for \u201c" + escapeHtml(currentAuthor) +
        "\u201d. Filter to a different author:"
      : "Authors in this import \u2014 click one to re-import only their content:";

    box.innerHTML = "";
    const panel = document.createElement("details");
    panel.className = "ap-panel";
    panel.open = authorPickerOpen(matchedNothing);
    const summary = document.createElement("summary");
    summary.className = "ap-summary";
    summary.innerHTML = '<span class="ap-head">' + head + "</span>" +
      '<span class="ap-count">' + escapeHtml(authorCountLabel(identities, total)) + "</span>";
    panel.appendChild(summary);
    panel.addEventListener("toggle", () => {
      try { localStorage.setItem("hcl-export-authors-open", panel.open ? "1" : "0"); } catch (_) {}
    });

    let filter = null;
    if (identities.length > AUTHOR_FILTER_THRESHOLD) {
      filter = document.createElement("input");
      filter.type = "search";
      filter.id = "author-filter-chips";
      filter.className = "ap-filter";
      filter.placeholder = "Filter these " + identities.length + " names\u2026";
      filter.setAttribute("aria-label", "Filter the author list");
      panel.appendChild(filter);
    }

    const chips = document.createElement("div");
    chips.className = "ap-chips";
    panel.appendChild(chips);
    box.appendChild(panel);
    box.hidden = false;

    identities.forEach((label) => {
      const b = document.createElement("button");
      b.className = "btn ap-chip"; b.type = "button"; b.textContent = label;
      b.title = "Re-import only content by this person";
      b.addEventListener("click", () => reimportByAuthor(chipValue(label)));
      chips.appendChild(b);
    });

    if (filter) {
      filter.addEventListener("input", () => {
        const needle = filter.value.trim().toLowerCase();
        // Hidden rather than rebuilt: every chip carries the handler that
        // re-imports that person, and a rebuild per keystroke drops them.
        chips.querySelectorAll(".ap-chip").forEach((chip) => {
          chip.hidden = !!needle && !chip.textContent.toLowerCase().includes(needle);
        });
      });
    }
  }

  function reimportByAuthor(value) {
    if ($("field-author")) $("field-author").value = value;
    if (!lastStartBody) { showSection("select"); return; }
    beginRun(Object.assign({}, lastStartBody, { author: value }));
  }

  // ---- live drawer content: render a page's REAL derived content (body,
  // comments, attachments) inline in the inspector, straight from the live
  // model, filling in as it's imported -- the prototype's click-a-node-see-
  // its-content, on real data ----
  let drawerContentTimer = null;
  function stopDrawerContent() {
    if (drawerContentTimer) { clearTimeout(drawerContentTimer); drawerContentTimer = null; }
  }

  // Lean /api/model fetch that just refreshes the shared snapshot + state
  // and calls back (no reader-screen messaging) -- used by the drawer.
  function refreshModelSnapshot(cb) {
    fetch("/api/model").then((res) => {
      if (res.status === 503 || !res.ok) { cb(); return; }
      realModelState = res.headers.get("X-HCL-Model-State") || "complete";
      return res.json().then((m) => { REAL_MODEL = m; cb(); });
    }).catch(() => cb());
  }

  // #20: renders a live node's REAL content inline in the ingest drawer --
  // extended (from wiki-only) to blog posts and forum topics, so all three
  // apps get the same click-a-node-see-its-content preview. Each branch
  // reuses the exact same body/comment/reply rendering the reader uses
  // (`sandboxedBodyMarkup`/`resolveBodyImages`/`renderCommentsReal`/
  // `renderReplyThread`) -- never a second, divergent render path.
  function livePageContentHtml(node) {
    const match = REAL_MODEL ? findRealEntityByLiveNode(REAL_MODEL, node) : null;
    if (!match) return null;  // this entity isn't in the derived snapshot yet
    if (match.app === "blog") {
      const blog = (REAL_MODEL.blogs || []).find((b) => b.id === match.blogId);
      const post = blog && blog.posts && blog.posts[match.postId];
      if (!post) return null;
      const rendered = resolveBodyImages(post.content_html, post.assets);
      const body = rendered
        ? '<div class="prose">' + sandboxedBodyMarkup(rendered) + '</div>'
        : '<div class="drawer-hint">Body not imported yet — it’ll appear here as it loads.</div>';
      return body + '<div class="r-comments"><h2>Comments · ' + threadComments(post.comments).length + '</h2>' +
        commentThreadHtml(post.comments, "No comments on this post.") + '</div>';
    }
    if (match.app === "forum") {
      const forum = (REAL_MODEL.forums || []).find((f) => f.id === match.forumId);
      const topic = forum && forum.topics && forum.topics[match.topicId];
      if (!topic) return null;
      const rendered = resolveBodyImages(topic.content_html, topic.assets);
      const body = rendered
        ? '<div class="prose">' + sandboxedBodyMarkup(rendered) + '</div>'
        : '<div class="drawer-hint">Body not imported yet — it’ll appear here as it loads.</div>';
      return body + renderReplyThread(topic);
    }
    if (match.app === "rich_content") {
      const container = (REAL_MODEL.rich_content || []).find((c) => c.id === match.containerId);
      const rcPage = container && container.pages && container.pages[match.pageId];
      if (!rcPage) return null;
      const rcRendered = resolveBodyImages(rcPage.content_html, rcPage.assets);
      // Highlights carry no comments and no replies -- the body is the whole
      // of it, so there is nothing to append here.
      return rcRendered
        ? '<div class="prose">' + sandboxedBodyMarkup(rcRendered) + '</div>'
        : '<div class="drawer-hint">Body not imported yet — it’ll appear here as it loads.</div>';
    }
    if (match.app === "files") {
      const library = (REAL_MODEL.file_libraries || []).find((l) => l.id === match.libraryId);
      const file = library && library.files && library.files[match.fileId];
      if (!file) return null;
      // A document has no reader view -- a spreadsheet or a zip cannot be
      // rendered, and pretending otherwise would be worse than saying plainly
      // what the archive holds. So: the bytes, or why they are absent. Same
      // vocabulary as the reader's file list, deliberately.
      const url = file.asset && file.asset.present ? blobUrl(file.asset.blob_hash) : null;
      const fname = escapeHtml(file.name || match.fileId);
      // Author, size and version are already in the File section above; this
      // pane is only ever the bytes, or the reason there are none.
      return url
        ? '<p><a class="btn" href="' + escapeHtml(url) + '" download="' + fname + '">' +
          icon("download") + ' Download ' + fname + '</a></p>'
        : (file && file.excluded_by_author_filter
          ? '<div class="drawer-hint">This document is by someone else, and this archive was captured with an author filter &mdash; so its bytes were never asked for. The archive records that the file exists.</div>'
          : '<div class="drawer-hint">The bytes of this file were not captured, so there is nothing to download. The archive records that the file exists.</div>');
    }
    const wiki = (REAL_MODEL.wikis || []).find((w) => w.id === match.wikiId);
    const page = wiki && wiki.pages && wiki.pages[match.pageId];
    if (!page) return null;
    const rendered = resolveBodyImages(page.content_html, page.assets);
    const body = rendered
      ? '<div class="prose">' + sandboxedBodyMarkup(rendered) + '</div>'
      : '<div class="drawer-hint">Body not imported yet — it’ll appear here as it loads.</div>';
    return body + renderAttachmentsReal(page) + renderCommentsReal(page);
  }

  const _spinHint = (text) =>
    '<div class="drawer-hint"><span class="spin" style="display:inline-block;vertical-align:-1px;margin-right:6px;"></span>' + text + '</div>';

  // Fill (and keep filling, while the run streams) the page drawer's
  // content pane. Stops when the drawer moves to another node or the run
  // completes.
  function loadLiveDrawerContent(node) {
    stopDrawerContent();
    // `looked` is whether the snapshot has been re-fetched since the run
    // ended. `REAL_MODEL` is taken DURING the run, and the polling that keeps
    // it fresh stops the moment the run completes -- so a page derived after
    // the last snapshot was missing from it permanently, and the drawer sat
    // there telling someone their finished import was still ingesting.
    let looked = false;
    const paint = () => {
      // `openId` is the tree's qualified key, not the raw entity id.
      if (openId !== (node.key || node.id)) { stopDrawerContent(); return; }  // drawer moved on
      const target = $("d-live-content");
      if (!target) return;
      const content = livePageContentHtml(node);
      if (content) { target.innerHTML = content; }
      else if (realModelState !== "complete") {
        target.innerHTML = _spinHint("This page’s content is still being ingested — it fills in here as it loads.");
      } else if (!looked) {
        // The run is over and this page is not in the snapshot we hold. One
        // more look before saying anything: the snapshot, not the archive, is
        // the thing most likely to be out of date.
        looked = true;
        refreshModelSnapshot(paint);
        return;
      } else {
        target.innerHTML = '<div class="drawer-hint">This run is finished and this page is not in the archive it produced. Nothing is still loading; the page was recorded but its content was not captured.</div>';
      }
      if (realModelState !== "complete") {
        drawerContentTimer = setTimeout(() => refreshModelSnapshot(paint), 1200);
      }
    };
    if (REAL_MODEL) paint(); else refreshModelSnapshot(paint);
  }

  function renderLiveDrawer(it) {
    $("d-title").textContent = it.title || it.id;
    $("d-path").innerHTML = '<span>' + escapeHtml(it.kindLabel || it.kind) + '</span>' +
      (it.discoveredFrom ? '<span class="sep">›</span><span>from ' + escapeHtml(shortLabel(it.discoveredFrom)) + '</span>' : '');
    const st = it.state;
    const stt = { pending: "Queued", active: "Ingesting…", ok: "Archived", warn: "Archived (with warning)", failed: "Failed — recorded" }[st] || st;
    $("d-state").className = "dstate " + st; $("d-state-t").textContent = stt;
    // A page/post/topic node can be opened in the reader while the run is
    // still going: the reader renders it from the
    // live partial model. A group header (wikigroup/group) has no reader
    // entry point of its own.
    // Every kind the tree can show has something to show in the drawer. A
    // highlight has a body like a page does; a file has no body at all, so
    // its bytes ARE its content and the pane offers them for download.
    const hasContent = it.kind === "page" || it.kind === "post" || it.kind === "topic"
      || it.kind === "rich_content" || it.kind === "file";
    $("d-reader").style.display = hasContent ? "" : "none";

    const w = warningFor(it.id);
    let html = "";
    if (st === "pending" || st === "active") {
      html += '<div class="pending-note" style="margin-top:16px;"><span class="spin"></span> Fetching this ' + escapeHtml(it.kindLabel || it.kind) + '… this view fills in the moment the event lands.</div>';
    } else if (st === "failed") {
      html += '<div class="pending-note" style="margin-top:16px; color:var(--crit); background:var(--crit-soft); border-color:var(--crit);">✕ Fetch failed — recorded in the manifest as a failure row, not a silent gap. Eligible for retry on a resumed run.</div>';
    } else if (w) {
      html += '<div class="pending-note" style="margin-top:16px; color:var(--' + (w.kind === "warn" ? "warn" : "crit") + '); background:var(--' + (w.kind === "warn" ? "warn" : "crit") + '-soft); border-color:var(--' + (w.kind === "warn" ? "warn" : "crit") + ');">⚠ ' + w.h + '</div>';
    }

    if (it.pageCounts) {
      html += section("Page", '<div class="metagrid">' +
        m("Comments", it.pageCounts.comments) + m("Versions", it.pageCounts.versions) + m("Attachments", it.pageCounts.attachments) + '</div>');
    }
    if ((it.kind === "page" || it.kind === "post" || it.kind === "topic") && (it.author || it.published || it.modified)) {
      html += section(it.kind === "page" ? "Page" : it.kind === "post" ? "Post" : "Topic", '<div class="metagrid">' +
        (it.author ? m("Author", escapeHtml(it.author)) : "") +
        (it.published ? m("Published", escapeHtml(whenText(it.published))) : "") +
        (it.modified ? m("Modified", escapeHtml(it.modified)) : "") + '</div>');
    }

    // A file node has no body to render, so its facts ARE the inspector.
    // "Captured" is the one that matters: knowing of a file and holding its
    // bytes are different things, and only one of them is an archive.
    if (it.kind === "file") {
      html += section("File", '<div class="metagrid">' +
        (it.author ? m("Author", escapeHtml(it.author)) : "") +
        (it.size != null ? m("Size", escapeHtml(humanSize(it.size))) : "") +
        (it.versionLabel ? m("Version", escapeHtml(it.versionLabel)) : "") +
        m("Captured", it.bytesCaptured == null
          ? '<span style="color:var(--warn);">bytes not downloaded</span>'
          : escapeHtml(humanSize(it.bytesCaptured))) + '</div>');
    }

    // For a page/post/topic node: render its REAL content inline, live, as
    // it's imported -- the drawer becomes the
    // click-a-node-see-its-content inspector the prototype had, now on
    // real data, for all three apps.
    if (hasContent) {
      html += '<div class="dsection"><div class="sh">Content <span class="meta" style="font-weight:400;">— live, as it’s imported</span></div>' +
        '<div id="d-live-content">' + _spinHint("Loading imported content…") + '</div></div>';
    }

    // #19: `url` is never populated on a live node (the crawler events that
    // build the tree don't carry one) -- render the provenance row only
    // when there's an actual value, never the literal string "null".
    html += '<div class="dsection"><div class="sh">Live provenance</div><div class="prov">' +
      (it.url ? pr("url", it.url) : '') + (it.discoveredFrom ? pr("discovered_from", it.discoveredFrom) : '') + '</div></div>';

    if (!hasContent) {
      html += '<div class="drawer-hint" style="margin-top:16px;">This view shows exactly what the live event stream carries for this node. For rendered page content, use “Open reader” — content is browsable live, as it’s imported.</div>';
    }

    $("d-body").innerHTML = html;
    if (hasContent) loadLiveDrawerContent(it);
  }

  // #21: a plain-English label for what a run is writing to, from the same
  // request body Restart replays (`lastStartBody`) -- used before the
  // server has confirmed anything (`startLive`), so the topbar never shows
  // the previous hardcoded, always-stale placeholder path.
  function describeRunTarget(startBody) {
    return (startBody && startBody.demo) ? "Demo (fake data)" : ((startBody && startBody.base_url) || "This run");
  }
  function setRoutePath(html) {
    const el = $("route-path");
    if (el) el.innerHTML = html;
  }

  function startLive() {
    liveGotEvent = false; livePendingPageId = null; liveRunning = true;
    setSelectRunning(true);
    resetSharedState();
    refreshCurrentArchive();  // the new run's archive is attached server-side
    const _isDemoRun = !!(lastStartBody && lastStartBody.demo);
    setBanner("connecting", _isDemoRun ? "Connecting to the demo pipeline…" : "Connecting to the source system…");
    setRoutePath('<span>' + escapeHtml(describeRunTarget(lastStartBody)) + '</span><span class="arrow" aria-hidden="true">→</span><span>connecting…</span>');
    // The reader is wired to /api/model independently of
    // this console's SSE state. It's disabled until the first page is
    // imported -- then it opens on the *live* partial
    // model and refreshes as more pages arrive; before that /api/model is
    // 503 and there is nothing to browse.
    setReaderEnabled(false);
    const pill = $("statuspill"); pill.className = "status-pill live"; $("statustext").textContent = "Crawling";
    // Arming: the console has flipped from configuration to observation. The
    // pill and bar carry that as colour (which reduced motion keeps); the
    // beam starts breathing as soon as we are listening, not on first event,
    // so a slow first response still looks connected rather than dead.
    const armed = document.querySelector(".overall"); if (armed) armed.classList.add("armed");
    setBeamRunning(true);
    $("foot-note").textContent = "Click any node to open what's known about it so far — live, as events arrive.";
    startTs = Date.now();
    clockTimer = setInterval(() => { tickClock(); if (liveRunning) pushSpark(Number($("k-tp").textContent) || 0); }, 1000);

    let es;
    try {
      es = new EventSource("/events");
    } catch (_) {
      showUnreachableBanner("Couldn't connect — this browser doesn't support EventSource (SSE).");
      return;
    }
    liveSource = es;

    es.addEventListener("message", (ev) => {
      liveGotEvent = true;
      if (mode !== "live") setBanner("live", _isDemoRun
        ? "Demo — reconstructing from built-in sample data (fake server)."
        : "Live — importing from the source system.");
      let data;
      try { data = JSON.parse(ev.data); } catch (_) { return; }
      handleLiveEvent(data);
    });

    es.addEventListener("error", () => {
      if (!liveGotEvent) {
        try { es.close(); } catch (_) {}
        liveSource = null;
        showUnreachableBanner("Couldn't reach the live demo pipeline — the server may be unreachable. Try Restart.");
      }
      // Otherwise the server already closed the stream normally after
      // `run_complete` (liveRunComplete() handled it); some browsers
      // fire a final "error" on that ordinary closure too.
    });
  }

  // ---------- setup screen: capabilities first, then the URL as the
  // hero input. Dropping/pasting a URL calls /api/identify; a
  // successful identify REVEALS the tailoring panel with the scope
  // options filtered to the detected app (wiki | blog | forum), the
  // target id pre-filled, the feed item count, and the preview limit.
  // Before any URL (or when identify can't parse one) the panel guides
  // the user to the URL — with a manual fallback that shows all scope
  // options, as before. /api/start is unchanged. ----------

  // ---------- app shell: persistent left sidebar + in-page section
  // router. Sections are panels inside #app-main; showSection() hides
  // every panel but the requested one and marks the matching sidebar
  // nav item active. The old setup/console screen togglers below are
  // now thin wrappers so every existing call site keeps working. ----------
  const SECTIONS = ["overview", "select", "ingest", "archives", "reader", "manual", "licenses", "settings"];
  // What ships in this build, and under what terms. Read once per visit --
  // the bundle inside a build cannot change while it runs.
  let licensesLoaded = false;
  function loadLicenses() {
    if (licensesLoaded) return;
    const body = $("licenses-body");
    if (!body) return;
    fetch("/api/licenses")
      .then((r) => (r.ok ? r.json() : r.json().then((e) => Promise.reject(e))))
      .then((data) => {
        licensesLoaded = true;
        const rows = data.components || [];
        const table = document.createElement("table");
        table.className = "lic-table";
        table.innerHTML = "<thead><tr><th>Component</th><th>Version</th><th>License</th>" +
          "<th>Text</th></tr></thead>";
        const tbody = document.createElement("tbody");
        rows.forEach((row) => {
          const tr = document.createElement("tr");
          const links = (row.texts || []).map((path) =>
            '<a href="/api/licenses/text?path=' + encodeURIComponent(path) + '" target="_blank" rel="noopener">' +
            escapeHtml(path.split("/").pop()) + "</a>").join(" ");
          tr.innerHTML = "<td>" + escapeHtml(row.name) + "</td><td class=\"lic-v\">" +
            escapeHtml(row.version) + '</td><td>' + escapeHtml(row.license) +
            "</td><td>" + (links || "&mdash;") + "</td>";
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.innerHTML = "";
        body.appendChild(table);
        const count = document.createElement("div");
        count.className = "foot-note";
        count.textContent = rows.length + " components.";
        body.appendChild(count);
        // What runs but does not ship. The table is an account of what is
        // distributed; read as an account of what runs it would be wrong about
        // the biggest piece of all -- the browser, which is never bundled.
        (data.not_shipped || []).forEach((item) => {
          const note = document.createElement("div");
          note.className = "lic-not-shipped";
          note.innerHTML = "<b>Not included: " + escapeHtml(item.name) + "</b>" +
            "<div>" + escapeHtml(item.why) + ".</div>" +
            "<div>" + escapeHtml(item.how) + ".</div>";
          body.appendChild(note);
        });
      })
      .catch((err) => {
        // A build with no bundle is a real state, and saying so beats an
        // empty table that reads as "no dependencies".
        body.innerHTML = '<div class="foot-note">' +
          escapeHtml((err && err.detail) || "Could not read this build's licence inventory.") +
          "</div>";
      });
  }

  function showSection(id) {
    if (!SECTIONS.includes(id)) id = "overview";
    SECTIONS.forEach((s) => {
      const panel = document.getElementById("panel-" + s);
      if (!panel) return;
      const showing = s === id;
      panel.hidden = !showing;
      // Only the incoming panel is animated -- switching views is how people
      // get somewhere, so an exit animation would just be latency.
      panel.classList.remove("entering");
      if (showing && !reduce) { void panel.offsetWidth; panel.classList.add("entering"); }
    });
    document.querySelectorAll("#sidebar .nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.section === id));
    try { localStorage.setItem("hcl-export-section", id); } catch (_) {}
    if (id === "licenses") loadLicenses();
    if (id === "settings" && typeof loadSettings === "function") loadSettings();
    // Always refresh the recent-archives list when the Overview is shown, so
    // it reflects runs finished this session and, crucially, drops archives
    // that were removed since page load. A stale entry would otherwise 404 on
    // click ("I was not able to open the one archive it presented me"); the
    // list is loaded once at init, so without this it never self-corrects.
    if (id === "archives" && typeof loadRecentArchives === "function") loadRecentArchives();
    // Always show which archive is in use when entering ingest or reader.
    if ((id === "ingest" || id === "reader") && typeof refreshCurrentArchive === "function") {
      refreshCurrentArchive();
    }
  }
  document.querySelectorAll("#sidebar .nav-item").forEach((b) =>
    b.addEventListener("click", () => {
      // Reader gets its real entry point (fetches /api/model and renders
      // the empty-state / "Open an archive…" CTA when there's nothing
      // loaded yet) rather than just uncovering a blank panel -- the
      // sidebar is now a valid way into the reader, not only the
      // in-flow "Open reader" buttons.
      if (b.dataset.section === "reader" && typeof enterReader === "function") { enterReader({ reveal: false }); return; }
      showSection(b.dataset.section);
    }));

  // Collapsible sidebar (icon rail) -- state persists across reloads.
  function setSidebarCollapsed(on) {
    document.getElementById("sidebar").classList.toggle("collapsed", on);
    const b = document.getElementById("sb-collapse");
    if (b) b.textContent = on ? "»" : "«";
    try { localStorage.setItem("hcl-export-sidebar", on ? "1" : "0"); } catch (_) {}
  }
  document.getElementById("sb-collapse").addEventListener("click", () =>
    setSidebarCollapsed(!document.getElementById("sidebar").classList.contains("collapsed")));

  // The brand glyph doubles as a "home" link back to the Overview -- it's
  // a real <button>, not inert text, so it works via click and keyboard.
  document.getElementById("sb-brand")?.addEventListener("click", () => showSection("overview"));

  // Pinned run-status card -- reflects the in-progress/finished run so it
  // stays visible even while browsing other sections; click jumps back to
  // the ingest console. Hidden (and cleared) when there's no run to show.
  function updateRunStatus(s) {
    const el = document.getElementById("sb-run-status");
    if (!el) return;
    if (!s) { el.hidden = true; return; }
    el.hidden = false;
    el.textContent = `● ${s.state || "run"} ${s.done ?? 0}/${s.total ?? 0}`;
  }
  document.getElementById("sb-run-status").addEventListener("click", () => showSection("ingest"));

  function showSetupScreen() { showSection("select"); }
  function showConsoleScreen() { showSection("ingest"); }

  function setSetupNote(kind, text) {
    const note = $("setup-note");
    if (!text) { note.hidden = true; note.className = "setup-note"; note.textContent = ""; return; }
    note.hidden = false;
    note.className = "setup-note" + (kind ? " " + kind : "");
    note.textContent = text;
  }

  // The app the last identify recognised (wiki | blog | forum | community); drives
  // what the target field means and what `/api/start` is told to crawl.
  let identifiedApp = "wiki";
  let identifiedCommunityUuid = null;
  // The human name of whatever was identified, kept for naming the
  // archive directory. Resolved from the feed's own title where the
  // deployment provides one, else the id out of the URL.
  let identifiedTargetLabel = null;
  let identifiedCommunity = false;
  // A URL-derived deployment must win over editable defaults. Keep it
  // separate from the field so settings loads cannot replace the source
  // selected by the user.
  let identifiedBaseUrl = null;
  // The URL-identified scope + single-entry ids, captured from /api/identify so
  // "Start ingest" scopes the run to exactly what the URL named (a single blog
  // post / forum thread), not the whole blog/forum. Reset whenever identify
  // fails or manual setup is entered.
  let identifiedScope = "all";
  let identifiedEntrySlug = null;
  let identifiedTopicId = null;
  // The last URL that identified, so the mode buttons can re-fetch its
  // feed-info count for the newly-chosen scope. MUST be declared: strict mode
  // makes an assignment to an undeclared name a ReferenceError, which
  // applyIdentifyResult's promise catch would mislabel as "couldn't reach the
  // server to identify that URL".
  let lastIdentifiedUrl = null;
  //: Refreshes the echoed CLI command. Assigned by `wireSetupScreen`, which is
  //: where it can see the selection; called from `applyIdentifyResult`, which is
  //: a sibling function and cannot. A no-op until the setup screen is wired, so
  //: an identify that lands first does not throw.
  let refreshCliEcho = () => {};
  // How much to ingest, chosen explicitly via the size-chooser
  // (#size-all / #size-preview) -- "all" | "preview". Defaults to "all" so
  // an import captures everything; "preview" is an explicit opt-in to avoid
  // accidentally archiving only a subset. #start-ingest is the ONLY place a
  // run begins (never implicitly from identify/paste/drop).
  let sizeMode = "all";
  const _TARGET = {
    wiki:  { label: "Wiki",          placeholder: "eng-handbook" },
    blog:  { label: "Blog (handle)", placeholder: "team-blog" },
    forum: { label: "Forum (uuid)",  placeholder: "leave blank for all forums" },
  };

  function setTargetField(app, value) {
    identifiedApp = app || "wiki";
    const t = _TARGET[identifiedApp] || _TARGET.wiki;
    $("field-target-label").textContent = t.label;
    $("field-wiki-label").placeholder = t.placeholder;
    if (value != null) $("field-wiki-label").value = value;
  }

  function resultAppForStart() {
    return identifiedCommunity ? "community" : identifiedApp;
  }

  // True while a community's components are still being discovered. Reading
  // them from a real deployment takes long enough that Start was reachable
  // against a half-built list -- and a run started then silently captures
  // only whatever had arrived.
  let analysisInFlight = false;

  function setAnalysing(on, detail) {
    analysisInFlight = on;
    const note = $("analysis-note");
    if (note) {
      note.hidden = !on;
      if (on && detail) note.querySelector(".analysis-detail").textContent = detail;
    }
    ["start-ingest", "run-demo"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      if (on) {
        el.dataset.analysingDisabled = "1";
        el.disabled = true;
        el.title = "Still working out what this URL contains — one moment.";
      } else if (el.dataset.analysingDisabled) {
        delete el.dataset.analysingDisabled;
        el.disabled = false;
        el.title = "";
      }
    });
  }

  // ---- Archive mode: the component ledger ---------------------------------
  //
  // What the archive holds, what the live system has now, and what this run
  // should do about each component. The asymmetry between apps is the point:
  // a blog can be asked "what changed since", a wiki has to be rescanned, and
  // the cost of each is shown rather than explained afterwards.

  //: What each row's chosen action is, keyed `kind:id`.
  const ledgerActions = new Map();
  let ledgerData = null;

  //: How each component answers "what changed", in words rather than
  //: mechanism, plus the hole that method leaves. Kept beside the cost so a
  //: user can weigh both without opening anything.
  const UPDATE_METHOD_NOTE = {
    since: "Asks the server only for items changed since the date above — a few requests, plus each changed item.",
    rescan: "The list has to be re-read and each item's own date compared: this system offers no way to ask for changes. Unchanged items are still cheap — their text, history, comments and images are not fetched again.",
    reread: "Small enough to re-read in full every time.",
  };

  //: The gap the re-check option closes, in the words of someone deciding
  //: whether to pay for it. Shown as a tooltip on the checkbox itself.
  const RECHECK_TOOLTIP =
    "Asking \u201cwhat changed since\u201d returns items that changed. " +
    "Someone adding a comment to an old post may not count as changing it, " +
    "so without this an update can miss new discussion on older content. " +
    "Costs about one extra request per item.";

  //: What this run will cost, before it looks. Always "at least": the changed
  //: set cannot be counted until the run asks, and saying otherwise would put
  //: a number on the screen that the run then exceeds.
  //: What a FRESH capture will cost, before it starts.
  //:
  //: The counts were always fetched and the delay was always known; the
  //: screen simply never multiplied one by the other. Without it, "run big
  //: exports overnight" asks people to judge which of their exports is big.
  function updateFreshEstimate() {
    const el = $("run-estimate");
    if (!el) return;
    // In archive mode the estimate lives in the ledger; a stale "New
    // archive · …" line here would contradict the Extend & update button
    // beside it.
    if (archiveSubject) { el.textContent = ""; return; }
    // The count is rendered with thousands separators, and `parseInt` stops
    // at the first one -- "1,240" would be read as 1.
    const shown = (($("size-all-n") || {}).textContent || "").replace(/[^0-9]/g, "");
    const items = parseInt(shown, 10);
    if (!Number.isFinite(items) || items <= 0) { el.textContent = ""; return; }
    const capped = sizeMode === "all" ? items : Math.min(items, parseInt(($("size-count") || {}).value, 10) || items);
    const delay = parseFloat(($("field-delay") || {}).value || "1") || 1;
    // Roughly: an item costs its own fetch plus its comments, and feeds and
    // assets add a share on top. Deliberately called "roughly" -- an estimate
    // presented as a figure is a promise the run then breaks.
    const requests = Math.round(capped * 2.5) + 8;
    const seconds = Math.round(requests * delay);
    // The figures are the message, so they carry the weight (<b>); the words
    // around them stay quiet. Every interpolated value here is a computed
    // number, never user text.
    let text = "New archive \u00b7 <b>" + capped.toLocaleString() + " item" + (capped === 1 ? "" : "s") +
      "</b> \u00b7 roughly <b>" + requests.toLocaleString() + "</b> requests \u00b7 about <b>" +
      humanDuration(seconds) + "</b> at " + delay + " s between requests.";
    if (seconds > 3600 && delay < 3) {
      text += " A run this long is kinder to a busy deployment overnight at 3 s.";
    }
    el.innerHTML = text;
  }

  function updateEstimate() {
    updateFreshEstimate();
    const el = $("ledger-estimate");
    if (!el || !ledgerData) return;
    const chosen = ledgerSelections();
    if (!chosen.update.length && !chosen.add.length) {
      el.textContent = "Nothing selected \u2014 this run would do nothing.";
      return;
    }
    const delay = parseFloat(($("field-delay") || {}).value || "1") || 1;
    const seconds = Math.round(chosen.floor * delay);
    const parts = ["Existing archive"];
    if (chosen.add.length) {
      const items = chosen.add.reduce((sum, row) => sum + (row.count || 0), 0);
      parts.push("adding " + items + " item" + (items === 1 ? "" : "s"));
    }
    if (chosen.update.length) {
      parts.push("updating " + chosen.update.length + " component" +
        (chosen.update.length === 1 ? "" : "s"));
    }
    parts.push("at least " + humanDuration(seconds) + " at " + delay + " s between requests");
    el.textContent = parts.join(" \u00b7 ") +
      ". Each item that actually changed adds a few requests on top \u2014 that part " +
      "cannot be counted before the run looks.";
  }

  function humanDuration(seconds) {
    if (seconds < 90) return Math.max(1, Math.round(seconds)) + " s";
    const minutes = Math.round(seconds / 60);
    if (minutes < 90) return minutes + " min";
    return (minutes / 60).toFixed(1) + " h";
  }

  function ledgerKey(row) { return row.kind + ":" + (row.id || ""); }

  function ledgerAction(row) {
    const key = ledgerKey(row);
    if (ledgerActions.has(key)) return ledgerActions.get(key);
    // Captured rows default to Update: someone who opened an archive is here
    // to bring it up to date. Uncaptured rows default to Skip, because adding
    // a component is a decision -- it should be taken, never arrived at.
    return row.in_archive ? "update" : "skip";
  }

  function renderLedgerRow(row) {
    const key = ledgerKey(row);
    const action = ledgerAction(row);
    const el = document.createElement("div");
    el.className = "ledger-row";
    el.dataset.key = key;

    const captured = row.captured_at ? whenText(row.captured_at) : null;
    const facts = row.in_archive
      ? escapeHtml(row.count + " captured" + (captured ? " " + captured : "")) +
        (Number.isFinite(row.live_count)
          ? " \u00b7 " + escapeHtml(String(row.live_count)) + " on the live system now" +
            (row.live_count > row.count
              ? " (" + (row.live_count - row.count) + " more than captured)"
              : "")
          : "")
      : escapeHtml(row.count + " on the live system \u2014 never captured into this archive");

    const pair = row.in_archive
      ? [["leave", "Leave as is"], ["update", "Update"]]
      : [["skip", "Skip"], ["add", "Add now"]];

    const kindIcon = {
      wiki: "reader", blog: "pen", ideation_blog: "flask",
      forum: "comment", files: "archive", rich_content: "columns", search: "search",
    }[row.kind] || "package";
    el.innerHTML =
      '<span class="lr-icon" aria-hidden="true">' + icon(kindIcon) + '</span>' +
      '<div><div class="lr-title">' + escapeHtml(row.title || row.kind) +
        '<span class="lr-kind">' + escapeHtml(kindLabel(row.kind)) + "</span>" +
        '<span class="lr-chip' + (row.in_archive ? ' have' : ' new') + '">' +
        (row.in_archive ? "in archive" : "not captured") + "</span></div>" +
        '<div class="lr-facts">' + facts + "</div></div>" +
      '<div class="lr-cost">' + escapeHtml(ledgerCost(row, action)) + "</div>" +
      '<div class="lr-actions">' +
        pair.map(([value, label]) =>
          '<button type="button" data-action="' + value + '" aria-pressed="' +
          (action === value ? "true" : "false") + '">' + label + "</button>").join("") +
      "</div>";

    if (action === "update" || action === "add") {
      const note = document.createElement("div");
      note.className = "lr-note";
      note.textContent = action === "add"
        ? "A full first capture. It lands beside what the archive already holds, and links between them resolve once both are in."
        : (UPDATE_METHOD_NOTE[row.update_method] || "");
      if (action === "update" && row.update_method !== "reread") {
        const label = document.createElement("label");
        label.title = RECHECK_TOOLTIP;
        const box = document.createElement("input");
        box.type = "checkbox";
        box.className = "lr-recheck";
        box.checked = !!row.recheck;
        box.addEventListener("change", () => {
          row.recheck = box.checked;
          renderLedger();
          refreshCliEcho();
        });
        label.appendChild(box);
        label.appendChild(document.createTextNode(
          " Also re-check comments and replies (about one extra request per item)"));
        note.appendChild(label);
      }
      el.appendChild(note);
    }

    el.querySelectorAll(".lr-actions button").forEach((button) => {
      button.addEventListener("click", () => {
        ledgerActions.set(key, button.dataset.action);
        renderLedger();
        refreshCliEcho();
      });
    });
    return el;
  }

  //: The per-row cost, in the mono column. "at least", always: what actually
  //: changed cannot be counted until the run looks.
  function ledgerCost(row, action) {
    if (action === "leave" || action === "skip") return "not in this run";
    if (action === "add") return "first capture \u00b7 \u2248 " + (row.floor_requests || 0) + " req";
    const extra = row.recheck ? " + re-check" : "";
    const verdict = { since: "date query", rescan: "rescan", reread: "full re-read" };
    return (verdict[row.update_method] || "update") + extra +
      " \u00b7 \u2248 " + (row.floor_requests || 0) + " req + changes";
  }

  //: Whether the demo's second wave has been revealed. The server builds a
  //: fresh fake deployment per run, so this travels with the next start
  //: rather than living on the server -- and it is why "let a week pass" is a
  //: state the console holds rather than a request it sends.
  let demoWave = 0;

  function wireDemoTime() {
    const button = $("demo-advance");
    if (!button) return;
    button.addEventListener("click", () => {
      demoWave = 1;
      const status = $("demo-time-status");
      if (status) {
        status.textContent =
          " A week has passed \u2014 there is new content now. Update below to pick it up.";
      }
      button.disabled = true;
    });
  }

  function renderLedger() {
    const wrap = $("archive-ledger"), rows = $("ledger-rows");
    if (!wrap || !rows || !ledgerData) return;
    rows.innerHTML = "";
    (ledgerData.rows || []).forEach((row) => rows.appendChild(renderLedgerRow(row)));
    (ledgerData.available || []).forEach((component) =>
      rows.appendChild(renderLedgerRow({
        kind: component.kind,
        id: component.id,
        title: component.title,
        count: component.count || 0,
        in_archive: false,
        update_method: "since",
        floor_requests: component.count || 0,
      })));

    const demoRow = $("demo-time-row");
    // Demo only: on a real deployment, time passes without being asked.
    if (demoRow) demoRow.hidden = !(runHasStarted && lastStartBody && lastStartBody.demo);
    const since = $("ledger-since-line");
    if (since) {
      since.textContent = ledgerData.since
        ? "Changes are looked for since " + whenText(ledgerData.since) +
          " \u2014 one minute before the last capture began. The spare minute covers small " +
          "clock differences and edits made while that capture ran. Blogs and forums are asked " +
          "server-side with this date; wiki pages and files are compared date by date instead, " +
          "so a skewed clock cannot cost them content."
        : "This archive has no completed capture to date from, so this run looks at everything.";
    }
    const inherited = $("ledger-inherited");
    if (inherited) {
      inherited.textContent = ledgerData.author_filter
        ? "Whose content: only " + ledgerData.author_filter +
          " \u2014 the choice this archive was made with. Updates and additions keep it, so the " +
          "archive stays one consistent answer to one question."
        : "Whose content: everyone \u2014 the choice this archive was made with.";
    }
    updateEstimate();
  }

  //: What the run bar says before Start, in archive mode.
  function ledgerSelections() {
    const out = { update: [], add: [], recheck: false, floor: 0 };
    const all = (ledgerData ? (ledgerData.rows || []).concat(
      (ledgerData.available || []).map((c) => ({
        kind: c.kind, id: c.id, title: c.title, count: c.count || 0,
        in_archive: false, floor_requests: c.count || 0, update_method: "since",
      }))) : []);
    all.forEach((row) => {
      const action = ledgerAction(row);
      if (action === "update") { out.update.push(row); out.floor += row.floor_requests || 0; }
      if (action === "add") { out.add.push(row); out.floor += row.floor_requests || 0; }
      if (action === "update" && row.recheck) { out.recheck = true; out.floor += row.count || 0; }
    });
    return out;
  }

  //: Enter archive mode: the subject is an existing archive plus the live
  //: system, rather than a URL. Fetches the ledger and swaps the tailor zone.
  function openArchiveForUpdate(name) {
    const wrap = $("archive-ledger");
    const components = $("community-components");
    if (!wrap) return;
    // Nothing visible may wait for the fetch below: that fetch asks the
    // deployment whether it answers -- seconds, on a real one -- so a button
    // that waited for it would do nothing at all for those seconds, on the
    // screen you were already looking at, with no way to tell a slow answer
    // from a dead
    // button. Open the screen first and say what is happening; the ledger
    // fills in when it arrives.
    showSection("select");
    const opening = $("setup-note");
    if (opening) {
      opening.hidden = false;
      opening.innerHTML =
        '<span class="spin" style="display:inline-block;vertical-align:-1px;margin-right:6px;"></span>' +
        "Reading " + escapeHtml(name) + " \u2014 and asking the original system whether it answers.";
    }
    fetch("/api/archive-ledger?name=" + encodeURIComponent(name))
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error("no ledger"))))
      .then((data) => {
        ledgerData = data;
        ledgerActions.clear();
        archiveSubject = name;
        wrap.hidden = false;
        if (components) components.hidden = true;
        const held = (data.rows || []).reduce((sum, row) => sum + (row.count || 0), 0);
        const componentCount = (data.rows || []).length;
        showIdentityStrip({
          name: data.display_name || data.archive,
          // The "existing archive" chip beside the title already names the
          // kind; a second chip saying "archive" would say it twice.
          kind: "",
          url: data.archive || "",
          extra: componentCount + " component" + (componentCount === 1 ? "" : "s") +
            " \u00b7 " + held + " item" + (held === 1 ? "" : "s") + " in the archive" +
            (data.since ? " \u00b7 last captured " + whenText(data.anchor || data.since) : ""),
          auth: data.live_error
            ? "The live system did not answer, so nothing new can be added right now."
            : "Live system reachable \u2014 extending and updating need it.",
        });
        const chip = $("is-archive-chip");
        if (chip) chip.hidden = false;
        const fresh = $("capture-fresh");
        if (fresh) fresh.hidden = false;
        // A leftover "Identified …" note from an earlier URL would be a
        // statement about a different subject than the archive now shown.
        const note = $("setup-note");
        if (note) note.hidden = true;
        // The scope cards describe a fresh capture; in archive mode the
        // ledger IS the tailoring, and showing both would offer two
        // contradictory ways to say the same thing.
        const modes = $("mode-grid");
        if (modes) modes.hidden = true;
        showSection("select");
        $("tailor-body").hidden = false;
        const guide = $("tailor-guide");
        if (guide) guide.hidden = true;
        renderLedger();
        setStartLabel();
      })
      .catch(() => {
        if (opening) opening.hidden = true;
        notify("Could not read that archive's contents.");
      });
  }

  //: The archive this run is adding to, or null for a fresh capture. Read by
  //: the run bar and the CLI echo so "fresh vs extending" is never implied by
  //: layout alone.
  let archiveSubject = null;

  function setStartLabel() {
    const button = $("start-ingest");
    if (!button) return;
    const label = archiveSubject ? "Extend & update" : "Start ingest";
    const icon = button.querySelector("svg");
    button.textContent = " " + label;
    if (icon) button.prepend(icon);
  }

  // One component card, for whichever community it belongs to. Extracted
  // so an added community cannot end up with a second, poorer rendering --
  // a bare checkbox and "title · count" -- while the first gets the kind's
  // description, a formatted count and the Highlights caveat. Two renderings
  // of one decision is how the two drift, and the user is choosing the same
  // thing in both.
  function componentOption(component) {
    const item = document.createElement("label");
    item.className = "component-option";
    const descriptions = {
      wiki: "Wiki pages and hierarchy",
      blog: "Blog posts and comments",
      ideation_blog: "Ideas and comments",
      forum: "Topics and replies",
      files: "Documents and folders",
      rich_content: "The community\u2019s front page",
    };
    let detail = descriptions[component.kind] || "Community content";
    // Rich Content is the one component whose count can be smaller than what
    // the community holds: an owner can place an area and never write in it.
    // Saying so here means the number on the checkbox is never read as the
    // whole story.
    const placed = component.placed;
    if (Number.isFinite(placed) && Number.isFinite(component.count) && placed > component.count) {
      const empty = placed - component.count;
      detail += " \u2014 " + component.count + " with content, " +
        empty + (empty === 1 ? " area" : " areas") + " never written in";
    }
    // The card leads with the component's own name; the kind lives in the
    // description line ("Wiki pages and hierarchy"), and the count sits in a
    // mono column -- it is half of the decision.
    item.innerHTML = '<input type="checkbox" value="' + component.kind + ':' + escapeHtml(component.id) + '">' +
      '<span class="co-main"><b>' + escapeHtml(component.title) +
      '</b><small>' + escapeHtml(detail) + '</small></span>' +
      (Number.isFinite(component.count)
        ? '<span class="c-count">' + component.count.toLocaleString() + '</span>'
        : '');
    item.querySelector("input").checked = true;
    return item;
  }

  // Select all / Clear all for ONE community's boxes. `scope` is that
  // community's own container: two communities are two independent sets, and
  // a control that reached across them would tick content the user did not
  // choose into their archive.
  function componentControls(scope) {
    const controls = document.createElement("div");
    controls.className = "component-controls";
    controls.innerHTML = '<button type="button" class="btn" data-components-action="all">Select all</button>' +
      '<button type="button" class="btn" data-components-action="none">Clear all</button>';
    controls.addEventListener("click", (event) => {
      const action = event.target.closest("button")?.dataset.componentsAction;
      if (!action) return;
      scope.querySelectorAll("input[type=checkbox]").forEach((input) => {
        input.checked = action === "all";
      });
    });
    return controls;
  }

  //: Which render of the component list is the current one. A paste fires two
  //: identifications -- `paste` immediately, `input` after its debounce -- so
  //: two renders overlap whenever the deployment takes a moment to answer,
  //: which is every real one. Without this the second render cleared the
  //: container, the first lookup finished and put its own controls and every
  //: component back, and the list came out twice.
  let componentsRender = 0;
  let componentsInFlight = null;

  function renderCommunityComponents() {
    const wrap = $("community-components"), options = $("community-component-options");
    if (!wrap || !options) return;
    const thisRender = ++componentsRender;
    // Stop the previous lookup rather than merely ignoring it: on a slow
    // deployment it is a request nobody is waiting for any more.
    if (componentsInFlight) {
      try { componentsInFlight.abort(); } catch (_) { /* already done */ }
      componentsInFlight = null;
    }
    options.innerHTML = "";
    const controls = componentControls(options);
    options.appendChild(controls);
    const loading = document.createElement("div");
    loading.className = "component-loading foot-note";
    loading.textContent = "Reading community components…";
    setAnalysing(true, "Reading the community's components");
    options.appendChild(loading);
    wrap.hidden = false;
    // A new identification is a new run: whatever set was assembled for the
    // last one is not this one's.
    extraCommunities = [];
    if ($("extra-community-groups")) $("extra-community-groups").innerHTML = "";
    offerSubcommunities();
      if (identifiedCommunityUuid) {
        const controller = new AbortController();
        componentsInFlight = controller;
        const timeout = setTimeout(() => controller.abort(), 35000);
        fetch("/api/community-components?community_uuid=" + encodeURIComponent(identifiedCommunityUuid) +
          "&base_url=" + encodeURIComponent($("field-base-url").value.trim()), { signal: controller.signal })
          .then((r) => r.json())
          .then((data) => {
            // A later render has already cleared the container and is asking
            // its own question; this answer is about a list that is gone.
            if (thisRender !== componentsRender) return;
          // Name the run after the community, so its archive directory does too.
          if (data && data.community) identifiedTargetLabel = data.community;
            loading.remove();
            const components = data.components || [];
            const knownCounts = components
              .map((component) => component.count)
              .filter((count) => Number.isFinite(count));
            if (knownCounts.length) {
              $("size-all-n").textContent = knownCounts
                .reduce((sum, count) => sum + count, 0)
                .toLocaleString();
              updateEstimate();
            }
            if (!components.length) {
              const empty = document.createElement("div");
              empty.className = "component-loading foot-note";
              // The server says WHY it found none, and the three reasons are
              // different situations: this console cannot see that deployment,
              // no feed answered, or the community genuinely holds nothing.
              // Replacing that with a sentence about the community states the
              // one thing we do not know.
              empty.textContent =
                data.detail || "No named components were found for this community.";
              options.appendChild(empty);
            }
            // Rich Content is addressed by the community uuid rather than
            // discovered by name, so when it is missing there is no half-built
            // row to hint at why. Say what happened instead of showing nothing
            // on a community whose front page the user is looking at.
            if (data && data.rich_content_note) {
              const note = document.createElement("div");
              note.className = "component-loading foot-note";
              note.textContent = "Highlights (rich content): " + data.rich_content_note;
              options.appendChild(note);
            }
            components.forEach((component) => {
              options.appendChild(componentOption(component));
            });
            // No `insertBefore(controls, ...)` here: `controls` is already the
            // first child, and re-inserting it is what put a DETACHED copy
            // back into a container another render had cleared.
          })
          .catch(() => {
            if (thisRender !== componentsRender) return;   // superseded, not failed
            loading.textContent = "Could not read community components (timeout or unavailable).";
          })
          .finally(() => {
            clearTimeout(timeout);
            if (componentsInFlight === controller) componentsInFlight = null;
            if (thisRender === componentsRender) setAnalysing(false);
          });
      }
  }

  function selectedCommunityComponents() {
    return Array.from(document.querySelectorAll("#community-component-options input:checked"))
      .map((input) => input.value);
  }

  // ---------- a run captures a SET of communities ----------
  // The identified community is the first group (the list above); every
  // community added afterwards -- discovered as a child, or pasted as a URL
  // because a merely related community is discoverable from nowhere -- gets
  // its own group here. All of them land in ONE archive, which is what lets
  // the links between them resolve instead of dying with the deployment.
  let extraCommunities = [];   // [{uuid, title, el}]

  function communitiesForRun() {
    const groups = [
      {
        uuid: identifiedCommunityUuid,
        title: identifiedTargetLabel || null,
        components: selectedCommunityComponents(),
      },
    ].concat(
      extraCommunities.map((community) => ({
        uuid: community.uuid,
        title: community.title || null,
        components: Array.from(
          community.el.querySelectorAll("input[type=checkbox]:checked")
        ).map((input) => input.value),
      }))
    );
    // A community with nothing ticked is not captured: unticking everything
    // IS how it is removed, so there is no second control to disagree with.
    return groups.filter((group) => group.uuid && group.components.length);
  }

  function addCommunityGroup(uuid, title) {
    if (!uuid) return Promise.resolve(false);
    if (uuid === identifiedCommunityUuid || extraCommunities.some((c) => c.uuid === uuid)) {
      return Promise.resolve(false);   // already in the run
    }
    const host = $("extra-community-groups");
    if (!host) return Promise.resolve(false);
    const group = document.createElement("div");
    group.className = "community-group";
    group.innerHTML = '<div class="community-group-head">' + escapeHtml(title || uuid) +
      '</div><div class="component-loading foot-note">Reading its components…</div>';
    host.appendChild(group);
    const record = { uuid, title, el: group };
    extraCommunities.push(record);
    return fetch("/api/community-components?community_uuid=" + encodeURIComponent(uuid) +
      "&base_url=" + encodeURIComponent($("field-base-url").value.trim()))
      .then((r) => r.json())
      .then((data) => {
        const components = (data && data.components) || [];
        record.title = title || (data && data.community) || uuid;
        group.innerHTML = '<div class="community-group-head">' + escapeHtml(record.title) + "</div>";
        if (!components.length) {
          const empty = document.createElement("div");
          empty.className = "component-loading foot-note";
          empty.textContent = "No named components were found for this community.";
          group.appendChild(empty);
          return true;
        }
        // The same cards, the same controls, the same caveats as the first
        // community. A sub-community is an ordinary community and the choice
        // being made about it is the same choice.
        const options = document.createElement("div");
        options.className = "component-options";
        group.appendChild(componentControls(options));
        components.forEach((component) => {
          options.appendChild(componentOption(component));
        });
        group.appendChild(options);
        // Rich Content is addressed by the community uuid rather than
        // discovered by name, so when it is missing there is no half-built row
        // to hint at why. Say what happened, here as above.
        if (data && data.rich_content_note) {
          const note = document.createElement("div");
          note.className = "component-loading foot-note";
          note.textContent = "Highlights (rich content): " + data.rich_content_note;
          group.appendChild(note);
        }
        return true;
      })
      .catch(() => {
        group.innerHTML = '<div class="community-group-head">' + escapeHtml(title || uuid) +
          '</div><div class="component-loading foot-note">Could not read its components.</div>';
        return false;
      });
  }

  // The children of the identified community, offered rather than added: a
  // set is the user's to choose, and discovery only supplies candidates.
  // Each child is named. Offering "add its 2 sub-communities" behind a count
  // asks someone to accept content they cannot see; a person recognises the
  // set they meant by its titles, so the titles are the offer.
  //: The same guard as the component list, for the same reason: this clears
  //: its own list and refills it in a callback, so two overlapping offers put
  //: every child in twice.
  let subcommunitiesRender = 0;

  function offerSubcommunities() {
    const offer = $("subcommunity-offer"), list = $("subcommunity-candidates");
    const button = $("add-subcommunities");
    if (!offer || !list || !identifiedCommunityUuid) return;
    const thisRender = ++subcommunitiesRender;
    offer.hidden = true;
    list.innerHTML = "";
    if (button) button.hidden = true;
    fetch("/api/subcommunities?community_uuid=" + encodeURIComponent(identifiedCommunityUuid) +
      "&base_url=" + encodeURIComponent($("field-base-url").value.trim()))
      .then((r) => r.json())
      .then((data) => {
        if (thisRender !== subcommunitiesRender) return;   // a later offer owns the list
        const children = (data && data.children) || [];
        if (!children.length) return;
        offer.hidden = false;
        const head = $("subcommunity-offer-head");
        if (head) {
          head.textContent = children.length === 1
            ? "This community has a sub-community:"
            : "This community has " + children.length + " sub-communities:";
        }
        children.forEach((child) => list.appendChild(subcommunityCandidate(child)));
        if (button && children.length > 1) {
          button.hidden = false;
          button.textContent = "Add all " + children.length;
          button.onclick = () => {
            button.disabled = true;
            Promise.all(children.map((child) => addCommunityGroup(child.id, child.title)))
              .then(() => {
                button.hidden = true;
                button.disabled = false;
                markSubcommunitiesAdded(list);
              });
          };
        }
      })
      .catch(() => {});
  }

  // One named candidate: its title, and its own Add. Adding one is the common
  // case -- a related set is rarely wholesale -- so the per-child control is
  // the primary one and "Add all" is the shortcut beside it.
  function subcommunityCandidate(child) {
    const row = document.createElement("div");
    row.className = "subcommunity-candidate";
    row.dataset.uuid = child.id || "";
    const name = document.createElement("span");
    name.className = "subcommunity-candidate-name";
    name.textContent = child.title || child.id;
    const add = document.createElement("button");
    add.className = "btn subcommunity-add";
    add.type = "button";
    add.textContent = "Add";
    add.onclick = () => {
      add.disabled = true;
      addCommunityGroup(child.id, child.title).then((added) => {
        if (added) {
          add.replaceWith(addedMark());
        } else {
          add.disabled = false;
        }
      });
    };
    row.appendChild(name);
    row.appendChild(add);
    return row;
  }

  function addedMark() {
    const mark = document.createElement("span");
    mark.className = "subcommunity-added foot-note";
    mark.textContent = "Added";
    return mark;
  }

  // "Add all" adds every child at once; the rows it covered say so rather
  // than still offering an Add that would now do nothing.
  function markSubcommunitiesAdded(list) {
    list.querySelectorAll(".subcommunity-candidate button").forEach((add) => {
      add.replaceWith(addedMark());
    });
  }

  function communityUuidFromInput(value) {
    // A pasted community URL carries its uuid as a query parameter; a pasted
    // uuid is already the answer. Anything else is not something this can add
    // without asking the deployment, and says so.
    const text = (value || "").trim();
    const match = text.match(/communityUuid=([0-9a-fA-F-]{36})/) ||
      text.match(/^([0-9a-fA-F-]{36})$/);
    return match ? match[1] : null;
  }

  function communitySelectionLabel() {
    const values = selectedCommunityComponents();
    return values.length ? values.join(", ") : "none";
  }

  // The size chooser: "all" (max_entries: null) vs "preview" (the
  // #size-count stepper, default 10) -- highlights the chosen option so
  // the state is always visible, never just implied by the stepper value.
  function setSizeMode(newMode) {
    sizeMode = newMode === "all" ? "all" : "preview";
    $("size-all").classList.toggle("active", sizeMode === "all");
    $("size-preview").classList.toggle("active", sizeMode === "preview");
  }

  // ---- progressive disclosure: the tailoring panel (scope options,
  // target id, preview limit, item count) stays behind a guide note
  // until a URL is identified — or the user opts into manual setup ----
  function setTailoringVisible(on) {
    const guide = $("tailor-guide"), body = $("tailor-body");
    if (guide) guide.hidden = !!on;
    if (body) body.hidden = !on;
  }

  function setTailorMeta(text) {
    const meta = $("tailor-meta");
    if (!meta) return;
    meta.hidden = !text;
    meta.textContent = text || "";
  }

  // Show only the scope options for the detected app (wiki | blog |
  // forum); `null` shows all of them (the manual fallback).
  function filterModeGrid(app) {
    document.querySelectorAll("#mode-grid .mode-btn").forEach((b) => {
      b.hidden = !!app && b.dataset.app !== app;
    });
  }

  function setActiveModeButton(app, scope) {
    const btns = Array.from(document.querySelectorAll("#mode-grid .mode-btn"));
    const chosen = btns.find((b) => b.dataset.app === app && b.dataset.scope === scope)
      || btns.find((b) => b.dataset.app === app)
      || btns[0];
    btns.forEach((b) => b.classList.toggle("active", b === chosen));
    return chosen;
  }

  function enterManualSetup() {
    // The one path with no URL to derive a deployment address from, so the
    // one place it is still typed.
    const fields = $("manual-setup-fields");
    if (fields) fields.hidden = false;
    filterModeGrid(null);
    const modeGrid = $("mode-grid");
    if (modeGrid) modeGrid.hidden = false;
    setTailoringVisible(true);
    setTailorMeta("manual — pick what to capture, including a community by UUID");
    // Manual setup has no URL-derived single-entry scope.
    identifiedScope = "all";
    identifiedEntrySlug = null;
    identifiedTopicId = null;
    identifiedBaseUrl = null;
    identifiedCommunityUuid = null;
    identifiedTargetLabel = null;
    identifiedCommunity = false;
    const communityPanel = $("community-components");
    if (communityPanel) communityPanel.hidden = true;
    const targetPanel = $("single-component-target");
    if (targetPanel) targetPanel.hidden = false;
  }

  // Populates the editable form from an `/api/identify` result
  // (`ParsedTarget` as JSON: ok/reason/app/base_url/auth_root plus the
  // app-specific wiki_label|blog_handle|forum_uuid) and reveals the
  // tailoring panel filtered to the detected app. `url` is the URL that
  // was identified — it also feeds /api/feed-info for the item count.
  // `ok=false` never guesses -- the note explains why and the tailoring
  // panel falls back to the full manual grid.
  //: Middle-truncate a long path so both ends stay readable. The tail is
  //: what identifies a page; the head is what identifies the deployment.
  //: Cutting only the end -- which is what CSS ellipsis does -- throws away
  //: the half that answers "which one".
  function middleTruncate(text, max) {
    const value = String(text || "");
    if (value.length <= max) return value;
    const head = Math.ceil((max - 1) / 2);
    return value.slice(0, head) + "\u2026" + value.slice(value.length - (max - 1 - head));
  }

  const APP_LABEL = {
    wiki: "wiki", blog: "blog", ideation_blog: "ideas",
    forum: "forum", community: "community",
  };

  //: What a component IS, for a list that shows several kinds together. An
  //: archive's components were told apart by their icon alone, which asks
  //: someone to learn six pictograms to answer "is that a blog or a forum".
  const KIND_LABEL = {
    wiki: "Wiki", blog: "Blog", ideation_blog: "Ideas",
    forum: "Forum", files: "Files", rich_content: "Highlights",
    search: "Search", community: "Community",
  };

  function kindLabel(kind) {
    return KIND_LABEL[kind] || (kind || "").replace(/_/g, " ") || "Component";
  }

  //: Swap the source panel to its second face: what the URL resolves to.
  //: The address itself is demoted to a fact -- it is long, it is cut off at
  //: any sane width, and nothing below this point needs the string.
  function showIdentityStrip({ name, kind, url, extra, auth }) {
    const bar = $("capture-bar"), strip = $("identity-strip");
    if (!bar || !strip) return;
    bar.hidden = true;
    strip.hidden = false;
    $("is-name").textContent = name || "(unnamed)";
    $("is-kind").textContent = kind || "";
    // An empty chip is a pill-shaped smudge; drop it when there is no kind.
    $("is-kind").hidden = !kind;
    const detail = $("is-detail");
    if (detail) {
      let host = "", path = url || "";
      try {
        const parsed = new URL(url);
        host = parsed.host;
        path = parsed.pathname + parsed.search;
      } catch (_) { /* a bare label, not a URL: show it as it is */ }
      detail.textContent = host ? host + " \u00b7 " + middleTruncate(path, 58) : middleTruncate(path, 72);
      // The whole address on hover and to a screen reader, so demoting it
      // never means losing it.
      detail.title = url || "";
    }
    const extraEl = $("is-extra");
    if (extraEl) extraEl.textContent = extra || "";
    const authEl = $("is-auth");
    if (authEl) authEl.innerHTML = auth || "";
  }

  function wireSourcePanel() {
    const change = $("change-source");
    if (change) {
      change.addEventListener("click", () => {
        showCaptureBar();
        const input = $("setup-url-input");
        if (input) { input.focus(); input.select(); }
      });
    }
    const fresh = $("capture-fresh");
    if (fresh) {
      fresh.addEventListener("click", () => {
        // Leaving archive mode is always explicit. Drifting out of it
        // silently is how someone ends up with a second archive they meant
        // to be an update.
        showCaptureBar();
        notify("Capturing fresh — this will create a new archive.", "New archive");
      });
    }
    document.addEventListener("click", (event) => {
      const target = event.target.closest("[data-goto]");
      if (target) showSection(target.dataset.goto);
    });
  }

  //: Ask whether this target is already in an archive here, and offer the
  //: choice if it is. Never decides: a fresh capture beside an older one is a
  //: legitimate thing to want, and quietly turning it into an update would be
  //: worse than saying nothing.
  function checkForExistingArchive(communityUuid) {
    const note = $("collision-note");
    if (!note) return;
    note.hidden = true;
    if (!communityUuid) return;
    fetch("/api/archive-match?community_uuid=" + encodeURIComponent(communityUuid))
      .then((response) => (response.ok ? response.json() : { matches: [] }))
      .then((data) => {
        const match = (data.matches || [])[0];
        if (!match) return;
        $("collision-text").textContent =
          "You have captured this before \u2014 " + match.display_name +
          (match.captured_at ? ", " + whenText(match.captured_at) : "") +
          " (" + match.items + " items across " + match.components + " components).";
        note.hidden = false;
        const extend = $("collision-extend");
        if (extend) extend.onclick = () => { note.hidden = true; openArchiveForUpdate(match.archive); };
        const dismiss = $("collision-dismiss");
        if (dismiss) dismiss.onclick = () => { note.hidden = true; };
      })
      .catch(() => { /* an unreadable archives directory is not worth a warning here */ });
  }

  function showCaptureBar() {
    const collision = $("collision-note");
    if (collision) collision.hidden = true;
    const bar = $("capture-bar"), strip = $("identity-strip");
    if (bar) bar.hidden = false;
    if (strip) strip.hidden = true;
    archiveSubject = null;
    const chip = $("is-archive-chip");
    if (chip) chip.hidden = true;
    const fresh = $("capture-fresh");
    if (fresh) fresh.hidden = true;
    const ledger = $("archive-ledger");
    if (ledger) ledger.hidden = true;
    setStartLabel();
  }

  function applyIdentifyResult(result, url) {
    if (!result) return;
    if (result.ok) {
      identifiedBaseUrl = result.base_url || null;
      if (identifiedBaseUrl) $("field-base-url").value = identifiedBaseUrl;
      // What is being worked on just changed, and the chip reports exactly
      // that -- a demo URL identified IS the demo viewing state.
      loadShellIdentity();
      const app = result.app || "wiki";
      identifiedCommunity = app === "community";
      identifiedCommunityUuid = result.community_uuid || null;
      const target = result.wiki_label || result.blog_handle || result.forum_uuid || "";
      const targetPanel = $("single-component-target");
      if (targetPanel) targetPanel.hidden = app === "community";
      // Re-identifying a single-app URL after a community: the component
      // grid belongs to the old subject and must not linger beside the
      // new one's scope cards.
      const communityPanel = $("community-components");
      if (communityPanel && app !== "community") communityPanel.hidden = true;
      if (app === "community") $("field-wiki-label").value = "";
      if (app === "community") {
        setSizeMode("all");
        $("size-count").value = "10";
      }
      setTargetField(app === "community" ? "wiki" : app, target || null);
      identifiedScope = result.scope || "all";
      identifiedEntrySlug = result.entry_slug || null;
      identifiedTopicId = result.topic_id || null;
      // Best-effort: find the owning community so search can be pinned to it.
      discoverCommunity(app, target || "", identifiedTopicId);
      checkForExistingArchive(identifiedCommunityUuid);
      if (result.auth_root && String(result.auth_root).startsWith("basic")) {
        $("field-auth-mode").value = "basic";
      }
      const authLabel = {
        sspi: "Windows sign-in", kerberos: "Kerberos",
        basic: "Username and password", paste_token: "Pasted token",
      }[($("field-auth-mode") || {}).value || "sspi"];
      showIdentityStrip({
        name: identifiedTargetLabel || target || result.title || url,
        kind: APP_LABEL[app] || app,
        url,
        extra: "",
        // Stated as a fact, not offered as a control: Settings owns the
        // default, and this says which one this capture will use.
        auth: escapeHtml(authLabel) +
          ' <button class="linklike" data-goto="settings" type="button">Settings</button>',
      });

      // Reveal the tailoring options, narrowed to the detected app, with
      // the scope the URL itself implies pre-selected.
      const modeGrid = $("mode-grid");
      filterModeGrid(app === "community" ? null : app);
      if (modeGrid) modeGrid.hidden = app === "community";
      if (app === "community") renderCommunityComponents();
      else setActiveModeButton(app, result.scope || "all");
      setTailoringVisible(true);

      // The "<app> "<name>"" fragment shared by the tailor-meta chip and
      // the setup note (Finding #26). `name` starts as the handle/label/
      // uuid `parse_url` extracted from the URL, then is upgraded below
      // to the feed's own title once /api/feed-info resolves -- never
      // blocking the initial render on that fetch.
      const describeMeta = (name) => app + (name ? " · " + name : "");
      const describeNote = (name) => (name ? " · " + app + " “" + name + "”" : " · " + app);

      setTailorMeta(describeMeta(target) + " — detected from the URL");
      // Default to full import (all entries). Preview is an explicit opt-in:
      // the user must consciously choose a limited capture to avoid
      // accidentally archiving only a subset.
      setSizeMode("all");
      setSetupNote(
        "ok",
        "Identified " + (result.base_url || "an HCL system") + describeNote(target) +
          " — tailor the capture below, then start."
      );
      // Fetch the total entry count from the feed and show it on the "All"
      // option -- informational only; it never changes sizeMode itself.
      // The same response's `title` (the feed's own name) upgrades the
      // tailor-meta chip and setup note from the bare handle/label/uuid
      // to the real blog/forum/wiki name, when the feed provides one.
      lastIdentifiedUrl = url;
      // The URL is what the echoed command leads with, so it only
      // becomes showable once one has been identified.
      refreshCliEcho();
      const allCount = document.getElementById("size-all-n");
      if (allCount && app !== "community") {
        allCount.textContent = "…";
        fetch("/api/feed-info?url=" + encodeURIComponent(url || ""))
          .then((r) => r.json())
          .then((j) => {
            allCount.textContent = j.total != null ? j.total.toLocaleString() : "?";
            updateEstimate();
            const name = (j && j.title) || target;
            identifiedTargetLabel = name || null;
            setTailorMeta(describeMeta(name) + " — detected from the URL");
            setSetupNote(
              "ok",
              "Identified " + (result.base_url || "an HCL system") + describeNote(name) +
                " — tailor the capture below, then start."
            );
          })
          .catch(() => { allCount.textContent = "?"; });
      }
    } else {
      setSetupNote(
        "warn",
        "Couldn't identify a wiki, blog, or forum from that URL" +
          (result.reason ? " (" + result.reason + ")" : "") +
          " — pick what to capture below and enter the fields manually."
      );
      enterManualSetup();
    }
  }

  // A genuine "server unreachable" is a fetch network failure (a TypeError
  // whose message mentions fetch/network/load). ANYTHING else reaching a
  // promise .catch here is an app error thrown while HANDLING a perfectly good
  // response -- and must NOT be mislabeled as unreachable (that mislabeling is
  // exactly the class of bug that hid the old lastIdentifiedUrl ReferenceError).
  function isNetworkError(err) {
    return err instanceof TypeError && /fetch|network|load failed|connection/i.test(err.message || "");
  }

  // Resolve the email (or login) in the author field to the stable directory
  // user id (snx:userid) via the Profiles API, and put THAT in the field --
  // matching by the stable uuid beats the volatile display name.
  function baseUrlParam() {
    const v = ($("field-base-url") ? $("field-base-url").value : "").trim();
    return v ? "&base_url=" + encodeURIComponent(v) : "";
  }

  // Best-effort: discover the community that owns the dragged container, so
  // search can be pinned to it (the only documented sub-component scoping).
  // Reveals the "scope to community" toggle when one is found.
  let discoveredCommunityUuid = null;
  let authorFilterMode = "none";
  // The author filter's value when it came back from the DEPLOYMENT -- "Only
  // me" resolving the signed-in principal, or an email looked up via
  // /api/resolve-user -- rather than being typed. Only such a value may be
  // handed to the Search person query: Search selects which forum threads a
  // community capture reads, and selecting from a query built on a display
  // name that Search happens to answer for would quietly read less than the
  // full walk would. Typing in the field clears it.
  let resolvedAuthorUserid = "";
  //  none  -> Everyone          (no filter)
  //  me    -> Only me            (resolved from the signed-in principal)
  //  custom-> A specific person  (typed name / user id / email)
  const AUTHOR_MODE_NOTES = {
    none: "Captures everything in the selected components, whoever wrote it.",
    me: "Captures only what you wrote or took part in — each item with its full surrounding thread.",
    custom: "Captures only what this person wrote or took part in, with the full surrounding thread.",
  };

  function setAuthorFilterMode(mode) {
    authorFilterMode = mode;
    const segments = { none: "author-mode-everyone", me: "author-mode-me", custom: "author-mode-person" };
    for (const [value, id] of Object.entries(segments)) {
      const el = $(id);
      if (el) el.setAttribute("aria-pressed", value === mode ? "true" : "false");
    }
    const wrap = $("author-person-wrap");
    if (wrap) wrap.hidden = mode !== "custom";
    const note = $("author-mode-note");
    if (note) note.textContent = AUTHOR_MODE_NOTES[mode] || AUTHOR_MODE_NOTES.none;
    // Kept for the Advanced button and any existing callers.
    const button = $("author-me");
    if (button) {
      button.classList.toggle("author-selected", mode === "me");
      button.setAttribute("aria-pressed", mode === "me" ? "true" : "false");
    }
  }

  function wireAuthorSegments() {
    const field = () => $("field-author");
    const everyone = $("author-mode-everyone");
    if (everyone) everyone.addEventListener("click", () => {
      if (field()) field().value = "";
      resolvedAuthorUserid = "";
      setAuthorFilterMode("none");
    });
    const me = $("author-mode-me");
    if (me) me.addEventListener("click", async () => {
      setAuthorFilterMode("me");
      await resolveCurrentUser(true);
      // No signed-in principal (the demo has none): say so and fall back
      // rather than silently capturing everything under a "Only me" label.
      if (field() && !field().value.trim()) {
        // Reset the mode FIRST: `setAuthorFilterMode` rewrites the note, so
        // explaining before resetting means the explanation is overwritten.
        setAuthorFilterMode("none");
        // The note below supersedes the resolve panel here -- it says the same
        // thing and also says what to do instead, so showing both is just the
        // same sentence twice.
        const panel = $("author-resolve-result");
        if (panel) { panel.hidden = true; panel.innerHTML = ""; }
        const note = $("author-mode-note");
        if (note) {
          note.textContent = "No signed-in user to filter by — this console is not "
            + "authenticated against a deployment. Choose “A specific person” instead.";
        }
      }
    });
    const person = $("author-mode-person");
    if (person) person.addEventListener("click", () => {
      setAuthorFilterMode("custom");
      if (field()) field().focus();
    });
  }

  // Is the thing being worked on right now demo data?
  //
  // Never "was the server started with --demo": that says the server has no
  // deployment configured, and a real URL dropped into such a console runs a
  // real import against a real system. Three things can make this session a
  // demo one, and all of them are about the work: a demo run, a demo URL in
  // the picker, or a demo archive open in the reader.
  //: Whether the archive currently open holds demo data. The SERVER decides
  //: it, from the archive's own name (`gui/archives.is_demo_archive`) -- a
  //: second copy of that rule here is exactly the kind that drifts.
  let demoArchiveOpen = false;
  function demoInView() {
    if (runHasStarted && lastStartBody && lastStartBody.demo) return true;
    if (demoArchiveOpen) return true;
    // The identified source, which is what "working on" means here -- the
    // base URL a dropped URL resolved to, or whatever is typed in the field
    // before anything has been identified.
    const url = identifiedBaseUrl || ($("field-base-url") ? $("field-base-url").value : "");
    return DEMO_HOSTS.some((host) => (url || "").includes(host));
  }

  //: The synthetic deployment's host names -- the same ones the server reads
  //: archive names for (gui/archives.py DEMO_SOURCE_HOSTS).
  const DEMO_HOSTS = ["demo.connections.example", "example.corp"];
  //: The demo's own address. Sent when the demo is what is being looked at,
  //: because the server decides which deployment to read from the address it
  //: is given and from nothing else.
  const DEMO_BASE_URL = "https://demo.connections.example";


  // Does this drop name an archive rather than a deployment page?
  //
  // A `file://` URI is one by definition -- a file manager hands the browser
  // that instead of a path, and nothing on a deployment is ever addressed
  // that way. A `.zip` is one wherever it came from. Everything else is left
  // to the capture flow, which is what a Connections URL is for.
  // A dropped file we can actually open: only a zip carries an archive, and
  // the name is what says so -- browsers disagree about the MIME type, and
  // some send none at all.
  function isZipFile(file) {
    if (!file) return false;
    const name = (file.name || "").toLowerCase();
    return name.endsWith(".zip") || file.type === "application/zip";
  }

  function looksLikeArchiveDrop(raw) {
    const first = (raw || "").split(/[\r\n]+/)[0].trim();
    if (!first) return false;
    const lowered = first.toLowerCase();
    return lowered.startsWith("file://") || lowered.split("?")[0].endsWith(".zip");
  }

  // Read a dropped archive: by path where the browser gave us one, by
  // uploading the bytes where it did not.
  //
  // Nothing is copied for a path -- the archive is read where it lies, which
  // is the point of dropping one from a share rather than filing it first.
  function openDroppedArchive(what) {
    const label = what.file ? what.file.name : what.path;
    notify("Opening " + label + "…", "Reading an archive");
    const request = what.file
      ? fetch("/api/upload-archive", {
          method: "POST",
          headers: { "Content-Type": "application/zip", "X-Archive-Name": what.file.name },
          body: what.file,
        })
      : fetch("/api/open-external", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: what.path }),
        });
    request
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (!ok) {
          // A drop that does not open has to say why. A page that appears to
          // ignore you is the worst of the available behaviours.
          notify((j && j.detail) || "That did not open as an archive.", "Nothing to read");
          return;
        }
        openArchiveName = null;   // it is not one of the filed ones
        setReaderSource(j.name || label);
        demoArchiveOpen = false;
        loadShellIdentity();
        showSection("reader");
        if (typeof enterReaderReal === "function") enterReaderReal(undefined, { reveal: true });
      })
      .catch(() => notify("Could not reach the server.", "Nothing to read"));
  }

  // What the chrome says about this session, stated once. It carries a fact or
  // it is not there: nobody signs in to this console -- it works on a URL,
  // and a deployment may authenticate the requests it makes -- so a permanent
  // "not signed in" described a state that does not exist and taught people to
  // ignore the one place that reports what is being worked on.
  async function loadShellIdentity() {
    const wrap = $("sb-identity");
    const text = $("sb-identity-text");
    if (!wrap || !text) return;
    try {
      // Ask what is open before deciding, so a reload lands on the truth
      // rather than on whatever the last click happened to set.
      try {
        const open = await (await fetch("/api/current-archive")).json();
        demoArchiveOpen = !!(open && open.is_demo_data);
      } catch (_) { /* best-effort: an unreadable answer is not a demo claim */ }
      const base = ($("field-base-url") ? $("field-base-url").value : "").trim();
      if (!base && !demoInView()) {
        // A real archive can be open while the server itself runs in demo
        // mode. Without a deployment URL, asking /api/current-user would
        // incorrectly return the demo principal for that real archive.
        wrap.hidden = true;
        return;
      }
      const query =
        "?base_url=" + encodeURIComponent(base || (demoInView() ? DEMO_BASE_URL : ""));
      const res = await fetch("/api/current-user" + query);
      const j = await res.json().catch(() => ({}));
      if (res.ok && j && (j.name || j.userid)) {
        // A deployment told us who its credentials belong to. Real, and worth
        // saying: it is who the capture will be attributed to.
        wrap.hidden = false;
        text.innerHTML = "Signed in as <b>" + escapeHtml(j.name || j.userid) + "</b>";
        return;
      }
      if (demoInView()) {
        wrap.hidden = false;
        text.textContent = "Demo data";
        return;
      }
      // A deployment that answered and declined is not the same as having
      // nothing to say. Hiding the chip made a refusal -- wrong credentials,
      // an unreachable host, a certificate that would not verify -- look
      // exactly like a console that had not asked, so the one signal that
      // something was wrong disappeared.
      if (base && j && (j.detail || j.status)) {
        wrap.hidden = false;
        const why = j.detail || (j.status === "no_base_url"
          ? "no deployment given"
          : "the deployment did not say who you are");
        text.innerHTML =
          "Not signed in — " + escapeHtml(String(why).slice(0, 160));
        text.title = String(why);
        return;
      }
      wrap.hidden = true;
    } catch (_) {
      wrap.hidden = true;
    }
  }

  async function discoverCommunity(app, container, topicId) {
    discoveredCommunityUuid = null;
    const wrap = $("community-scope-wrap");
    if (wrap) wrap.hidden = true;
    if (!app || !container) return;
    try {
      const topicParam = topicId ? "&topic_id=" + encodeURIComponent(topicId) : "";
      const res = await fetch("/api/community-of?app=" + encodeURIComponent(app) +
        "&container=" + encodeURIComponent(container) + topicParam + baseUrlParam());
      const j = await res.json().catch(() => ({}));
      if (j && j.community_uuid) {
        discoveredCommunityUuid = j.community_uuid;
        if (wrap) wrap.hidden = false;
        const idEl = $("community-scope-id");
        if (idEl) idEl.textContent = "· " + j.community_uuid;
      }
    } catch (_) { /* discovery is best-effort */ }
  }

  async function resolveCurrentUser(force = false) {
    if (!force && (($("field-author") ? $("field-author").value : "").trim())) return;
    try {
      const base = ($("field-base-url") ? $("field-base-url").value : "").trim();
      const query =
        "?base_url=" + encodeURIComponent(base || (demoInView() ? DEMO_BASE_URL : ""));
      const res = await fetch("/api/current-user" + query);
      const user = await res.json().catch(() => ({}));
      if (res.ok && user.userid && $("field-author")) {
        $("field-author").value = user.userid;
        resolvedAuthorUserid = user.userid;
        setAuthorFilterMode("me");
        renderResolveResult(user, user);
      } else if (!res.ok) {
        renderResolveResult(null, user);
      }
    } catch (_) { /* identity discovery is best-effort */ }
  }

  async function resolveAuthorEmail() {
    const val = ($("field-author") ? $("field-author").value : "").trim();
    if (!val) { notify("Enter an email address in the person field first.", "Nothing to look up"); return; }
    const btn = $("author-resolve");
    const label = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "⏳ Resolving…"; }
    try {
      const res = await fetch("/api/resolve-user?email=" + encodeURIComponent(val) + baseUrlParam());
      const j = await res.json().catch(() => ({}));
      if (!res.ok) {
        // Show the URL we tried so the syntax is debuggable.
        renderResolveResult(null, j);
        notify(j.detail || ("Resolve failed (" + res.status + ")."));
        return;
      }
      if ($("field-author")) $("field-author").value = j.userid;
      resolvedAuthorUserid = j.userid || "";
      setAuthorFilterMode("custom");
      renderResolveResult(j, j);
    } catch (_) {
      notify("Resolve isn't reachable — is hcl-serve running?");
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = label; }
    }
  }

  // Show the resolved person as a result: display name, email, user id, and
  // the exact profile URL queried (so the lookup is inspectable). `user` is
  // null on failure (then just the URL is shown for debugging).
  function renderResolveResult(user, payload) {
    const box = $("author-resolve-result");
    if (!box) return;
    const rows = [];
    if (user) {
      rows.push('<div class="rr-row"><span class="rr-k">name</span><span class="rr-v">' + escapeHtml(user.name || "—") + "</span></div>");
      rows.push('<div class="rr-row"><span class="rr-k">email</span><span class="rr-v">' + escapeHtml(user.email || "—") + "</span></div>");
      rows.push('<div class="rr-row"><span class="rr-k">user id</span><span class="rr-v"><b>' + escapeHtml(user.userid || "—") + "</b> ← now in the filter</span></div>");
    }
    // A failure is not a diagnostic dump. Reporting a "queried" row with the
    // raw endpoint and a "status" row carrying whatever the transport said
    // puts "SspiAuth requires the 'requests-negotiate-sspi' package" in front
    // of someone who pressed a button labelled "Only me". Lead with what
    // happened; keep the endpoint available for
    // debugging, but folded away.
    if (!user && payload && payload.detail) {
      rows.push('<div class="rr-msg">' + escapeHtml(payload.detail) + "</div>");
    }
    if (payload && payload.url) {
      rows.push(
        '<details class="rr-tech"><summary>Technical detail</summary>' +
        '<div class="rr-row"><span class="rr-k">queried</span><span class="rr-v"><code>' +
        escapeHtml(payload.url) + "</code></span></div></details>"
      );
    }
    box.innerHTML = rows.join("");
    box.hidden = rows.length === 0;
  }

  // Start a SEARCH-DRIVEN ingest: seed the crawl from the person's search hits
  // (each forum hit → its whole thread, each blog → its entries with comments)
  // instead of walking the whole container. Reuses the normal run pipeline.
  let spCurrentUid = "";
  function ingestSearchResults(uid) {
    uid = (uid || "").trim();
    if (!uid) { notify("Resolve a user id first, then search.", "Nothing to search for"); return; }
    const base = identifiedBaseUrl || (($('field-base-url') ? $('field-base-url').value : '').trim());
    const useCommunity = discoveredCommunityUuid &&
      $("community-scope") && $("community-scope").checked;
    beginRun({
      demo: false,
      base_url: base,
      app: identifiedApp,
      source: "search",
      search_userid: uid,
      community_uuid: useCommunity ? discoveredCommunityUuid : "",
      // Keep the author filter so blog entries reduce to yours (whole entry +
      // comments); forum seeds are already precise (whole threads).
      author: uid,
      min_interval: requestDelaySeconds(),
    });
  }

  // Politeness delay between requests (seconds) from the setup field, for
  // slow/rate-limited deployments. 0 = as fast as the server allows.
  function requestDelaySeconds() {
    const el = $("field-delay");
    const v = el ? parseFloat(el.value) : 0;
    return Number.isFinite(v) && v > 0 ? v : 0;
  }

  // "Preview what search finds": run HCL's Search person-query (read-only) for
  // the value in the author field, treated as a user id, and list the hits so
  // the user can compare the search-based selection with the name filter --
  // neither of which they trust yet. Search is a superset (author OR
  // contributor OR community member), flagged in the panel.
  async function previewSearchIngest() {
    const btn = $("author-search-preview");
    const uid = ($("field-author") ? $("field-author").value : "").trim();
    if (!uid) { notify("Enter a user id or name in the person field first — search matches by user id.", "Nothing to search for"); return; }
    const label = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "⏳ Searching…"; }
    try {
      // Scope server-side to the identified app's component (the verified
      // `scope`/Source expression: wikis:page | blogs:entry | forums:topic),
      // then narrow client-side to the exact container from the dragged URL.
      const scope = { wiki: "wikis:page", blog: "blogs:entry", forum: "forums:topic" }[identifiedApp] || "";
      const container = ($("field-wiki-label") ? $("field-wiki-label").value : "").trim();
      const scopeParam = scope ? "&scope=" + encodeURIComponent(scope) : "";
      // Experimental: pin to the owning community server-side when discovered
      // and the toggle is on (verified sub-component scoping).
      const useCommunity = discoveredCommunityUuid &&
        $("community-scope") && $("community-scope").checked;
      const communityParam = useCommunity
        ? "&community_uuid=" + encodeURIComponent(discoveredCommunityUuid) : "";
      const res = await fetch("/api/search-preview?userid=" + encodeURIComponent(uid) +
        scopeParam + communityParam + baseUrlParam());
      const j = await res.json().catch(() => ({}));
      if (!res.ok) {
        // Show the URL we queried even on failure, so the syntax is debuggable.
        showSearchPreview(uid, [], 0, j.url, j.detail, container, null);
        return;
      }
      const all = j.results || [];
      // Reduce to the specific blog/forum/wiki: keep hits whose browser URL
      // carries the container id. If none match (unexpected URL form), fall
      // back to all so nothing is silently hidden.
      let shown = all, reducedTo = "";
      if (container) {
        const inContainer = all.filter((r) => (r.url || "").indexOf(container) !== -1);
        if (inContainer.length) { shown = inContainer; reducedTo = container; }
      }
      showSearchPreview(uid, shown, all.length, j.url, null, reducedTo, j.total);
    } catch (_) {
      notify("Search preview isn't reachable — is hcl-serve running?");
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = label; }
    }
  }

  function showSearchPreview(uid, results, count, queriedUrl, errorDetail, reducedTo, totalResults) {
    let ov = $("search-preview");
    if (!ov) {
      ov = document.createElement("div");
      ov.id = "search-preview";
      ov.className = "pdf-viewer";
      ov.innerHTML =
        '<div class="pv-bar"><span class="pv-title" id="sp-title"></span>' +
        '<span class="spacer"></span>' +
        '<button class="btn primary" id="sp-ingest" title="Ingest these search results — each forum hit as a whole thread, each blog entry with its comments">' + icon('download') + ' Ingest these (whole threads)</button>' +
        '<button class="btn" id="sp-close">✕ Close</button></div>' +
        '<div class="fv-list" id="sp-list"></div>';
      document.body.appendChild(ov);
      const close = () => { ov.style.display = "none"; };
      $("sp-ingest").addEventListener("click", () => { close(); ingestSearchResults(spCurrentUid); });
      $("sp-close").addEventListener("click", close);
      ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && ov.style.display === "flex") close();
      });
    }
    spCurrentUid = uid;
    // This preview only ever fetches page 1 (a quick eyeball, not the ingest
    // itself) -- when the feed's own `openSearch:totalResults` says there's
    // more than that one page, say so explicitly instead of implying `count`
    // is everything. The actual ingest (`_search_seeds`, server-side) DOES
    // page through the full result set, so this is a preview-only caveat.
    const moreThanShown = totalResults != null && totalResults > count;
    const totalNote = moreThanShown
      ? " — " + totalResults.toLocaleString() + " total match; ingest fetches all of them, this preview only shows the first " + count
      : "";
    $("sp-title").textContent = reducedTo
      ? results.length + " of " + count + " search hits are in this container (“" + reducedTo + "”)" + totalNote
      : "Search finds " + count + " item" + (count === 1 ? "" : "s") +
        " for “" + uid + "” (superset: authored, contributed, or member)" + totalNote;
    // Always show the exact URL queried so the syntax is inspectable, plus any
    // error detail (e.g. a rejected query).
    const header =
      (errorDetail ? '<div class="fv-row" style="border-left-color:var(--crit)"><b>' + escapeHtml(errorDetail) + "</b></div>" : "") +
      (queriedUrl ? '<div class="fv-row"><span class="rr-k">queried</span> <code style="word-break:break-all">' + escapeHtml(queriedUrl) + "</code></div>" : "");
    $("sp-list").innerHTML = header + (results.length
      ? results.map((r) =>
          '<div class="fv-row"><div class="fv-url">' + escapeHtml(r.title || "(untitled)") + '</div>' +
          '<div class="fv-meta"><span class="rloc">' + escapeHtml(r.component || "?") + '</span>' +
          '<span class="fv-err">' + escapeHtml((r.author || "") + (r.author_userid ? " · " + r.author_userid : "")) + '</span></div>' +
          (r.url ? '<div class="fv-meta"><a href="' + escapeHtml(r.url) + '" target="_blank" rel="noopener noreferrer">open on HCL ↗</a></div>' : "") +
          (r.via ? '<div class="fv-meta"><code style="word-break:break-all">via: ' + escapeHtml(r.via) + "</code></div>" : "") +
          '</div>')
        .join("")
      : '<div class="fv-row">Search returned nothing for that user id. Resolve your email to a user id (button above), or use the id from the authors picker after an import.</div>');
    ov.style.display = "flex";
  }

  function identifyUrl(url) {
    url = (url || "").trim();
    if (!url) return;
    // Identifying is itself a round trip to the deployment; the gate starts
    // here rather than at component discovery so Start is never live against
    // a URL we have not looked at yet.
    setAnalysing(true, "Identifying this URL");
    fetch("/api/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    })
      .then((res) => res.json())
      .then((result) => {
        applyIdentifyResult(result, url);
        // A community goes on to discover its components, which re-arms the
        // gate; anything else is fully analysed at this point.
        if (!identifiedCommunity) setAnalysing(false);
      })
      .catch((err) => {
        setAnalysing(false);
        if (isNetworkError(err)) {
          setSetupNote("warn", "Couldn't reach the server to identify that URL.");
        } else {
          // Surface the REAL error instead of blaming the network.
          console.error("identify: error handling the response", err);
          setSetupNote("warn", "Something went wrong handling that URL: " +
            (err && err.message ? err.message : err) + " (see the browser console).");
        }
      });
  }

  // fillAndIdentify: the demo chips' own click handler
  // -- fills the URL input and identifies it, exactly like a paste, but
  // NEVER starts a run. Chips are drag/drop-or-click-to-identify only;
  // #run-demo (the demo pipeline, beginRun({demo:true})) stays the only
  // run trigger in demo mode.
  function fillAndIdentify(url) {
    const urlInput = $("setup-url-input");
    if (urlInput) urlInput.value = url;
    identifyUrl(url);
  }

  function firstUrlFromDropText(text) {
    return String(text || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find((line) => line && !line.startsWith("#")) || "";
  }

  function beginRun(startBody) {
    lastStartBody = startBody;
    runHasStarted = true;
    // How many communities this run covers, taken from the request the console
    // is about to send -- the only place that knows before the first event
    // arrives, and the reason the tree can group the FIRST community rather
    // than discovering halfway through that it should have.
    const requested = startBody.communities || [];
    liveCommunityCount = requested.length;
    liveCommunityByComponent = new Map();
    requested.forEach((community) => {
      (community.components || []).forEach((value) => {
        liveCommunityByComponent.set(value, { uuid: community.uuid, title: community.title });
      });
    });
    showConsoleScreen();
    const stopBtn = document.getElementById("stop-btn");
    if (stopBtn) stopBtn.style.display = "";  // show during a run
    fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(startBody),
    })
      .then(() => startLive())
      .catch((err) => {
        if (isNetworkError(err)) {
          showUnreachableBanner("Couldn't reach the server to start the demo — check that hcl-serve is running, then try Restart.");
        } else {
          console.error("start: error after POST /api/start", err);
          showUnreachableBanner("Something went wrong starting the run: " +
            (err && err.message ? err.message : err) + " (see the browser console).");
        }
      });
  }

  // Mode grid: clicking a mode button updates identifiedApp + scope, highlights selection.
  function wireModeGrid() {
    const btns = document.querySelectorAll(".mode-btn");
    btns.forEach((btn) => {
      btn.addEventListener("click", () => {
        btns.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        identifiedApp = btn.dataset.app || "wiki";
        identifiedScope = btn.dataset.scope || "all";  // keep scope in sync with mode selection
        // Re-fetch the count for the new scope (e.g. whole-forum → topic count,
        // single-thread → reply count).
        if (lastIdentifiedUrl) {
          const allCountEl = document.getElementById("size-all-n");
          if (allCountEl) {
            allCountEl.textContent = "\u2026";
            fetch("/api/feed-info?url=" + encodeURIComponent(lastIdentifiedUrl) + "&scope=" + encodeURIComponent(identifiedScope))
              .then((r) => r.json())
              .then((j) => { allCountEl.textContent = j.total != null ? j.total.toLocaleString() : "?"; })
              .catch(() => { allCountEl.textContent = "?"; });
          }
        }
        // Scope hint shown in the target label
        const scope = btn.dataset.scope || "all";
        const labels = {
          wiki:  { all: "Wiki label",        sub: "Wiki label (root page)", single: "Wiki label (page)" },
          blog:  { all: "Blog handle / UUID", single: "Blog handle / UUID" },
          forum: { all: "Forum UUID",         single: "Forum UUID" },
        };
        const lbl = (labels[identifiedApp] || {})[scope] || "ID";
        const el = document.getElementById("field-target-label");
        if (el) el.textContent = lbl;
        // For single-item modes, pre-set to a preview of 1 so the user
        // knows exactly what they'll get. For whole-app modes, leave
        // the size chooser at its current setting (default: "all").
        const sizeCountField = document.getElementById("size-count");
        if (sizeCountField) {
          if (scope === "single") {
            setSizeMode("preview");
            sizeCountField.value = "1";
          }
        }
      });
    });
  }

  function wireSetupScreen() {
    const urlInput = $("setup-url-input");

    // Manual fallback: skip the URL and pick the app/scope by hand.
    [$("manual-setup"), $("manual-setup-link")].forEach((button) => {
      if (button) button.addEventListener("click", enterManualSetup);
    });

    // Identifying is debounced off typing, but the field now sits beside an
    // explicit button: pasting and pressing it (or Enter) should not wait for
    // a timer, and the action being visible is what makes the field read as a
    // control rather than a decorated placeholder.
    const identifyNow = () => {
      const value = (urlInput.value || "").trim();
      if (value) identifyUrl(value);
    };
    const identifyBtn = $("identify-url");
    if (identifyBtn) identifyBtn.addEventListener("click", identifyNow);
    urlInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); identifyNow(); }
    });

    // THE WHOLE PAGE is the drop target. The dedicated rectangle and the
    // field were two affordances for one verb, and the rectangle was dead
    // space whenever nobody was dragging -- so the document-level handler,
    // which already existed as a safety net, is now the design. The source
    // panel shows a veil on dragenter, which is the feedback a permanent
    // rectangle would give by simply existing.
    //
    // `dragover` must call preventDefault() or the browser navigates to (or
    // downloads) the dropped link instead of firing `drop`.
    // Every zone that should light up while something is being dragged. The
    // whole page is the drop target, so these are affordances rather than
    // targets -- and arming only the capture screen's panel meant a drag over
    // Archives showed nothing at all, which reads as the page ignoring you.
    const dropTargets = () => [$("setup-source"), $("archive-drop")].filter(Boolean);
    const arm = () => dropTargets().forEach((el) => el.classList.add("drop-armed"));
    const disarm = () => dropTargets().forEach((el) => el.classList.remove("drop-armed"));
    let dragDepth = 0;
    document.addEventListener("dragenter", (e) => {
      e.preventDefault();
      // Counted, not toggled: dragging across a child element fires
      // dragleave on the parent, and a naive toggle flickers the veil away
      // while the pointer is still over the page.
      dragDepth += 1;
      arm();
    });
    document.addEventListener("dragover", (e) => e.preventDefault());
    document.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) disarm();
    });
    document.addEventListener("drop", (e) => {
      e.preventDefault(); // never let the browser navigate to the dropped link
      dragDepth = 0;
      disarm();
      const dt = e.dataTransfer;
      const raw = dt ? (dt.getData("text/uri-list") || dt.getData("text/plain") || "") : "";

      // An archive dropped on the console means READ THIS -- the opposite of a
      // deployment URL, which means crawl that. Told apart by what was
      // dropped, so there is no second target to aim at and no mode to be in.
      //
      // The TEXT decides, and it is read first. Dragging a link hands the page
      // a `text/uri-list` AND, on Windows and some Linux desktops, a shortcut
      // FILE -- so looking at `files` first made a dropped community URL
      // arrive as a file and be refused for not being a zip, which is a
      // correct answer to a question nobody asked.
      if (looksLikeArchiveDrop(raw)) {
        openDroppedArchive({ path: raw.split(/[\r\n]+/)[0].trim() });
        return;
      }

      const url = firstUrlFromDropText(raw);
      if (url) { urlInput.value = url; identifyUrl(url); return; }

      if (dt && dt.files && dt.files.length) {
        // No link in the drop, so this really is a file: the browser has the
        // bytes and no path, which is what anything dragged out of a OneDrive
        // or SharePoint folder looks like.
        const file = dt.files[0];
        if (isZipFile(file)) { openDroppedArchive({ file }); return; }
        notify(
          escapeHtml(file.name) + " is not an archive (a folder or a .zip).",
          "Nothing to read"
        );
      }
    });

    // The same three routes without a mouse gesture: a file picker, and a
    // field taking a path or a link. A drop zone that only accepts drops
    // excludes everyone who does not drag.
    const chooser = $("archive-choose");
    const fileInput = $("archive-file");
    if (chooser && fileInput) {
      chooser.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", () => {
        if (fileInput.files && fileInput.files.length) {
          openDroppedArchive({ file: fileInput.files[0] });
          fileInput.value = "";   // so choosing the same file twice still fires
        }
      });
    }
    const pathField = $("archive-path");
    const openPath = $("archive-open-path");
    if (pathField && openPath) {
      const go = () => {
        const value = pathField.value.trim();
        if (value) openDroppedArchive({ path: value });
      };
      openPath.addEventListener("click", go);
      pathField.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    }

    let debounceTimer = null;
    urlInput.addEventListener("input", (e) => {
      clearTimeout(debounceTimer);
      const value = e.target.value;
      debounceTimer = setTimeout(() => identifyUrl(value), 200);
    });
    urlInput.addEventListener("paste", (e) => {
      const pasted = (e.clipboardData && e.clipboardData.getData("text")) || "";
      if (pasted.trim()) setTimeout(() => identifyUrl(pasted), 0);
    });

    // The size chooser (All / Preview[N]) -- purely a toggle of
    // sizeMode, never itself a trigger for beginRun.
    setSizeMode("all");  // sync button active-class with the initial sizeMode value
    $("size-all").addEventListener("click", () => { setSizeMode("all"); updateEstimate(); });
    $("size-preview").addEventListener("click", () => { setSizeMode("preview"); updateEstimate(); });
    ["size-count", "field-delay"].forEach((id) => {
      const input = $(id);
      if (input) input.addEventListener("input", updateEstimate);
    });
    if ($("author-search-preview")) {
      $("author-search-preview").addEventListener("click", previewSearchIngest);
    }
    if ($("author-resolve")) {
      $("author-resolve").addEventListener("click", resolveAuthorEmail);
    }
    if ($("author-me")) {
      $("author-me").addEventListener("click", () => resolveCurrentUser(true));
    }
    if ($("field-author")) {
      $("field-author").addEventListener("input", () => {
        // Typed, so no longer a resolved id: the run falls back to reading
        // the selected forums in full rather than trusting a guess.
        resolvedAuthorUserid = "";
        setAuthorFilterMode("custom");
      });
    }

    // #start-ingest is the ONLY explicit start path in this screen -- no
    // identify/paste/drop handler above may call beginRun (the old
    // #start-import, which did, is superseded by this size-chooser flow).
    // The body /api/start receives, built in one place so the command echoed
    // below it is a serialization of the very same object -- an echo that can
    // drift from what Start actually does is worse than no echo.
    function startBody() {
      const target = $("field-wiki-label").value.trim() || null;
      const max = sizeMode === "all" ? null
        : (parseInt($("size-count").value, 10) || 10);
      const authorField = $("field-author");
      const author = authorField ? (authorField.value.trim() || null) : null;
      return {
        base_url: identifiedBaseUrl || $("field-base-url").value.trim() || null,
        auth_mode: $("field-auth-mode").value,
        app: resultAppForStart(),
        wiki_label: identifiedApp === "wiki" ? target : null,
        blog_handle: identifiedApp === "blog" ? target : null,
        forum_uuid: identifiedApp === "forum" ? target : null,
        community_uuid: identifiedCommunityUuid,
        community_components: identifiedCommunity ? selectedCommunityComponents() : [],
        // The set. Sent alongside the single-community fields above so a
        // server that has not learned this shape still runs the first one;
        // a server that has ignores those in favour of this.
        communities: identifiedCommunity ? communitiesForRun() : [],
        // Names the archive directory. The console already knows what this
        // run is called -- the community title, or the identified target --
        // and without passing it the directory can only record where and
        // when, never what.
        target_label: identifiedTargetLabel || target || null,
        // Scope the run to a single post/thread when the URL named one, so the
        // archive holds only that entry (not the whole blog/forum).
        scope: identifiedScope,
        entry_slug: identifiedApp === "blog" ? identifiedEntrySlug : null,
        topic_id: identifiedApp === "forum" ? identifiedTopicId : null,
        max_entries: max,
        // "Capture everything I authored": crawl the whole app, expose only
        // content this user authored/participated in (each with its full chain).
        author: author,
        // The same person, as a value Search can be asked about -- sent only
        // when the deployment itself gave it to us. It turns a community
        // capture's forums from "read all of them and prune" into "ask which
        // threads this person is in, then read those". Empty means the
        // full walk, which is slower and always correct.
        search_userid: author && author === resolvedAuthorUserid ? resolvedAuthorUserid : "",
        min_interval: requestDelaySeconds(),
        demo: false,
        // Archive mode: what this run is adding to, what it re-checks, and --
        // in the demo -- how much of the fake deployment exists by now.
        into: archiveSubject,
        // WHICH components this update is for. The console has always known
        // -- the ledger is where you choose them -- and sent only `recheck`,
        // so the server had no scope at all and an update walked the whole
        // deployment.
        archive_components: archiveSubject
          ? ledgerSelections().update.concat(ledgerSelections().add)
              .map((row) => ({ kind: row.kind, id: row.id }))
              .filter((row) => row.kind && row.id)
          : [],
        recheck_comments: ledgerSelections().recheck,
        demo_wave: demoWave || null,
      };
    }

    // ---- the CLI echo -------------------------------------------------
    // Assembling these switches by hand is tedious and easy to get wrong, so
    // the console writes the command for whatever is currently selected. Built
    // from startBody(), the same object Start ingest posts.

    function shellQuote(value) {
      const text = String(value);
      // Leave plain tokens bare; quote anything a shell would otherwise split
      // or interpret. Single quotes are the safe wrapper, with the usual
      // '"'"' dance for an embedded one.
      if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(text)) return text;
      return "'" + text.replace(/'/g, "'\"'\"'") + "'";
    }

    function cliCommandFor(body) {
      if (!body || body.demo) return null;
      const parts = ["connections-export", "crawl"];
      const url = lastIdentifiedUrl || null;
      if (url) parts.push(shellQuote(url));

      const components = body.community_components || [];
      if (components.length) {
        for (const component of components) {
          parts.push("--component", shellQuote(component));
        }
      } else if (!url) {
        // Nothing identified and nothing selected: there is no honest command
        // to show, so show none rather than one that would not run.
        return null;
      }

      if (!url && body.base_url) parts.push("--base-url", shellQuote(body.base_url));
      if (body.author) parts.push("--author", shellQuote(body.author));
      if (body.max_entries) parts.push("--max-entries", String(body.max_entries));
      if (body.target_label) parts.push("--target-label", shellQuote(body.target_label));
      return parts.join(" ");
    }

    refreshCliEcho = updateCliEcho;

    function updateCliEcho() {
      const box = $("cli-echo");
      const code = $("cli-command");
      if (!box || !code) return;
      let command = null;
      try {
        command = cliCommandFor(startBody());
      } catch (err) {
        command = null;  // mid-edit state; just hide rather than show nonsense
      }
      if (!command) {
        box.hidden = true;
        code.textContent = "";
        return;
      }
      code.textContent = command;
      box.hidden = false;
    }

    if ($("cli-copy")) {
      $("cli-copy").addEventListener("click", () => {
        const text = ($("cli-command") || {}).textContent || "";
        if (!text) return;
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(
            () => notify("Command copied.", "Copied"),
            () => notify("Could not copy — select the text and copy manually.", "Copy failed")
          );
        } else {
          notify("Select the text and copy manually.", "Copy");
        }
      });
    }

    // Anything that changes what would be captured also changes the command.
    for (const id of ["field-wiki-label", "field-base-url", "field-author", "size-count", "field-auth-mode"]) {
      const el = $(id);
      if (el) el.addEventListener("input", updateCliEcho);
      if (el) el.addEventListener("change", updateCliEcho);
    }
    for (const id of ["size-all", "size-preview"]) {
      const el = $(id);
      if (el) el.addEventListener("click", () => setTimeout(updateCliEcho, 0));
    }
    document.addEventListener("change", (event) => {
      const target = event.target;
      if (target && target.matches && target.matches("#community-components input[type=checkbox]")) {
        updateCliEcho();
      }
    });

    $("start-ingest").addEventListener("click", () => {
      if (identifiedCommunity) {
        const selected = selectedCommunityComponents();
        if (!selected.length) {
          notify("Select at least one community component before starting the ingest.", "Nothing selected");
          return;
        }
        setSetupNote("ok", "Selected community components: " + communitySelectionLabel());
      }
      beginRun(startBody());
    });
    const addCommunityButton = $("add-community");
    if (addCommunityButton) {
      const addFromInput = () => {
        const field = $("add-community-url");
        const status = $("add-community-status");
        const uuid = communityUuidFromInput(field ? field.value : "");
        if (!uuid) {
          // Said plainly rather than silently ignored: a URL this cannot read
          // is the one case where the user has to fetch the uuid themselves.
          if (status) status.textContent = "Paste a community URL containing communityUuid=, or the uuid itself.";
          return;
        }
        if (status) status.textContent = "Reading its components…";
        addCommunityGroup(uuid, null).then((added) => {
          if (status) status.textContent = added ? "" : "That community is already in this run.";
          if (added && field) field.value = "";
        });
      };
      addCommunityButton.addEventListener("click", addFromInput);
      const addField = $("add-community-url");
      if (addField) {
        addField.addEventListener("keydown", (event) => {
          if (event.key === "Enter") { event.preventDefault(); addFromInput(); }
        });
      }
    }

    $("run-demo").addEventListener("click", () => {
      const authorField = $("field-author");
      const author = authorField ? (authorField.value.trim() || null) : null;
      beginRun({ demo: true, author: author });
    });

    // "Live browser PDF" on setup screen — passes base_url directly.
    const livePdfSetupBtn = document.getElementById("start-live-pdf");
    if (livePdfSetupBtn) {
      livePdfSetupBtn.addEventListener("click", async () => {
        const url = ($("field-base-url") || {}).value || "";
        const label = livePdfSetupBtn.textContent;
        livePdfSetupBtn.disabled = true;
        livePdfSetupBtn.textContent = "⏳ Rendering…";
        const apiUrl = "/api/live-pdf?scope=all" + (url ? "&url=" + encodeURIComponent(url) : "");
        try {
          const res = await fetch(apiUrl);
          if (!res.ok) {
            let msg = "Live PDF failed.";
            try { const j = await res.json(); if (j.detail) msg = j.detail; } catch(_) {}
            notify(msg); return;
          }
          const blob = await res.blob();
          const objUrl = URL.createObjectURL(blob);
          const a = document.createElement("a"); a.href = objUrl; a.download = "live-export.pdf";
          document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(objUrl);
        } catch(_) { notify("Live PDF request failed."); }
        finally { livePdfSetupBtn.disabled = false; livePdfSetupBtn.textContent = label; }
      });
    }
  }

  // ── Dark / light / auto theme management ───────────────────────────────
  const THEME_KEY = "hcl-export-theme";
  const THEME_LABELS = { auto: ["theme", "Auto"], dark: ["theme", "Dark"], light: ["theme", "Light"] };
  const THEME_CYCLE  = { auto: "dark", dark: "light", light: "auto" };

  // Each theme maps to [icon name, word]. They go into the button's
  // `.ti-icon`/`.sb-label` spans separately so the sidebar's existing
  // collapse rule (`#sidebar.collapsed .sb-label { display: none }`) can
  // shrink it to icon-only in rail mode, same as every nav item. Previously
  // the pair was one string split on its first space, which only worked
  // while the icon was a single emoji character.
  function setThemeButtonText(btn, theme) {
    const [iconName, word] = THEME_LABELS[theme] || THEME_LABELS.auto;
    const iconEl = btn.querySelector(".ti-icon");
    const labelEl = btn.querySelector(".sb-label");
    if (iconEl && labelEl) { iconEl.innerHTML = icon(iconName); labelEl.textContent = word; }
    else btn.innerHTML = icon(iconName) + " " + word;
  }

  function applyTheme(theme) {
    const root = document.documentElement;
    if (theme === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    const btn = document.getElementById("theme-toggle");
    if (btn) setThemeButtonText(btn, theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch(_) {}
    // Re-render any open reader page so sandboxed iframes pick up the new theme.
    if (realPageId && realWikiId) openReaderPageReal(realWikiId, realPageId);
    else if (realPostId && realBlogId) openReaderPostReal(realBlogId, realPostId);
    else if (realTopicId && realForumId) openReaderTopicReal(realForumId, realTopicId);
  }

  // The toggle lives in the persistent sidebar markup (console.html) now,
  // not injected into the reader header -- it's reachable from every
  // section, not just the reader, and there's exactly one #theme-toggle.
  function initTheme() {
    let theme = "auto";
    try { theme = localStorage.getItem(THEME_KEY) || "auto"; } catch(_) {}
    if (!THEME_LABELS[theme]) theme = "auto";
    applyTheme(theme);
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.addEventListener("click", () => {
        const cur = document.documentElement.getAttribute("data-theme") || "auto";
        applyTheme(THEME_CYCLE[cur] || "auto");
      });
    }
  }

  // ---------- Overview (newcomer landing): the CTAs just route into the
  // existing sections/flows via showSection() -- no new state of their
  // own. #ov-recent (recent-archives list) is filled in below, so
  // returning to a past export never requires the CLI. ----------
  function wireOverview() {
    document.getElementById("ov-start").addEventListener("click", () => showSection("select"));
    document.getElementById("ov-archives").addEventListener("click", () => showSection("archives"));
    document.getElementById("ov-demo").addEventListener("click", () => {
      showSection("select");
      // "Try the demo" is an explicit opt-in: reveal the toggle itself (in
      // case the /api/demo-urls probe hasn't resolved yet) and open the
      // chips directly, rather than relying on a synthetic click that only
      // does something once #demo-mode is already unhidden.
      const demoModeBtn = document.getElementById("demo-mode");
      if (demoModeBtn) demoModeBtn.hidden = false;
      openDemoChips();
    });
  }

  // Attach the reader/model to a previously-run archive (`POST
  // /api/open-archive`), then jump straight into the real reader on it --
  // the same entry point a live run's "Open reader" button uses.
  function openArchive(name, displayName, button) {
    // The card button is structured (title / name / facts spans), so the
    // busy state swaps only the title line rather than flattening the card.
    const labelEl = button ? (button.querySelector(".ra-title") || button) : null;
    const originalLabel = labelEl ? labelEl.textContent : "";
    const overlay = $("archive-open-overlay");
    const overlayTitle = $("archive-open-title");
    if (overlay) overlay.hidden = false;
    if (overlayTitle) overlayTitle.textContent = displayName || name;
    if (button) {
      button.disabled = true;
      labelEl.textContent = "Opening archive…";
    }
    fetch("/api/open-archive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    })
      .then((r) => {
        if (r.ok) return r.json();
        // Surface the server's reason (e.g. an empty/partial archive → 422)
        // instead of switching to a reader that would spin forever on a
        // pending model.
        return r.json().catch(() => ({})).then((j) => {
          // 404/not_found means the presented entry is stale (the archive was
          // removed since the list was loaded). Refresh the list so the dead
          // entry disappears, and say so plainly rather than a generic error.
          if (r.status === 404 || j.status === "not_found") {
            loadRecentArchives();
            throw new Error(
              "That archive is no longer available — it may have been removed. " +
              "The list has been refreshed."
            );
          }
          throw new Error(j.detail || "Couldn't open that archive.");
        });
      })
      .then(() => {
        // Opening a *different* archive must not show whatever was cached
        // from a previous one -- enterReaderReal() short-circuits on a
        // truthy REAL_MODEL, so drop the cache (and any in-flight
        // poll/live-refresh timers and the current selection) first, forcing
        // a fresh /api/model fetch for the newly-opened archive.
        stopRealPolling();
        stopLiveRefresh();
        REAL_MODEL = null;
        openArchiveName = name;
        clearReaderSelection();
        showSection("reader");
        setReaderSource(displayName || name);
        // Opening an archive changes what is in view, which is what the
        // sidebar chip reports. A real archive opened in a `serve --demo`
        // console is not a demo session.
        loadShellIdentity();   // what is in view changed; the chip follows
        if (typeof enterReaderReal === "function") enterReaderReal(undefined, { reveal: true });
      })
      .catch((e) => notify(e && e.message ? e.message : "Couldn't open that archive."))
      .finally(() => {
        if (overlay) overlay.hidden = true;
        if (button) {
          button.disabled = false;
          labelEl.textContent = originalLabel;
        }
      });
  }

  // #24: every run (especially every demo) creates a new timestamped
  // archive that persists forever, so the recent list can grow without
  // bound. Cap what the Overview actually renders so it stays usable --
  // the full history is still on disk and still reachable via the CLI/
  // Settings' archives dir, just not all dumped into one scrolling list.
  const RECENT_ARCHIVES_LIMIT = 200;

  // Raw ISO-8601 ("2020-09-13T14:26:40Z") is a machine format shown to people.
  // Render it readably, keeping the exact value in `title=` so the precise
  // timestamp is still one hover away, and falling back to the original
  // string for anything that does not parse.
  function whenText(value) {
    if (!value) return "";
    const d = new Date(value);
    if (isNaN(d.getTime())) return String(value);
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
      + " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  const whenHtml = (value, cls) => (value
    ? '<span' + (cls ? ' class="' + cls + '"' : "") + ' title="' + escapeAttr(value) + '">'
      + escapeHtml(whenText(value)) + "</span>"
    : "");

  // ---------- Dialogs ----------
  // `showDialog` resolves true/false; with `confirmWord` the confirm button
  // stays disabled until that exact word is typed. Native alert()/prompt()
  // were OS chrome -- unthemeable, and for a delete they asked the user to
  // transcribe a 40-character slug with no view of what was at stake.
  let dialogClose = null;

  function showDialog({ title, bodyHtml, confirmLabel = "Confirm", confirmWord = null,
                        confirmHint = null, danger = true, cancelLabel = "Cancel" }) {
    const scrim = $("modal-scrim");
    if (!scrim) {  // markup missing: never silently skip a destructive confirm
      return Promise.resolve(confirmWord ? false : window.confirm(title));
    }
    $("modal-title").textContent = title;
    $("modal-body").innerHTML = bodyHtml || "";
    const wrap = $("modal-confirm-wrap");
    const input = $("modal-confirm-input");
    const ok = $("modal-ok");
    const cancel = $("modal-cancel");
    ok.textContent = confirmLabel;
    ok.className = "btn " + (danger ? "danger" : "primary");
    cancel.textContent = cancelLabel;
    wrap.hidden = !confirmWord;
    input.value = "";
    if (confirmWord) {
      $("modal-confirm-label").textContent = confirmHint || ("Type " + confirmWord + " to confirm");
      ok.disabled = true;
    } else {
      ok.disabled = false;
    }
    scrim.hidden = false;
    (confirmWord ? input : ok).focus();

    return new Promise((resolve) => {
      const finish = (value) => {
        scrim.hidden = true;
        input.oninput = null; ok.onclick = null; cancel.onclick = null;
        scrim.onmousedown = null; document.removeEventListener("keydown", onKey);
        dialogClose = null;
        resolve(value);
      };
      dialogClose = () => finish(false);
      const onKey = (e) => {
        if (e.key === "Escape") finish(false);
        else if (e.key === "Enter" && !ok.disabled && document.activeElement !== cancel) finish(true);
      };
      if (confirmWord) {
        input.oninput = () => { ok.disabled = input.value.trim() !== confirmWord; };
      }
      ok.onclick = () => finish(true);
      cancel.onclick = () => finish(false);
      scrim.onmousedown = (e) => { if (e.target === scrim) finish(false); };
      document.addEventListener("keydown", onKey);
    });
  }

  // The alert() replacement. Keeps the message, adds a title so the dialog
  // says what KIND of thing happened, and renders newlines as breaks -- the
  // old alerts embedded "\n\n" for paragraphs the OS dialog rendered as-is.
  function notify(message, title) {
    const text = escapeHtml(String(message == null ? "" : message)).replace(/\n/g, "<br>");
    return showMessage(title || "Something went wrong", "<p>" + text + "</p>");
  }

  // A message with a single dismiss -- the alert() replacement.
  function showMessage(title, bodyHtml) {
    return showDialog({ title, bodyHtml, confirmLabel: "OK", danger: false,
                        cancelLabel: "Close" });
  }

  // ---------- Archives panel: bulk selection ----------
  // Typing each archive's full name protects against deleting the WRONG one,
  // but does not scale to the pile repeated attempts at a target produce.
  // Selection replaces it for batches: pick a range, then confirm by typing
  // how many are selected -- a number read off the screen, so it proves the
  // scope was seen, and it changes every time so it never becomes automatic.
  const archiveSelection = new Set();
  let archivesOnPage = [];   // every archive the server returned, not just rendered
  let lastCheckedIndex = null;

  function updateSelectionSummary() {
    const toolbar = $("archive-toolbar");
    if (toolbar) toolbar.hidden = !archivesOnPage.length;
    const count = archiveSelection.size;
    const summary = $("selection-summary");
    if (summary) {
      const rendered = document.querySelectorAll(".archive-select").length;
      const hidden = count - document.querySelectorAll(".archive-select:checked").length;
      summary.textContent = count
        ? count + " selected" + (hidden > 0 ? " (" + hidden + " not shown below)" : "")
        : "Nothing selected.";
      if (!count && archivesOnPage.length > rendered) {
        summary.textContent = "Nothing selected. Selection helpers cover all "
          + archivesOnPage.length + " archives, not just the " + rendered + " shown.";
      }
    }
    const del = $("delete-selected");
    if (del) {
      del.disabled = count === 0;
      del.textContent = count ? "Delete selected (" + count + ")" : "Delete selected";
    }
    document.querySelectorAll(".recent-archive-row").forEach((row) => {
      const box = row.querySelector(".archive-select");
      if (box) row.classList.toggle("is-selected", archiveSelection.has(box.dataset.name));
    });
  }

  function setSelected(name, on) {
    if (on) archiveSelection.add(name); else archiveSelection.delete(name);
  }

  function syncCheckboxes() {
    document.querySelectorAll(".archive-select").forEach((box) => {
      box.checked = archiveSelection.has(box.dataset.name);
    });
    updateSelectionSummary();
  }

  // Filter the rendered rows by name or by what the archive contains (its
  // summary line), so a long list stays navigable. Rows are hidden, never
  // removed, so a selection survives filtering.
  function applyArchiveFilter() {
    const box = $("archive-filter");
    const query = (box ? box.value : "").trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll(".recent-archive-row").forEach((row) => {
      const hit = !query || row.textContent.toLowerCase().includes(query);
      row.hidden = !hit;
      if (hit) shown += 1;
    });
    const empty = $("archive-filter-empty");
    if (empty) empty.hidden = shown !== 0 || !query;
  }

  function wireArchiveFilter() {
    const box = $("archive-filter");
    if (box) box.addEventListener("input", applyArchiveFilter);
  }

  function wireSelectionToolbar() {
    $("select-superseded")?.addEventListener("click", () => {
      // Operates over every archive the server listed, not only the rendered
      // rows -- that is the whole point when there are hundreds.
      const superseded = archivesOnPage.filter((a) => a.is_superseded);
      if (!superseded.length) {
        notify("A run counts as superseded when a later run captured the same content. Archives without a stored summary are never included — run tools/backfill_archive_summaries.py to characterise older ones.", "No superseded runs found");
        return;
      }
      superseded.forEach((a) => archiveSelection.add(a.name));
      syncCheckboxes();
    });
    $("clear-selection")?.addEventListener("click", () => {
      archiveSelection.clear();
      syncCheckboxes();
    });
    $("delete-selected")?.addEventListener("click", async () => {
      const names = Array.from(archiveSelection);
      if (!names.length) return;
      const preview = names.slice(0, 6).map((n) => "<li class=\"mono\">" + escapeHtml(n) + "</li>").join("");
      const more = names.length > 6 ? "<li>…and " + (names.length - 6) + " more</li>" : "";
      const ok = await showDialog({
        title: "Delete " + names.length + " archive" + (names.length === 1 ? "" : "s") + "?",
        bodyHtml: "<p>This permanently deletes the selected archives. It cannot be undone.</p>"
          + "<ul>" + preview + more + "</ul>",
        confirmLabel: "Delete " + names.length,
        confirmWord: String(names.length),
        confirmHint: "Type " + names.length + " to confirm you have seen how many this is",
      });
      if (!ok) return;
      const answer = String(names.length);
      const button = $("delete-selected");
      const label = button.textContent;
      button.disabled = true;
      deleteArchives(names, answer, (done, total) => {
        button.textContent = "Deleting… " + done + " / " + total;
      }).then((summary) => {
        if (summary && summary.failed && summary.failed.length) {
          notify("Deleted " + summary.deleted + ", but " + summary.failed.length + " could not be removed.", "Deleted with problems");
        }
        archiveSelection.clear();
        loadRecentArchives();
      }).catch((e) => notify(e.message || "Could not delete the selected archives."))
        .then(() => { button.disabled = false; button.textContent = label; });
    });
  }

  // Streams POST /api/delete-archives. Same NDJSON contract as the demo bulk
  // delete: a record per archive, then a `done` summary.
  function deleteArchives(names, confirmation, onProgress) {
    return fetch("/api/delete-archives", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names, confirmation }),
    }).then((r) => {
      if (!r.ok) return r.json().then((j) => Promise.reject(new Error(j.error || "Delete failed")));
      return readNdjson(r, onProgress);
    });
  }

  // Fills the Archives panel's #archive-list ("Open an existing archive") from
  // `GET /api/archives` -- newest first, server-side. Summary derivation can
  // take a moment for a large archive, so the list always exposes its state.
  // Deleting a row is the one place layout animation earns its cost: rare,
  // user-initiated, and the question it answers ("which row died, and where
  // did the rest go?") cannot be answered by colour. Transforms only -- the
  // rows are measured before the list re-renders, then animated from their
  // old slots to their new ones (FLIP).
  function archiveRowPositions() {
    const map = new Map();
    const box = document.getElementById("archive-list");
    if (!box) return map;
    box.querySelectorAll(".recent-archive-row").forEach((row) => {
      const pick = row.querySelector(".archive-select");
      if (pick && pick.dataset.name) map.set(pick.dataset.name, row.getBoundingClientRect().top);
    });
    return map;
  }
  function flipArchiveRows(before) {
    if (reduce || !before || !before.size) return;
    const box = document.getElementById("archive-list");
    if (!box) return;
    box.querySelectorAll(".recent-archive-row").forEach((row) => {
      const pick = row.querySelector(".archive-select");
      const prev = pick && pick.dataset.name ? before.get(pick.dataset.name) : null;
      if (prev == null) return;
      const dy = prev - row.getBoundingClientRect().top;
      if (!dy) return;
      row.style.transform = "translateY(" + dy + "px)";
      requestAnimationFrame(() => {
        row.style.transition = "transform var(--dur-base) var(--ease-inout)";
        row.style.transform = "";
        setTimeout(() => { row.style.transition = ""; }, 240);
      });
    });
  }

  // `flipFrom` carries the pre-delete geometry; when it is present the rows
  // are survivors moving, not a list arriving, so the entrance stagger is
  // suppressed in favour of the FLIP.
  function loadRecentArchives(flipFrom) {
    const box = document.getElementById("archive-list");
    if (!box) return;
    box.innerHTML = '<span class="foot-note"><span class="spin" style="display:inline-block;vertical-align:-2px;margin-right:7px;"></span>Reading recent archives…</span>';
    fetch("/api/archives").then((r) => {
      if (!r.ok) throw new Error("archive list request failed");
      return r.json();
    }).then((j) => {
      const items = (j.archives || []);
      archivesOnPage = items;
      // Drop selections for archives that no longer exist.
      Array.from(archiveSelection).forEach((name) => {
        if (!items.some((a) => a.name === name)) archiveSelection.delete(name);
      });
      if (!items.length) { box.innerHTML = '<span class="foot-note">No recent archives yet.</span>'; return; }
      box.innerHTML = "";
      const shown = items.slice(0, RECENT_ARCHIVES_LIMIT);
      const extra = items.length - shown.length;
      shown.forEach((a, rowIndex) => {
        const row = document.createElement("div");
        row.className = "recent-archive-row";
        const pick = document.createElement("input");
        pick.type = "checkbox";
        pick.className = "archive-select";
        pick.dataset.name = a.name;
        pick.checked = archiveSelection.has(a.name);
        pick.title = "Select for bulk delete (shift-click to select a range)";
        pick.addEventListener("click", (event) => {
          const index = shown.indexOf(a);
          if (event.shiftKey && lastCheckedIndex !== null) {
            const [lo, hi] = [Math.min(lastCheckedIndex, index), Math.max(lastCheckedIndex, index)];
            for (let i = lo; i <= hi; i += 1) setSelected(shown[i].name, pick.checked);
          } else {
            setSelected(a.name, pick.checked);
          }
          lastCheckedIndex = index;
          syncCheckboxes();
        });
        row.appendChild(pick);
        // The card: an icon tile, then an identity block that IS the open
        // action, then the row's other actions beside it. One centred
        // button for the whole row, with more buttons nested inside it, is
        // invalid HTML and reads as a strip of noise.
        const tile = document.createElement("span");
        tile.className = "ra-icon";
        tile.setAttribute("aria-hidden", "true");
        tile.innerHTML = icon(a.is_demo ? "flask" : "package");
        row.appendChild(tile);
        const b = document.createElement("button");
        b.className = "recent-archive";
        b.type = "button";
        b.title = "Open this archive in the Reader";
        const label = a.display_name || a.name;
        const title = document.createElement("span");
        title.className = "ra-title";
        title.textContent = label;
        b.appendChild(title);
        const archiveName = document.createElement("span");
        archiveName.className = "archive-name mono";
        archiveName.textContent = a.name;
        b.appendChild(archiveName);
        const itemsFact = (a.item_count || 0) + " items";
        const detail = document.createElement("span");
        detail.className = "foot-note archive-detail";
        detail.textContent = itemsFact + " · reading archive details…";
        b.appendChild(detail);
        // `/api/archives` already carried the persisted summary, so only ask
        // for it separately when this archive has none stored yet.
        (a.summary ? Promise.resolve(a.summary)
                   : fetch("/api/archive-summary?name=" + encodeURIComponent(a.name))
                       .then((r) => r.ok ? r.json() : Promise.reject(new Error("summary failed"))))
          .then((summary) => {
            if (summary.status === "ok") {
              const groups = (summary.groups || []).map((g) =>
                g.kind + ": " + g.title + " (" + g.count + ")"
              );
              const communities = (summary.communities || []).map((c) => "community: " + c.title);
              detail.textContent = [itemsFact].concat(groups).concat(communities).join(" · ");
            } else {
              detail.textContent = itemsFact + " · " + (summary.detail || "not browsable");
            }
          })
          .catch(() => { detail.textContent = itemsFact + " · details unavailable"; });
        b.addEventListener("click", () => openArchive(a.name, label, b));
        row.appendChild(b);
        const actions = document.createElement("div");
        actions.className = "ra-actions";
        // "Extend or update" beside "open": an archive stays workable for as
        // long as the original system is reachable, and this is where someone
        // is already looking at the archive they mean.
        const extend = document.createElement("button");
        extend.type = "button";
        extend.className = "btn";
        extend.dataset.extendAction = "1";
        extend.textContent = "Extend or update";
        extend.title =
          "Add a component this archive never captured, or bring what it holds up to date. " +
          "Nothing already captured is removed, and nothing is duplicated.";
        extend.addEventListener("click", (event) => {
          event.stopPropagation();
          openArchiveForUpdate(a.name);
        });
        actions.appendChild(extend);
        row.appendChild(actions);
        const del = document.createElement("button");
        del.className = "btn danger archive-delete";
        del.type = "button";
        del.textContent = "Delete";
        del.title = "Delete this archive (requires typing its exact name)";
        del.addEventListener("click", async (event) => {
          event.stopPropagation();
          const ok = await showDialog({
            title: "Delete this archive?",
            bodyHtml: "<p>" + escapeHtml(a.display_name || a.name) + "</p>"
              + "<p class=\"mono\">" + escapeHtml(a.name) + "</p>"
              + "<p>" + (a.item_count || 0) + " captured items. This cannot be undone.</p>",
            confirmLabel: "Delete archive",
          });
          if (!ok) return;
          const confirmation = a.name;
          const before = archiveRowPositions();
          before.delete(a.name);  // the doomed row leaves; it never FLIPs
          if (!reduce) { row.classList.remove("entering"); row.classList.add("leaving"); }
          fetch("/api/delete-archive", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: a.name, confirmation }),
          }).then((r) => r.ok ? r.json() : r.json().then((j) => Promise.reject(new Error(j.error || "Delete failed"))))
            // The row is re-rendered out of existence, so the list rebuild
            // waits for its exit -- otherwise the delete lands in the same
            // frame and nothing ever animates. Runs alongside the request,
            // not after it, so it costs nothing on a slow delete.
            .then(() => reduce ? null : new Promise((res) => setTimeout(res, 170)))
            .then(() => loadRecentArchives(before))
            .catch((e) => {
              row.classList.remove("leaving");
              notify(e.message || "Could not delete archive.");
            });
        });
        actions.appendChild(del);
        // Empty -> populated reads as a list arriving, not as a pop. Capped
        // at 8 so a full list never feels slow to draw.
        if (!reduce && !flipFrom) {
          row.classList.add("entering");
          row.style.animationDelay = (Math.min(rowIndex, 8) * 24) + "ms";
          row.addEventListener("animationend", () => {
            row.classList.remove("entering");
            row.style.animationDelay = "";
          }, { once: true });
        }
        box.appendChild(row);
      });
      flipArchiveRows(flipFrom);
      if (extra > 0) {
        const more = document.createElement("span");
        more.className = "foot-note";
        const loc = (j.archives_dir || "").trim();
        more.textContent = "…and " + extra + " more" + (loc ? " (in " + loc + ")" : "") + ". See Settings for the full location.";
        box.appendChild(more);
      }
      const bulk = document.getElementById("delete-demo-archives");
      if (bulk) bulk.disabled = !items.some((a) => a.is_demo);
      applyArchiveFilter();
      updateSelectionSummary();
    }).catch(() => {
      box.innerHTML = '<span class="foot-note">Could not read recent archives. <button class="btn" type="button" id="retry-recent-archives">Retry</button></span>';
      document.getElementById("retry-recent-archives")?.addEventListener("click", loadRecentArchives);
    });
  }

  // Streams POST /api/delete-demo-archives, invoking `onProgress(done, total)`
  // as each archive is removed and resolving with the final `done` summary.
  // A refusal (wrong confirmation) is still a plain JSON 422, so that is
  // checked before any streaming begins.
  function deleteDemoArchives(confirmation, onProgress) {
    return fetch("/api/delete-demo-archives", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmation }),
    }).then((r) => {
      if (!r.ok) return r.json().then((j) => Promise.reject(new Error(j.error || "Delete failed")));
      return readNdjson(r, onProgress);
    });
  }

  // Reads an NDJSON delete stream, calling `onProgress(done, total)` per
  // archive and resolving with the final `done` summary.
  function readNdjson(response, onProgress) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let summary = null;
    const pump = () => reader.read().then(({ done, value }) => {
      buffer += done ? "" : decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = done ? "" : lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let record;
        try { record = JSON.parse(line); } catch (_) { continue; }
        if (record.event === "deleted") onProgress(record.index, record.total);
        else if (record.event === "done") summary = record;
      }
      return done ? summary : pump();
    });
    return pump();
  }

  function wireArchiveActions() {
    const bulk = document.getElementById("delete-demo-archives");
    if (!bulk) return;
    bulk.addEventListener("click", async () => {
      const demoCount = archivesOnPage.filter((x) => x.is_demo).length;
      const ok = await showDialog({
        title: "Delete all demo archives?",
        bodyHtml: "<p>This permanently deletes <b>" + demoCount + "</b> demo archive"
          + (demoCount === 1 ? "" : "s") + ". Real captures are not touched.</p>",
        confirmLabel: "Delete demo archives",
        confirmWord: "DELETE DEMO ARCHIVES",
      });
      if (!ok) return;
      const confirmation = "DELETE DEMO ARCHIVES";
      // The response is an NDJSON stream (one record per archive, then a
      // `done` summary), not a single JSON body: a large collection takes
      // long enough that waiting for the whole thing looks like a hang.
      // Read it incrementally and report progress on the button itself.
      const label = bulk.textContent;
      bulk.disabled = true;
      deleteDemoArchives(confirmation, (done, total) => {
        bulk.textContent = "Deleting… " + done + " / " + total;
      }).then((summary) => {
        if (summary && summary.failed && summary.failed.length) {
          notify("Deleted " + summary.deleted + ", but " + summary.failed.length + " could not be removed.", "Deleted with problems");
        }
        loadRecentArchives();
      }).catch((e) => notify(e.message || "Could not delete demo archives."))
        .then(() => { bulk.disabled = false; bulk.textContent = label; });
    });
  }

  // ---------- Settings (editable defaults + environment status) ----------
  // Fills #panel-settings from GET /api/settings and keeps the editable
  // defaults in sync with the server. An unreachable server
  // just leaves the panel's own "—" placeholders in place, same
  // degrade-gracefully pattern as loadRecentArchives/loadDemoChips.
  // ---------- PDF appearance -------------------------------------------
  // Controls are built from what the server publishes, not from a list here:
  // a token added to the stylesheet shows up without touching this file.
  let styleTokenDefaults = [];

  function renderStyleControls(tokens, current) {
    styleTokenDefaults = tokens || [];
    const grid = $("style-grid");
    if (!grid) return;
    grid.innerHTML = "";
    for (const tok of styleTokenDefaults) {
      const label = document.createElement("label");
      label.className = "style-field";
      const name = document.createElement("span");
      name.textContent = tok.name.replace(/-/g, " ");
      const input = document.createElement("input");
      input.type = "text";
      input.dataset.token = tok.name;
      // The default as placeholder, so an empty field reads as "unchanged"
      // rather than "unset", and you can always see what you are moving from.
      input.placeholder = tok.default || "";
      input.value = (current && current[tok.name]) || "";
      label.appendChild(name);
      label.appendChild(input);
      grid.appendChild(label);
    }
  }

  // Header/footer fields, built from the defaults the server publishes for the
  // same reason the token controls are: one list, on the server, next to the
  // code that uses it.
  function renderMarkControls(defaults, current) {
    const grid = $("marks-grid");
    if (!grid || !defaults) return;
    grid.innerHTML = "";
    for (const name of Object.keys(defaults)) {
      const label = document.createElement("label");
      label.className = "style-field";
      const caption = document.createElement("span");
      caption.textContent = name.replace(/_/g, " ");
      const input = document.createElement("input");
      input.type = "text";
      input.dataset.mark = name;
      input.placeholder = defaults[name] || "(nothing)";
      input.value = (current && current[name]) !== undefined ? current[name] : "";
      label.appendChild(caption);
      label.appendChild(input);
      grid.appendChild(label);
    }
  }

  document.addEventListener("input", (event) => {
    const el = event.target;
    if (el && el.dataset && el.dataset.mark !== undefined) el.dataset.touched = "1";
  });

  function currentMarkOverrides() {
    const out = {};
    for (const input of document.querySelectorAll("#marks-grid input[data-mark]")) {
      // An EMPTY field here is meaningful -- it means "print nothing there" --
      // so unlike the style tokens it cannot be skipped. Only a field left
      // untouched (never edited, still matching its placeholder) is omitted.
      if (input.dataset.touched === "1") out[input.dataset.mark] = input.value.trim();
    }
    return out;
  }

  //: Every editable setting as the console currently shows it. One place, so
  //: a control added to any card reaches every save -- a body that omits a
  //: field is a save that resets it.
  function settingsBody() {
    return {
      auth_mode: $("settings-auth-mode").value,
      min_interval: parseFloat($("settings-min-interval").value) || 1,
      default_author_filter: $("settings-author-filter").value,
      archive_only: !!($("settings-archive-only") || {}).checked,
      pdf_style: currentStyleOverrides(),
      pdf_marks: currentMarkOverrides(),
    };
  }

  function currentStyleOverrides() {
    const out = {};
    for (const input of document.querySelectorAll("#style-grid input[data-token]")) {
      const value = input.value.trim();
      if (value) out[input.dataset.token] = value;
    }
    return out;
  }

  //: Same idiom as the component list: this clears its pane and then appends
  //: page by page with an `await` between each, so two previews started close
  //: together interleave their pages into one pane. Hardened by inspection
  //: after the component list was caught doing it for real -- there is no
  //: reproduction of this one, so it is a guard rather than a fix.
  let stylePreviewRender = 0;

  async function previewPdfStyle() {
    const pane = $("style-preview-pane");
    const status = $("style-status");
    if (!pane) return;
    if (!window.pdfjsLib) {
      if (status) status.textContent = "The PDF preview library didn't load — reload and retry.";
      return;
    }
    if (status) status.textContent = "Rendering…";
    let res;
    try {
      res = await fetch("/api/pdf-style-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pdf_style: currentStyleOverrides(),
          pdf_marks: currentMarkOverrides(),
          archive_only: !!($("settings-archive-only") || {}).checked,
        }),
      });
    } catch (_) {
      if (status) status.textContent = "Could not reach the server.";
      return;
    }
    if (!res.ok) {
      let message = "Preview failed.";
      try { message = (await res.json()).error || message; } catch (_) {}
      if (status) status.textContent = message;
      return;
    }
    const thisPreview = ++stylePreviewRender;
    const buf = await res.arrayBuffer();
    let doc;
    try { doc = await window.pdfjsLib.getDocument({ data: buf, isEvalSupported: false }).promise; }
    catch (_) { if (status) status.textContent = "Could not read the preview."; return; }
    if (thisPreview !== stylePreviewRender) return;   // a later preview owns the pane
    pane.innerHTML = "";
    pane.hidden = false;
    const dpr = window.devicePixelRatio || 1;
    const targetW = Math.max(240, Math.min(760, pane.clientWidth - 24));
    for (let n = 1; n <= doc.numPages; n++) {
      if (thisPreview !== stylePreviewRender) return;   // superseded mid-render
      const page = await doc.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const vp = page.getViewport({ scale: (targetW / base.width) * dpr });
      const canvas = document.createElement("canvas");
      canvas.width = vp.width; canvas.height = vp.height;
      canvas.style.width = (vp.width / dpr) + "px";
      canvas.style.height = (vp.height / dpr) + "px";
      pane.appendChild(canvas);
      await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp, canvas }).promise;
    }
    if (status) status.textContent = `Preview of ${doc.numPages} sample page(s).`;
  }

  function renderSettings(s) {
    renderStyleControls(s.pdf_style_tokens, s.pdf_style);
    renderMarkControls(s.pdf_mark_defaults, s.pdf_marks);
    const authInput = $("settings-auth-mode");
    if (authInput) authInput.value = s.auth_mode || "sspi";
    const delayInput = $("settings-min-interval");
    if (delayInput && document.activeElement !== delayInput) delayInput.value = s.min_interval ?? 1;
    // The run screen's own delay field shows the same value, or the two
    // disagree and the one out of sight is the one that counts.
    const runDelay = $("field-delay");
    if (runDelay && document.activeElement !== runDelay && s.min_interval != null) {
      runDelay.value = String(s.min_interval);
    }
    const authorInput = $("settings-author-filter");
    if (authorInput && document.activeElement !== authorInput) authorInput.value = s.default_author_filter || "";

    const pdfStatusEl = $("set-pdf-status");
    const pdfDetailEl = $("set-pdf-detail");
    if (pdfStatusEl) {
      pdfStatusEl.textContent = s.pdf_browser_available
        ? "Ready — a Chromium-based browser is available"
        : "Unavailable";
      pdfStatusEl.className = "v " + (s.pdf_browser_available ? "good" : "warn");
    }
    if (pdfDetailEl) {
      if (s.pdf_browser_available || !s.pdf_browser_detail) {
        pdfDetailEl.hidden = true;
      } else {
        pdfDetailEl.hidden = false;
        pdfDetailEl.textContent = s.pdf_browser_detail;
      }
    }

    const archiveOnly = $("settings-archive-only");
    if (archiveOnly) archiveOnly.checked = !!s.archive_only;
    applyArchiveOnly(!!s.archive_only);
    const dirEl = $("set-archives-dir");
    // Only when it is not being edited: overwriting a half-typed path with a
    // refresh is a small thing that feels like the program fighting you.
    if (dirEl && document.activeElement !== dirEl) dirEl.value = s.archives_dir || "";
    const countEl = $("set-archives-count");
    if (countEl) {
      const n = s.archives_count || 0;
      countEl.textContent = n + (n === 1 ? " archive stored" : " archives stored");
    }
  }

  function loadSettings() {
    if (location.protocol === "file:") return; // no server to fetch from
    fetch("/api/settings").then((r) => r.json()).then(renderSettings).catch(() => {});
  }

  function wireSettings() {
    const themeBtn = $("settings-theme-btn");
    if (themeBtn) {
      themeBtn.addEventListener("click", () => {
        const cur = document.documentElement.getAttribute("data-theme") || "auto";
        applyTheme(THEME_CYCLE[cur] || "auto");
      });
    }
    const previewBtn = $("style-preview");
    if (previewBtn) {
      previewBtn.addEventListener("click", () => {
        previewBtn.disabled = true;
        previewPdfStyle().finally(() => { previewBtn.disabled = false; });
      });
    }
    const resetBtn = $("style-reset");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        for (const input of document.querySelectorAll("#style-grid input[data-token]")) {
          input.value = "";
        }
        for (const input of document.querySelectorAll("#marks-grid input[data-mark]")) {
          input.value = ""; delete input.dataset.touched;
        }
        const pane = $("style-preview-pane");
        if (pane) { pane.hidden = true; pane.innerHTML = ""; }
        const status = $("style-status");
        if (status) status.textContent = "Back to the defaults shown in each field.";
      });
    }

    // Two buttons, one save. The PDF fields sit in a card of their own while
    // the Save that keeps them is further up the page, so each button has to
    // send the whole set -- and therefore has to send every field on screen.
    // A field left out of this is one a save silently resets.
    const saveSettings = (save, status) => {
      if (!save) return;
      save.disabled = true;
      if (status) status.textContent = "Saving\u2026";
      fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsBody()),
      }).then((r) => r.ok ? r.json() : r.json().then((j) => Promise.reject(new Error(j.error || "Save failed"))))
        .then((s) => { renderSettings(s); if (status) status.textContent = "Saved for new ingests."; })
        .catch((err) => { if (status) status.textContent = err.message || "Could not save settings."; })
        .finally(() => { save.disabled = false; });
    };
    // Applied and kept as it is pressed, like the theme control beside it in
    // the same card -- there is no Save here because there is nothing to
    // confirm: the screens appear or disappear in front of you, which is the
    // whole of what this setting does.
    const archiveOnlyBox = $("settings-archive-only");
    if (archiveOnlyBox) {
      archiveOnlyBox.addEventListener("change", () => {
        const on = !!archiveOnlyBox.checked;
        applyArchiveOnly(on);
        // This card's own status line. The Save buttons' status sits in a
        // card further up the page, where a message about a control down
        // here would be reported out of sight of the thing it is about.
        const status = $("settings-appearance-status");
        // There is no Save here, so the saving has to be visible instead:
        // "Saving" while it is in flight, then what was kept. Without it the
        // screens change and nothing says whether the change outlives the
        // tab, which is the one thing a display setting has to answer.
        if (status) status.textContent = "Saving\u2026";
        // Sent as part of the whole set: a partial body would reset the
        // fields it left out.
        fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(settingsBody()),
        })
          .then((r) => (r.ok ? r.json() : Promise.reject(new Error("Could not keep that."))))
          .then(() => {
            if (status) {
              status.textContent = on
                ? "Saved — the capture screens stay hidden until you turn this off."
                : "Saved — the capture screens are back.";
            }
          })
          .catch(() => {
            // Kept and shown must agree. If the server would not take it,
            // put the control back rather than leave a tick that means
            // nothing after a reload.
            archiveOnlyBox.checked = !on;
            applyArchiveOnly(!on);
            if (status) status.textContent = "Could not save that setting.";
          });
      });
    }

    const settingsSave = $("settings-save");
    if (settingsSave) {
      settingsSave.addEventListener("click", () =>
        saveSettings(settingsSave, $("settings-save-status")));
    }
    const styleSave = $("style-save");
    if (styleSave) {
      styleSave.addEventListener("click", () => saveSettings(styleSave, $("style-save-status")));
    }
  }

  // ---------- Archive-only mode -------------------------------------------
  //
  // For anyone who only ever works with archives they already have. A screen
  // that can never succeed is not neutral: it is clutter that makes the tool
  // look broken. Purely a display choice -- nothing on disk changes, and
  // unticking it brings everything back.

  //: What needs the original system to answer, and so has nothing to offer
  //: without one. The demo is deliberately NOT here: it runs entirely
  //: in-process, and it is the one place left that shows how an archive comes
  //: to exist at all.
  const LIVE_ONLY_SECTIONS = ["select", "ingest"];
  const LIVE_ONLY_CONTROLS = ["start-live-pdf", "live-pdf-preview"];

  function applyArchiveOnly(on) {
    document.querySelectorAll("[data-section]").forEach((button) => {
      if (LIVE_ONLY_SECTIONS.includes(button.dataset.section)) button.hidden = !!on;
    });
    LIVE_ONLY_CONTROLS.forEach((id) => {
      const el = $(id);
      if (el) el.hidden = !!on;
    });
    document.querySelectorAll("[data-extend-action]").forEach((el) => { el.hidden = !!on; });
    // Standing on a screen that has just been hidden would leave the console
    // showing a section its own navigation no longer offers.
    const showing = SECTIONS.find((s) => {
      const panel = document.getElementById("panel-" + s);
      return panel && !panel.hidden;
    });
    if (on && LIVE_ONLY_SECTIONS.includes(showing)) showSection("archives");
  }

  // ---------- Archives location -------------------------------------------
  //
  // It was an environment variable and a read-only line of text. A folder can
  // now be typed or dropped: a dropped directory is the shortest path from
  // "the archives are over there" to the console showing them.

  function applyArchivesDir(path) {
    const status = $("set-archives-status");
    const value = (path || "").trim();
    if (!value) return;
    if (status) status.textContent = "Checking\u2026";
    fetch("/api/archives-dir", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: value }),
    })
      .then((r) => r.json().then((j) => ({ ok: r.ok, body: j })))
      .then(({ ok, body }) => {
        if (!ok) {
          // The path stays as typed: retyping a long path to fix one
          // character is the kind of thing that makes people give up.
          if (status) status.textContent = body.error || "That directory cannot be used.";
          return;
        }
        if (status) status.textContent = "Now reading archives from here.";
        loadSettings();
        loadRecentArchives();
      })
      .catch(() => { if (status) status.textContent = "Could not change the location."; });
  }

  function wireArchivesDir() {
    const input = $("set-archives-dir");
    const apply = $("set-archives-apply");
    const drop = $("archives-dir-drop");
    if (!input) return;
    if (apply) apply.addEventListener("click", () => applyArchivesDir(input.value));
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); applyArchivesDir(input.value); }
    });
    if (!drop) return;
    // A dropped folder. Browsers hand a directory over as a file entry with
    // no usable path, so what actually travels is the text of the drag --
    // which is what a file manager puts there, usually as a file:// URL.
    drop.addEventListener("dragover", (event) => {
      event.preventDefault();
      drop.classList.add("drop-armed");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("drop-armed"));
    drop.addEventListener("drop", (event) => {
      event.preventDefault();
      drop.classList.remove("drop-armed");
      const dt = event.dataTransfer;
      const raw = dt ? (dt.getData("text/uri-list") || dt.getData("text/plain") || "") : "";
      const path = pathFromDropText(raw);
      if (path) { input.value = path; applyArchivesDir(path); }
    });
  }

  //: A dropped folder as a filesystem path. `file:///home/joe/x` -> `/home/joe/x`,
  //: with percent-escapes undone, because a folder called "My Archives" arrives
  //: as `My%20Archives` and would otherwise be reported as not existing.
  function pathFromDropText(raw) {
    const first = String(raw || "").split(/[\r\n]+/).find((line) => line.trim());
    if (!first) return "";
    const text = first.trim();
    if (!text.startsWith("file://")) return text;
    try {
      return decodeURIComponent(text.replace(/^file:\/\/[^/]*/, ""));
    } catch (_) {
      return text;
    }
  }

  // ---------- Manual: full interchange spec, rendered & lazy-loaded ----------
  // GET /api/manual/interchange returns our own trusted, pre-rendered HTML
  // (connections_export.gui.app: Python-Markdown over INTERCHANGE.md / the
  // repo's docs/reference/interchange-format.md) -- fetched once, on first
  // click, and injected into #man-spec-container. Unlike a captured page
  // body, this never goes through the sandboxed reader iframe: it's our
  // own documentation, not third-party content.
  let manSpecLoaded = false;

  function wireManual() {
    const btn = $("man-spec-toggle");
    const container = $("man-spec-container");
    if (!btn || !container) return;
    btn.addEventListener("click", () => {
      if (location.protocol === "file:") {
        container.hidden = false;
        container.innerHTML =
          '<p class="foot-note">Unavailable when the console is opened directly as a file -- ' +
          "run hcl-serve (or the demo) to view the full specification here.</p>";
        return;
      }
      if (!container.hidden) {
        container.hidden = true;
        btn.textContent = "Show the full specification";
        return;
      }
      container.hidden = false;
      btn.textContent = "Hide the full specification";
      if (manSpecLoaded) return;
      container.innerHTML = '<div class="manual-spec-loading">Loading…</div>';
      fetch("/api/manual/interchange")
        .then((r) => {
          if (!r.ok) throw new Error("spec unavailable");
          return r.json();
        })
        .then((data) => {
          container.innerHTML = data.html || "<p>Not available.</p>";
          manSpecLoaded = true;
        })
        .catch(() => {
          container.innerHTML =
            '<p class="foot-note">Could not load the specification from this server.</p>';
        });
    });
  }

  // Demo drag-drop chips: sample URLs sourced from the
  // fakeserver's own demo entities (GET /api/demo-urls), never hardcoded
  // here -- this file only renders whatever the server returns. Chips
  // are identify-only: click -> fillAndIdentify(url); dragstart -> sets
  // "text/plain" so dropping on the existing #dropzone runs identify via
  // its own drop handler above. Nothing here ever calls beginRun -- the
  // ONLY run trigger in demo mode is #run-demo's beginRun({demo:true}).
  let demoChipsLoaded = false;

  function renderDemoChips(urls) {
    const container = $("demo-chips");
    if (!container) return;
    container.innerHTML = "";
    urls.forEach((item) => {
      const chip = document.createElement("div");
      chip.className = "demo-chip";
      chip.draggable = true;
      chip.dataset.url = item.url;
      chip.title = item.url;
      chip.textContent = item.label || item.kind;
      chip.addEventListener("click", () => fillAndIdentify(chip.dataset.url));
      chip.addEventListener("dragstart", (e) => {
        if (e.dataTransfer) e.dataTransfer.setData("text/plain", chip.dataset.url);
      });
      container.appendChild(chip);
    });
  }

  function loadDemoChips() {
    if (demoChipsLoaded) return;
    demoChipsLoaded = true;
    fetch("/api/demo-urls")
      .then((res) => res.json())
      .then((data) => {
        const urls = data.urls || [];
        renderDemoChips(urls);
        // A non-empty list only ever comes back from the demo/fakeserver --
        // treat that as "we're in demo mode" and reveal the #demo-mode
        // TOGGLE itself so it exists to be opened. In a real deployment
        // /api/demo-urls returns [], so #demo-mode (and everything
        // demo-related) never takes any space. The chips container itself
        // stays hidden until the user actually opts in -- see
        // openDemoChips()/wireDemoChips() below (finding 3 fixed the
        // reachability half of this; this fixes the "took space anyway"
        // half).
        if (urls.length) {
          const btn = $("demo-mode");
          if (btn) btn.hidden = false;
        }
      })
      .catch(() => {});
  }

  function setDemoModeLabel(open) {
    const btn = $("demo-mode");
    if (btn) btn.textContent = open ? "Hide demo sample URLs" : "Show demo sample URLs";
  }

  // Opt-in reveal of the chip container -- called both by #demo-mode's own
  // click handler and by the Overview's "Try the demo" CTA. Lazy-loads the
  // chips via the existing loadDemoChips() (a no-op past its first call).
  function openDemoChips() {
    const chipsContainer = $("demo-chips");
    if (chipsContainer) chipsContainer.hidden = false;
    loadDemoChips();
    setDemoModeLabel(true);
  }

  function closeDemoChips() {
    const chipsContainer = $("demo-chips");
    if (chipsContainer) chipsContainer.hidden = true;
    setDemoModeLabel(false);
  }

  function wireDemoChips() {
    const demoModeBtn = $("demo-mode");
    const chipsContainer = $("demo-chips");
    if (!demoModeBtn || !chipsContainer) return;
    demoModeBtn.addEventListener("click", () => {
      if (chipsContainer.hidden) openDemoChips();
      else closeDemoChips();
    });
  }

  // The delay is set in two places -- Settings, and the field on this screen
  // -- and the run body carries THIS one, which the server prefers over the
  // saved setting. So a saved 0.2 with the field left at its markup default
  // of 1 meant the run paced at 1: a value the user had never touched
  // silently beat the one they had saved. The field is the run's value, so it
  // starts as whatever was saved.
  function syncDelayFromSettings() {
    const field = $("field-delay");
    if (!field) return;
    fetch("/api/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((settings) => {
        if (!settings || settings.min_interval == null) return;
        if (document.activeElement === field) return;   // never fight the user
        field.value = String(settings.min_interval);
        if (typeof updateEstimate === "function") updateEstimate();
      })
      .catch(() => { /* the markup default stands */ });
  }

  //: Everything on Select & Tailor that describes a run rather than watches
  //: one. Locked while a run is in flight -- still readable, because seeing
  //: what THIS run was given is the point, but not editable, because editing
  //: it would describe a run that is not happening. The delay is deliberately
  //: not in this list.
  const selectControlsDisabled = [
    "start-ingest", "size-all", "size-preview", "size-count",
    "add-community", "add-community-url", "add-subcommunities",
    "author-mode-everyone", "author-mode-me", "author-mode-person",
    "field-author", "author-resolve", "author-me", "author-search-preview",
    "recheck-comments", "field-base-url",
  ];

  // Say that a run is happening, on the screen that started it.
  function setSelectRunning(running) {
    const note = $("select-running");
    if (note) note.hidden = !running;
    const start = $("start-ingest");
    if (start) {
      start.disabled = running;
      start.classList.toggle("is-running", running);
    }
    selectControlsDisabled.forEach((id) => {
      const el = $(id);
      if (el) el.disabled = running;
    });
    document
      .querySelectorAll("#community-component-options input, #extra-community-groups input")
      .forEach((input) => { input.disabled = running; });
  }

  // The delay is the one thing worth changing mid-run, so it changes the RUN
  // rather than the saved default -- and says whether it took.
  function wireLivePacing() {
    const field = $("field-delay");
    if (!field) return;
    field.addEventListener("change", () => {
      if (!liveRunning) return;   // between runs it is just the next run's value
      const seconds = parseFloat(field.value);
      if (!Number.isFinite(seconds)) return;
      fetch("/api/pace", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ min_interval: seconds }),
      })
        .then((r) => r.json())
        .then((j) => {
          if (j && j.status === "ok") {
            field.value = String(j.min_interval);   // the floor may have raised it
            notify("Now pacing at " + j.min_interval + "s between requests.", "Pace changed");
          }
        })
        .catch(() => { /* the run keeps its current pace */ });
    });
  }

  function boot() {
    initTheme();
    syncDelayFromSettings();
    wireLivePacing();
    setSidebarCollapsed((localStorage.getItem("hcl-export-sidebar") || "0") === "1");
    wireOverview();
    wireModeGrid();
    wireSetupScreen();
    wireSourcePanel();
    wireArchivesDir();
    wireDemoTime();
    wireDemoChips();
    wireArchiveActions();
    wireSelectionToolbar();
    wireArchiveFilter();
    wirePdfPicker();
    wireAuthorSegments();
    loadShellIdentity();
    // Before the first screen is chosen: the settings decide which sections
    // exist at all (`archive_only`), so a console that read them only when
    // you opened Settings would spend until then contradicting them.
    loadSettings();
    wireSettings();
    wireManual();
    loadDemoChips();
    loadRecentArchives();
    showSection("overview");
  }

  boot();
})();
