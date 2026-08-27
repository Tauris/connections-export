"""The curated, believable demo dataset the fake server serves.

This module hard-codes a fixed set of wikis (`_WIKIS`, `_AUTHORS`,
`_PAGE_CONTENT`) that reads as a real, convincing Connections deployment:
curated titles and page tree, per-page comment counts, versions,
images, attachments, and — importantly — **its own topic-specific body prose
for every page**, so a demo reads as a genuine deployment rather than twenty
identical boilerplate pages. It builds all of this into a `WikiSet` the fake
server serves and the real pipeline crawls.

This dataset is the **single source of the demo's content**. The served front
end (`gui/static/console.html` + `console.js`) renders whatever the pipeline
produces over SSE and fabricates nothing itself; there is no separate offline
simulator to keep in sync.

Nothing here is parametric or random: it is a fixed, transcribed dataset. The
gradient "diagram" thumbnail is served as a real fetchable asset
(`genthumb_svg`) and referenced from the body via `<img src=...>`, so the
genuine pipeline crawls it to a blob and the reader renders it, exercising the
same-host/cross-app asset paths. Determinism is preserved (`_IdFactory`/
`_Clock`, seeded), matching `synth.py`'s guarantees.
"""

from __future__ import annotations

import html
import random

from connections_export.fakeserver.content import page_comment
from connections_export.fakeserver.model import (
    FakeAttachment,
    FakeComment,
    FakePage,
    FakeVersion,
    FakeWiki,
    WikiSet,
)
from connections_export.fakeserver.synth import _Clock, _IdFactory

# --- the curated demo dataset ----------------------------------------------

# label, title, [(page title, depth, comment count),...]
_WIKIS: tuple[tuple[str, str, tuple[tuple[str, int, int], ...]], ...] = (
    (
        "eng-handbook",
        "Engineering Handbook",
        (
            ("Onboarding", 0, 12),
            ("Dev Environment", 1, 5),
            ("Coding Standards", 1, 23),
            ("Code Review", 2, 8),
            ("Release Process", 1, 14),
            ("Hotfix Runbook", 2, 3),
            ("On-Call Guide", 1, 31),
            ("Incident Retro Template", 2, 0),
        ),
    ),
    (
        "product-wiki",
        "Product Wiki",
        (
            ("Vision 2026", 0, 41),
            ("Roadmap", 1, 19),
            ("Q3 Themes", 2, 7),
            ("Personas", 1, 4),
            ("Competitor Notes", 1, 16),
            ("Pricing Rationale", 2, 27),
        ),
    ),
    (
        "ops-kb",
        "Operations KB",
        (
            ("Runbooks Index", 0, 2),
            ("Backup & Restore", 1, 9),
            ("DR Playbook", 2, 11),
            ("Network Diagram", 1, 6),
            ("Access Requests", 1, 0),
            ("Vendor Contacts", 1, 5),
        ),
    ),
)

_AUTHORS = (
    "A. Okafor",
    "M. Lindqvist",
    "R. Delgado",
    "J. Weber",
    "S. Nakamura",
    "P. Novak",
    "L. Haddad",
    "T. Bianchi",
)

# genThumb: 5 gradient palettes × 4 white shapes, 120×80.
_THUMB_PALETTES = (
    ("#0ea5a0", "#134e4a"),
    ("#6366f1", "#312e81"),
    ("#e0a44c", "#7c4a11"),
    ("#ec5b62", "#7f1d22"),
    ("#38bdf8", "#0c4a6e"),
)
_THUMB_SHAPES = (
    '<rect x="14" y="30" width="92" height="10" rx="3" fill="#fff" opacity="0.85"/>'
    '<rect x="14" y="48" width="60" height="8" rx="3" fill="#fff" opacity="0.5"/>',
    '<circle cx="60" cy="40" r="21" fill="#fff" opacity="0.85"/>'
    '<path d="M40 60 L80 60" stroke="#fff" stroke-width="6" opacity="0.5"/>',
    '<path d="M18 60 L45 30 L70 50 L102 22" stroke="#fff" stroke-width="5" '
    'fill="none" opacity="0.9"/>',
    '<rect x="24" y="22" width="30" height="36" fill="#fff" opacity="0.8"/>'
    '<rect x="64" y="34" width="30" height="24" fill="#fff" opacity="0.55"/>',
)

# genAssets file pool: (basename, extension, badge, content-type).
_FILE_POOL = (
    ("spec", "pdf", "PDF", "application/pdf"),
    (
        "notes",
        "docx",
        "DOC",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ("metrics", "xlsx", "XLS", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("export", "zip", "ZIP", "application/zip"),
)

# --- believable per-page body prose ----------------------------------------
#
# Each page gets 2-3 short, topic-true paragraphs and its own author callout,
# so a demo reads as a real deployment rather than 20 identical boilerplate
# pages. Keyed by (wiki_label, page_label). A page missing from this table
# falls back to `_GENERIC_PARAS` / `_GENERIC_NOTE` so the generator can never
# emit an empty body.

_GENERIC_PARAS = (
    "This page is part of the wiki. It carries its full revision history and comment thread.",
    "The section below was styled by its author using inline CSS — preserved verbatim on export.",
)
_GENERIC_NOTE = "Author callout: keep this in sync with the release checklist before every deploy."

# (wiki_label, page_label) -> (paragraphs, author_note)
_PAGE_CONTENT: dict[tuple[str, str], tuple[tuple[str, ...], str]] = {
    # --- Engineering Handbook ---
    ("eng-handbook", "onboarding"): (
        (
            "Welcome aboard. Your first week is about getting access, a working "
            "environment, and one small change shipped to production — in that "
            "order. Don't try to understand the whole system at once; pick a "
            "starter ticket and follow it end to end.",
            "By day two you should be able to build and run the app locally (see "
            "Dev Environment). By day five, aim to have a review-approved pull "
            "request merged, however tiny. Pair with your onboarding buddy "
            "whenever you're stuck for more than thirty minutes.",
        ),
        "Author callout: if any onboarding step is out of date, fix it in the "
        "same PR as your first change — that's the tradition.",
    ),
    ("eng-handbook", "dev-environment"): (
        (
            "Everything runs from the mono-repo. Clone it, run the bootstrap "
            "script, and you'll have the toolchain, pre-commit hooks, and a "
            "seeded local database in one pass. Prefer the containerised setup "
            "unless you have a specific reason not to.",
            "The most common failure is a stale dependency cache after a rebase — "
            'a clean bootstrap fixes ninety percent of "works on my machine" '
            "reports. If the app won't start, check the ports listed below before "
            "opening a ticket.",
        ),
        "Author callout: pin new tooling versions here the day you introduce "
        "them, so the next hire doesn't inherit a moving target.",
    ),
    ("eng-handbook", "coding-standards"): (
        (
            "We optimise for readability over cleverness. Code is read far more "
            "often than it's written, so match the style of the file you're in "
            "and leave it a little clearer than you found it.",
            "Small, focused functions; explicit names; comments that explain why, "
            "not what. Formatting is automated — don't argue with the formatter, "
            "and don't hand-tune what the linter owns. Anything the tools can "
            "enforce, we let them enforce.",
        ),
        "Author callout: propose standards changes as a PR to this page with an "
        "example, not as a hallway decree.",
    ),
    ("eng-handbook", "code-review"): (
        (
            "Review is a conversation, not a gate. The author's job is to make "
            "the change easy to review; the reviewer's job is to understand it "
            "and improve it, not to prove they could have written it differently.",
            "Aim to respond within one working day. Approve when the change is a "
            "net improvement and safe to ship — not when it's theoretically "
            "perfect. Leave nits as nits, and say so.",
        ),
        "Author callout: if a review thread passes three round-trips, switch to "
        "a call — text is the wrong medium for the disagreement.",
    ),
    ("eng-handbook", "release-process"): (
        (
            "Releases go out on a regular cadence from the main branch. Every "
            "change rides the same train: merged, built, staged, verified, "
            "promoted. No side doors.",
            "A release is owned by a named release captain for the day. They "
            "watch the dashboards through the rollout and make the call to "
            "continue or roll back. Feature flags decouple deploy from launch, so "
            "shipping code is boring by design.",
        ),
        "Author callout: keep this in sync with the release checklist before every deploy.",
    ),
    ("eng-handbook", "hotfix-runbook"): (
        (
            "A hotfix is for a live, customer-impacting defect that can't wait "
            "for the next release. If it can wait, it isn't a hotfix — use the "
            "normal train.",
            "Branch from the current release tag, not from main, so you ship only "
            "the fix and nothing in flight. Get a second pair of eyes even under "
            "pressure; the whole point of the process is to stop a bad five "
            "minutes becoming a bad afternoon. File the retro before you close "
            "the incident.",
        ),
        "Author callout: every hotfix must be forward-ported to main the same "
        "day, or it will regress in the next release.",
    ),
    ("eng-handbook", "on-call-guide"): (
        (
            "On-call is a one-week rotation. You are the first responder for "
            "production alerts, not the person expected to fix everything alone — "
            "escalate early and without embarrassment.",
            "Acknowledge a page within five minutes. Your first job is to "
            "stabilise, not to diagnose root cause; mitigate, then investigate. "
            "Hand off cleanly at the end of your shift with a short written "
            "summary of anything still smouldering.",
        ),
        "Author callout: if an alert paged you and wasn't actionable, tune or "
        "delete it before you go off shift — noisy alerts are how real ones get "
        "missed.",
    ),
    ("eng-handbook", "incident-retro-template"): (
        (
            "Copy this page for every incident of significant severity. Retros "
            "are blameless: we examine the system and the process, never the "
            "person who happened to be holding the pager.",
            "Fill in the timeline, the customer impact, what went well, what "
            "didn't, and the follow-up actions with owners and dates. An action "
            "item without an owner is a wish, not a fix.",
        ),
        "Author callout: assign every action item to a person, not a team — "
        "teams don't get reminders.",
    ),
    # --- Product Wiki ---
    ("product-wiki", "vision-2026"): (
        (
            "Our north star for 2026 is to make the product something people "
            "reach for daily without thinking about it — reliable, fast, and "
            "quietly powerful. Everything on the roadmap should ladder up to that.",
            "We are willing to say no to good ideas that don't serve the core "
            "loop. Focus is the strategy. When a decision is hard, we optimise "
            "for the long-term trust of the people who already rely on us.",
        ),
        "Author callout: if a proposed feature doesn't ladder to this vision, "
        "that's a signal to cut it, not to widen the vision.",
    ),
    ("product-wiki", "roadmap"): (
        (
            "The roadmap is a statement of intent, not a contract. Near-term "
            "items are committed; anything past the current quarter is a "
            "direction we reserve the right to revise as we learn.",
            "Themes, not features, anchor each quarter — we describe the problem "
            "we're solving and let the shape of the solution stay flexible. Dates "
            "on this page are targets for planning, not promises to customers.",
        ),
        "Author callout: never paste a roadmap date into a customer email — link "
        "them to the changelog instead.",
    ),
    ("product-wiki", "q3-themes"): (
        (
            "Q3 has three themes: reduce time-to-first-value for new accounts, "
            "close the top five reliability gaps, and pay down the migration debt "
            "from the platform upgrade.",
            "Each theme has a directly responsible individual and a single "
            "headline metric. If a piece of work doesn't move one of those three "
            "metrics, it waits.",
        ),
        "Author callout: revisit these themes at the mid-quarter check-in; a "
        "theme with no movement by then gets re-scoped, not carried.",
    ),
    ("product-wiki", "personas"): (
        (
            "We design for three people: the daily power user who lives in the "
            "product, the occasional collaborator who visits to get one thing "
            "done, and the administrator who never sees the fun parts but carries "
            "all the risk.",
            "When a design pleases the power user but confuses the occasional "
            "visitor, the visitor wins — they're the ones we lose silently. The "
            "admin's needs are non-negotiable because their trust is what keeps "
            "the account.",
        ),
        "Author callout: keep these personas honest with real interview quotes; "
        "a persona nobody has actually met is a stereotype.",
    ),
    ("product-wiki", "competitor-notes"): (
        (
            "These notes are for our own calibration, not for marketing copy. "
            "Assume every competitor is competent and shipping; the interesting "
            "question is always where our priorities genuinely differ, not who is "
            '"better".',
            "We do not disparage competitors to customers, ever. Where a rival "
            "does something well, we say so internally and learn from it. Where "
            "we're behind, we write it down here so it can't be quietly forgotten.",
        ),
        "Author callout: cite a date and a source for every claim on this page — "
        "competitor facts rot faster than anything else we track.",
    ),
    ("product-wiki", "pricing-rationale"): (
        (
            "Pricing has three tiers, and the middle tier is designed to be the "
            "obvious choice for most teams. The entry tier exists to remove the "
            "first objection; the top tier exists to make the middle look "
            "reasonable and to serve genuinely large accounts.",
            "We price on value delivered, not on cost incurred. The goal is that "
            "a customer never feels punished for growing — usage should scale in "
            "gentle steps, never cliffs. Any change to these numbers needs "
            "sign-off from both product and finance.",
        ),
        "Author callout: model any pricing change against the current top ten "
        "accounts before proposing it — the tail is where surprises live.",
    ),
    # --- Operations KB ---
    ("ops-kb", "runbooks-index"): (
        (
            "This is the front door to Operations. Every routine and emergency "
            "procedure the team owns is linked from here; if a runbook exists but "
            "isn't listed, it effectively doesn't exist.",
            "A good runbook is boring: numbered steps, exact commands, and a "
            'clear "you are done when…" at the end. Prose belongs elsewhere. '
            "When you run a procedure, note anything that was wrong and fix it "
            "before you forget.",
        ),
        "Author callout: a runbook that hasn't been executed in six months is "
        "assumed stale until someone re-runs it.",
    ),
    ("ops-kb", "backup-restore"): (
        (
            "Backups are only as good as the last successful restore. We take "
            "frequent automated backups, but the number that matters is how "
            "quickly and reliably we can bring data back — so we rehearse "
            "restores on a schedule, not just when it's on fire.",
            "Backups are encrypted at rest and copied to a second region. "
            "Retention follows the data-classification policy. Never test a "
            "restore against production; use the isolated restore target "
            "described below.",
        ),
        "Author callout: log every restore rehearsal here with its wall-clock "
        "duration — that number is our real recovery time, not the one in the "
        "SLA doc.",
    ),
    ("ops-kb", "dr-playbook"): (
        (
            "This playbook is for the loss of an entire region or a comparable "
            "disaster. It is deliberately prescriptive: in a real event, people "
            "follow steps, they don't design solutions.",
            "Recovery targets: bring critical services back within the stated "
            "RTO, with no more than the stated RPO of data loss. Failover order "
            "matters — data layer first, then application, then edge. Declare the "
            "incident and open the comms bridge before you touch anything.",
        ),
        "Author callout: the failover has to be rehearsed to count; an untested "
        "DR plan is a document, not a capability.",
    ),
    ("ops-kb", "network-diagram"): (
        (
            "The diagram below is the canonical view of how traffic flows from "
            "the edge to the data layer. Treat it as the source of truth; if "
            "reality and this page disagree, reality wins and this page is the "
            "bug.",
            "Trust boundaries are drawn deliberately — anything crossing one "
            "needs authentication and logging. Public ingress is limited to the "
            "two entry points shown; everything else is internal by default.",
        ),
        "Author callout: update this diagram in the same change that alters the "
        'topology, not "later" — a stale network diagram has caused more '
        "outages than it has prevented.",
    ),
    ("ops-kb", "access-requests"): (
        (
            "Access follows least privilege: request the narrowest role that "
            "lets you do the job, for the shortest time you need it. Standing "
            "admin access is the exception, granted rarely and reviewed often.",
            "Requests are approved by the resource owner, not by whoever is "
            "quickest to say yes. Time-boxed grants expire on their own; that's a "
            'feature. Emergency "break-glass" access is available, heavily '
            "logged, and always followed by a review.",
        ),
        "Author callout: every quarter we revoke access nobody has used — if you "
        "lose something you actually needed, that's a signal you over-scoped, so "
        "just re-request it.",
    ),
    ("ops-kb", "vendor-contacts"): (
        (
            "This page lists who to call, for what, and how fast they're "
            "contractually obliged to answer. Keep it accurate — the middle of "
            "an incident is the worst time to discover a support number changed.",
            "For each vendor we record the support tier, the escalation path, the "
            "account manager, and the contract renewal date. Sensitive "
            "credentials never live here; this page holds contacts and process "
            "only.",
        ),
        "Author callout: verify each vendor's escalation path at renewal time — "
        "support entry points change silently and nobody sends you a memo.",
    ),
}


#: Cover palettes, deliberately distinct from `_THUMB_PALETTES` so a blog
#: cover never looks like an inline wiki diagram.
_COVER_PALETTES = (
    ("#0f766e", "#134e4a"),
    ("#7c3aed", "#4c1d95"),
    ("#b45309", "#7c2d12"),
    ("#0369a1", "#0c4a6e"),
    ("#be123c", "#881337"),
    ("#4d7c0f", "#1a2e05"),
    ("#a21caf", "#701a75"),
    ("#1d4ed8", "#1e3a8a"),
)


def _wrap_svg_text(text: str, width: int = 26, lines: int = 3) -> list[str]:
    """Greedy word wrap for SVG `<text>`, which does not wrap on its own."""
    words, out, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            out.append(current)
            current = word
            if len(out) == lines:
                break
        else:
            current = candidate
    if current and len(out) < lines:
        out.append(current)
    if out and len(" ".join(out)) < len(text):
        out[-1] = out[-1].rstrip(".,;:") + "\u2026"
    return out


def cover_svg(*, title: str, kicker: str, index: int) -> bytes:
    """A blog post's cover image: a 16:9 card carrying the post's own title.

    Every synthesized post embedded `cover.png`, and the image route picked
    its gradient from the FILENAME -- which has no digits, so every cover in
    every blog resolved to `genthumb_svg(0)`: one shared blob, the same
    picture on every entry. A cover is now keyed to the post, and says what
    it is a cover for, so a demo archive looks like a blog rather than a
    column of identical grey boxes.
    """
    pal = _COVER_PALETTES[index % len(_COVER_PALETTES)]
    esc = lambda s: (  # noqa: E731
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    rows = _wrap_svg_text(title)
    # Well clear of the kicker at y=100: the title block grows upward as it
    # wraps, so start it low enough that three lines still clear the label.
    y0 = 260 - (len(rows) - 1) * 30
    spans = "".join(
        f'<tspan x="56" y="{y0 + i * 52}">{esc(row)}</tspan>' for i, row in enumerate(rows)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" '
        'viewBox="0 0 960 540" role="img" '
        f'aria-label="{esc(title)}">'
        f'<defs><linearGradient id="c{index}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{pal[0]}"/>'
        f'<stop offset="1" stop-color="{pal[1]}"/></linearGradient></defs>'
        f'<rect width="960" height="540" fill="url(#c{index})"/>'
        f'<circle cx="820" cy="96" r="150" fill="#ffffff" opacity="0.06"/>'
        f'<circle cx="880" cy="470" r="96" fill="#ffffff" opacity="0.05"/>'
        f'<rect x="56" y="64" width="52" height="5" rx="2.5" fill="#ffffff" opacity="0.85"/>'
        f'<text x="56" y="100" fill="#ffffff" opacity="0.75" '
        'font-family="ui-sans-serif, system-ui, sans-serif" font-size="24" '
        f'letter-spacing="3">{esc(kicker.upper())}</text>'
        f'<text fill="#ffffff" font-family="ui-sans-serif, system-ui, sans-serif" '
        f'font-size="46" font-weight="700">{spans}</text>'
        "</svg>"
    ).encode()


def genthumb_svg(i: int) -> bytes:
    """genThumb(i) as a standalone SVG document (the 120×80 gradient + shape).
    Served by the fake server's image routes so a demo page's `<img>` resolves
    to a real, colourful blob."""
    pal = _THUMB_PALETTES[i % len(_THUMB_PALETTES)]
    shape = _THUMB_SHAPES[i % len(_THUMB_SHAPES)]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" '
        'viewBox="0 0 120 80" preserveAspectRatio="xMidYMid slice" role="img" '
        'aria-label="diagram">'
        f'<defs><linearGradient id="g{i}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{pal[0]}"/>'
        f'<stop offset="1" stop-color="{pal[1]}"/></linearGradient></defs>'
        f'<rect width="120" height="80" fill="url(#g{i})"/>{shape}</svg>'
    )
    return svg.encode("utf-8")


# A believable primary tag per wiki; each page also gets its own leading
# keyword, so the demo exercises tag capture -> derive -> print with varied
# values rather than a single repeated label.
_WIKI_TAG = {
    "eng-handbook": "engineering",
    "product-wiki": "product",
    "ops-kb": "operations",
}


def _tags_for(wiki_label: str, page_label: str) -> list[str]:
    base = _WIKI_TAG.get(wiki_label, "wiki")
    keyword = page_label.split("-")[0]
    tags = [base]
    if keyword and keyword != base:
        tags.append(keyword)
    return tags


def _slug(title: str) -> str:
    out = []
    prev_dash = False
    for ch in title.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def _n_images(gi: int) -> int:
    return 0 if gi % 4 == 3 else 1 + (gi % 2)


def _n_files(gi: int) -> int:
    return gi % 3


def _image_src(*, gi: int, wiki_label: str, page_label: str, cross_app: bool) -> str:
    """The `<img>` src for a page's first image. Same-host images point at
    the wiki media route; cross-app ones at the Files app -- both fetchable
    same-deployment URLs the fake serves (so the crawler archives them and
    the reader shows a real image), with the genThumb index in the filename."""
    if cross_app:
        return f"/files/basic/api/library/lib1/document/doc1/media/logo-{gi}.png"
    return f"/wikis/basic/api/wiki/{wiki_label}/page/{page_label}/media/img/diagram-{gi}.png"


def _body_html(*, wiki_title: str, wiki_label: str, page_label: str, gi: int) -> str:
    """The page body the reader lays out: believable topic-specific prose, the
    first image as a figure, the author-styled callout, and a platform-widget
    line. The prose and callout come from `_PAGE_CONTENT[(wiki_label,
    page_label)]` (falling back to generic text for any unlisted page).

    Structural styling (layout, the author callout, figure) is unconditional;
    the **dark theme** is wrapped in `@media screen` so the reader's sandboxed
    iframe looks like the dark prototype on screen, while every PDF path
    (Chromium prints with `print` media, ignoring `@media screen`) renders
    **light, print-appropriate** — no dark page background, black text."""
    paras, note = _PAGE_CONTENT.get((wiki_label, page_label), (_GENERIC_PARAS, _GENERIC_NOTE))
    # First paragraph, then the figure, then any remaining paragraphs — so the
    # image keeps its place near the top and page-image tests stay valid.
    lead = html.escape(paras[0]) if paras else ""
    rest = "".join(f"  <p>{html.escape(p)}</p>\n" for p in paras[1:])
    figure = ""
    if _n_images(gi) > 0:
        cross_app = gi % 2 == 0  # (gi + 0) % 2 === 0
        name = f"{'logo' if cross_app else 'diagram'}-{gi % 9}-0.png"
        src = _image_src(gi=gi, wiki_label=wiki_label, page_label=page_label, cross_app=cross_app)
        caption = html.escape(name) + (" · retrieved from Files" if cross_app else "")
        figure = (
            f'  <figure><img src="{src}" alt="{html.escape(name)}" class="thumb"/>'
            f"<figcaption>{caption}</figcaption></figure>\n"
        )
    return (
        "<style>\n"
        "  /* The author styles their CONTENT, not the page it lands on.\n"
        "     Deliberately no font-size on `body`: an author rule and the PDF\n"
        "     stylesheet share that selector, the author block is emitted\n"
        "     later, so a pixel size here would outrank the print size and\n"
        "     every export would come out at a size nobody chose -- 12pt body\n"
        "     and 24pt headings off a 16px base, ~18% more pages. Line height\n"
        "     and rhythm are the author's; the base size is the renderer's. */\n"
        "  .lotusWiki { line-height: 1.7; }\n"
        "  p { margin: 0 0 16px; }\n"
        "  figure { margin: 22px 0; }\n"
        "  .thumb { display: block; width: 100%; height: 240px; object-fit: cover;\n"
        "           border-radius: 10px; border: 1px solid #cbd2dc; }\n"
        "  figcaption { margin-top: 8px; font: 11px/1.4 ui-monospace, monospace;\n"
        "               color: #667085; }\n"
        "  .authors-note { border-left: 3px solid #c084fc; background: rgba(192,132,252,0.12);\n"
        "                  padding: 10px 14px; border-radius: 6px; margin: 18px 0; }\n"
        "  /* dark reader theme — SCREEN ONLY, so PDFs stay light */\n"
        "  @media screen {\n"
        "    body { background: #12141a; color: #e7e9ee; }\n"
        "    .thumb { border-color: #2a2f3a; }\n"
        "    figcaption { color: #8b93a2; }\n"
        "    .lotusWiki { color: #7dd3fc; }\n"
        "  }\n"
        "</style>\n"
        '<div class="lotusWiki wikiPage">\n'
        f"  <p>{lead}</p>\n"
        f"{figure}"
        f"{rest}"
        f'  <div class="authors-note">{html.escape(note)}</div>\n'
        '  <p><span class="lotusWiki">lotusWiki widget (platform-styled)</span></p>\n'
        "</div>\n"
    )


def _comments(
    *, ids: _IdFactory, clock: _Clock, gi: int, count: int, title: str
) -> list[FakeComment]:
    """genRecord comments: `count` of them (the prototype caps *display* at
    16 but the true total is `count`; we materialise all so the served count
    matches).

    The text comes from `content.page_comment`, written per page title. It
    used to come from one pool of ten lines shared by every page here and
    every blog post the synthesizer produced, which put "The screenshot no
    longer matches the current UI" on pages with no screenshot -- and, since
    "Vision 2026" carries 41 comments, printed those ten lines four times
    over on a single page. A comment marked as the page author's is
    attributed to the page's own author (`AUTHORS[gi%8]`), so an answer to a
    correction comes from the person who wrote the page.

    Everyone else rotates through the OTHER seven authors. The old rotation
    was `AUTHORS[(gi+i)%8]`, which at i=0 is the page's own author -- so
    every page in the demo opened its comment thread with its author talking
    to himself."""
    page_author = _AUTHORS[gi % len(_AUTHORS)]
    others = tuple(a for a in _AUTHORS if a != page_author)
    comments = []
    for i in range(count):
        text, by_author = page_comment(title, i)
        author = page_author if by_author else others[(gi + i) % len(others)]
        comments.append(
            FakeComment(
                uuid=ids.next(),
                author=author,
                content_html=f"<p>{html.escape(text)}</p>",
                published=clock.tick(),
                updated=clock.tick(),
                title=f"Re: comment {i + 1}",
            )
        )
    return comments


def _versions(*, ids: _IdFactory, clock: _Clock, gi: int) -> list[FakeVersion]:
    """genRecord versions: vlabel = 1 + (gi*7)%5 revisions."""
    vlabel = 1 + (gi * 7) % 5
    versions = []
    for v in range(vlabel, 0, -1):
        versions.append(
            FakeVersion(
                uuid=ids.next(),
                version_label=v,
                author=_AUTHORS[(gi + v) % len(_AUTHORS)],
                created=clock.tick(),
                content_html=f"<p>Revision {v}</p>",
            )
        )
    return versions


def _attachments(
    *, ids: _IdFactory, clock: _Clock, gi: int, wiki_label: str
) -> list[FakeAttachment]:
    """genAssets files: `gi % 3` of them, named `{base}-{wiki}.{ext}`."""
    attachments = []
    for i in range(_n_files(gi)):
        base, ext, _badge, content_type = _FILE_POOL[(gi + i) % len(_FILE_POOL)]
        filename = f"{base}-{wiki_label}.{ext}"
        attachments.append(
            FakeAttachment(
                uuid=ids.next(),
                filename=filename,
                content_type=content_type,
                size=((gi + i) * 40 + 30) * 1024,
                author=_AUTHORS[(gi + i) % len(_AUTHORS)],
                created=clock.tick(),
                content=_fake_attachment_bytes(filename, content_type),
            )
        )
    return attachments


def _fake_attachment_bytes(filename: str, content_type: str) -> bytes:
    """Deterministic synthetic bytes for a demo attachment, so the whole
    capture -> blob -> reader-download / PDF-embed path can be exercised. A
    format-appropriate magic header (where cheap) keeps content-type sniffing
    honest; the rest is human-readable so a downloaded file is obviously demo
    content, not a real document."""
    body = (
        f"Demo attachment: {filename}\n"
        f"Declared type: {content_type}\n\n"
        "This is synthetic content generated by the fakeserver to exercise the "
        "attachment capture and download mechanism. It is not a real document.\n"
    ).encode()
    if filename.endswith(".pdf") or content_type == "application/pdf":
        return b"%PDF-1.4\n" + body + b"\n%%EOF\n"
    if filename.endswith(".zip") or content_type == "application/zip":
        return b"PK\x03\x04" + body  # zip local-file-header magic
    return body


def _links_showcase_html(*, wiki_label: str, target_label: str, target_title: str) -> str:
    """A showcase block appended to the demo's first wiki page so an exporter
    can verify, in the reader AND the PDF, that (1) an INLINE `<svg>` renders,
    (2) a clickable AREA inside that SVG works, (3) an in-export wiki link
    (deployment path matching another captured page) becomes an in-document
    anchor, and (4) an external link stays clickable and visibly URL-labelled.
    The internal hrefs use the `/wiki/{label}/page/{label}` path the link
    resolver keys on (`derive/links.py`), so they classify as `in_export`."""
    internal = f"/wikis/basic/anonymous/api/wiki/{wiki_label}/page/{target_label}/entry"
    tgt = html.escape(target_title)
    return (
        '<div class="links-showcase">\n'
        "  <h3>Diagram &amp; links demo</h3>\n"
        "  <p>An inline SVG with a clickable area, plus an in-export link and an\n"
        "     external one — for verifying they all survive to the PDF.</p>\n"
        '  <svg xmlns="http://www.w3.org/2000/svg" width="320" height="120"\n'
        '       viewBox="0 0 320 120" role="img" aria-label="Architecture sketch">\n'
        '    <rect x="0" y="0" width="320" height="120" rx="10" fill="#e7edf6"\n'
        '          stroke="#c084fc"/>\n'
        '    <text x="16" y="30" font-size="13" fill="#334">Architecture (click the box)</text>\n'
        f'    <a href="{internal}">\n'
        '      <rect x="16" y="44" width="150" height="56" rx="8" fill="#c084fc"/>\n'
        '      <text x="30" y="78" font-size="13" fill="#fff">Open ' + tgt + "</text>\n"
        "    </a>\n"
        '    <rect x="186" y="44" width="118" height="56" rx="8" fill="#fff"\n'
        '          stroke="#98a2b3"/>\n'
        '    <text x="200" y="78" font-size="12" fill="#475">this page</text>\n'
        "  </svg>\n"
        f'  <p>Internal wiki link: <a href="{internal}">{tgt}</a>.\n'
        '     External link: <a href="https://example.com/architecture-spec">the spec</a>.</p>\n'
        "</div>\n"
    )


def build_prototype_wikiset() -> WikiSet:
    """The curated, believable demo dataset as a `WikiSet` -- what
    `hcl-serve --demo` crawls, so the demo shows convincing, topic-true
    content produced by the real pipeline."""
    rng = random.Random(0xC0FFEE)
    ids = _IdFactory(rng)
    clock = _Clock(1_767_225_600)  # fixed base (2026-01-01T00:00:00Z), never wall clock

    wikis: list[FakeWiki] = []
    gi = 0  # global item index, matching buildWorklist (a feed then each page)
    for wiki_label, wiki_title, pages in _WIKIS:
        gi += 1  # the wiki's pages feed consumes one gi in the prototype
        wiki = FakeWiki(
            uuid=ids.next(),
            label=wiki_label,
            title=wiki_title,
            created=clock.tick(),
            modified=clock.tick(),
            pages=[],
        )
        last_at_depth: dict[int, str] = {}
        ordinal_by_parent: dict[str | None, int] = {}
        for title, depth, comment_count in pages:
            parent_uuid = last_at_depth.get(depth - 1) if depth > 0 else None
            ordinal = ordinal_by_parent.get(parent_uuid, 0)
            ordinal_by_parent[parent_uuid] = ordinal + 1
            versions = _versions(ids=ids, clock=clock, gi=gi)
            page_label = _slug(title)
            page = FakePage(
                uuid=ids.next(),
                label=page_label,
                title=title,
                parent_uuid=parent_uuid,
                ordinal=ordinal,
                body_html=_body_html(
                    wiki_title=wiki_title, wiki_label=wiki_label, page_label=page_label, gi=gi
                ),
                created=clock.tick(),
                modified=clock.tick(),
                version_label=len(versions),
                author=_AUTHORS[gi % len(_AUTHORS)],
                comments=_comments(ids=ids, clock=clock, gi=gi, count=comment_count, title=title),
                versions=versions,
                attachments=_attachments(ids=ids, clock=clock, gi=gi, wiki_label=wiki_label),
                tags=_tags_for(wiki_label, page_label),
                recommendations=(gi * 3) % 12,
                hits=40 + (gi * 17) % 400,
                anonymous_hits=(gi * 5) % 90,
            )
            wiki.pages.append(page)
            last_at_depth[depth] = page.uuid
            # a deeper level restarts below this page
            for deeper in list(last_at_depth):
                if deeper > depth:
                    del last_at_depth[deeper]
            gi += 1
        wikis.append(wiki)

    # Append the SVG/links showcase to the very first page of the first wiki,
    # linking to a real sibling page so the internal link resolves in-export.
    if wikis and len(wikis[0].pages) >= 2:
        first = wikis[0]
        sibling = first.pages[1]
        first.pages[0].body_html += _links_showcase_html(
            wiki_label=first.label, target_label=sibling.label, target_title=sibling.title
        )

    return WikiSet(wikis=wikis, base_timestamp_ms=1_767_225_600 * 1000)
