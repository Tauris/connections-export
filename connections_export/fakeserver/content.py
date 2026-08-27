"""Written demo content: real posts, real forum threads, real comments.

A demo is the only way anyone judges this tool without a deployment of
their own, so its content has to survive being read. Synthesized prose does
not: one template per app, interpolating only the title, makes a demo
archive of six posts read as the same post six times. Comments fail the same
way one level down -- a shared pool of lines gives a page with no screenshot
a comment about the screenshot, and the demo's busiest page goes round the
pool several times.

Bodies here are written per title, in the voice the title implies: an
incident write-up sounds like an incident write-up, a support question
sounds like somebody stuck at 90%. Comment threads are written per page and
per post, about the thing they are attached to. Titles not covered fall back
to `varied_*`, which composes from several distinct shapes rather than one
-- still no repeated sentence across a page of entries.

Prose only. The caller owns the surrounding markup (the permalink and cover
`<img>` on a post, the signature on a topic, the `<p>` around a comment).
"""

from __future__ import annotations

import hashlib

# --------------------------------------------------------------------
# Blog posts, keyed by title (`synth._BLOG_POST_TITLES`)
# --------------------------------------------------------------------

BLOG_BODIES: dict[str, tuple[str, ...]] = {
    "What we shipped in Q3": (
        "Three months, forty-one merged changes, and one migration we had "
        "been putting off since February. Here is what actually landed.",
        "The headline is the new export pipeline. It replaces the nightly "
        "batch job that nobody wanted to touch, and it runs incrementally, "
        "so a failed run no longer means re-fetching everything from the "
        "beginning. Median run time went from 51 minutes to just under 9.",
        "Less visible but more useful day to day: attachments are now "
        "captured with their original filenames. We had been storing them "
        "under content hashes, which was correct and completely unusable "
        "when somebody asked for “the budget spreadsheet Ana sent”.",
        "What did not land: the search rewrite. We under-estimated how much "
        "behaviour was encoded in the old ranking code, and half-migrating "
        "it would have been worse than leaving it alone. It moves to Q4 with "
        "a proper spike in front of it this time.",
    ),
    "Postmortem: the Friday outage": (
        "On Friday the export service was unavailable for 3 hours and 12 "
        "minutes, from 14:06 to 17:18 UTC. No data was lost. Runs queued "
        "during the window were replayed on Saturday morning.",
        "<b>What happened.</b> A configuration change intended to raise the "
        "connection pool limit was applied with the value in the wrong unit "
        "— seconds where the setting expected milliseconds. The pool "
        "treated every connection as already expired and opened a new one "
        "per request until the database refused further connections.",
        "<b>Why it took three hours.</b> The dashboards were green. Every "
        "instance was healthy, memory was flat, and the error rate was zero "
        "because requests were queueing rather than failing. We were looking "
        "for a crash for the first ninety minutes. The thing that finally "
        "pointed at it was a colleague asking why the connection count "
        "graph looked like a staircase.",
        "<b>What we are changing.</b> The setting now rejects values below "
        "1000 outright. Queue depth is on the primary dashboard rather than "
        "three clicks into a secondary one. And configuration changes to the "
        "data layer get the same review as code changes, which is a rule we "
        "had informally and did not follow.",
        "Thanks to everyone who stayed late. The full timeline is on the "
        "incident page if you want the detail.",
    ),
    "Why we moved off the old exporter": (
        "The old exporter served us for four years, which is longer than "
        "anybody expected of something originally written over a weekend to "
        "unblock a single customer.",
        "It broke because of an assumption baked in early: that a page has "
        "one version and one author. That was true of the content we had in "
        "2022. It stopped being true the moment teams started using the wiki "
        "as a living document, and every workaround after that was a special "
        "case stacked on the previous special case.",
        "The rewrite is not more clever. It is mostly the same logic with "
        "the version history treated as a first-class thing rather than "
        "something to flatten away. That one change deleted about 600 lines "
        "of conditionals.",
        "If you are maintaining something similar: the tell was that every "
        "bug report needed a new branch in the same function. That is the "
        "code telling you the model is wrong, and it is cheaper to listen "
        "early.",
    ),
    "Migrating our test suite to pytest": (
        "We finished moving the last of the unittest-style tests across this "
        "week. 1,180 tests, about three weeks of background effort, no "
        "behaviour changes intended and two found by accident.",
        "The mechanical part was easy — most of it was a script. The "
        "interesting part was the fixtures. The old suite had a base class "
        "eleven levels deep whose `setUp` did database work that roughly a "
        "third of the tests did not need. Untangling that is where the real "
        "time went, and it is also where the two bugs surfaced: two tests "
        "had been passing because a sibling test happened to leave the right "
        "row behind.",
        "Runtime is down from 6m40s to 1m55s, almost entirely from not doing "
        "that unnecessary setup. Parallelism helped less than we assumed.",
    ),
    "Three lessons from the migration": (
        "Now that the dust has settled, three things worth writing down "
        "while they are still uncomfortable enough to remember.",
        "<b>One: the estimate was wrong because the unknown was not the "
        "code.</b> We sized the work by counting call sites. The actual cost "
        "was in the two systems nobody owned, where finding out what "
        "depended on them took longer than changing them.",
        "<b>Two: running both in parallel was worth the cost.</b> It was "
        "ugly for six weeks and it meant every fix landed twice. It also "
        "meant we could stop at any point, and we very nearly needed to.",
        "<b>Three: the rollback plan was never tested and would not have "
        "worked.</b> We wrote it, everyone approved it, and when we finally "
        "rehearsed it in week five it failed on the first step. Write the "
        "plan, then actually run it.",
    ),
    "How we cut build times in half": (
        "The build went from 14 minutes to 6. Most of the win came from one "
        "unglamorous place: we were rebuilding dependencies that had not "
        "changed since the previous run.",
        "The cache key included a timestamp. It had been added to fix a "
        "stale-artifact problem in 2023 and it worked — by ensuring the "
        "cache never hit. Removing it and fixing the actual staleness bug "
        "took an afternoon and saved about five minutes per build.",
        "The rest was splitting the test job by directory so the slow "
        "integration tests stop blocking the fast unit tests, and deleting a "
        "linting step that duplicated what the formatter already enforced.",
        "None of this was clever. It was reading the build log line by line, "
        "which nobody had done in a year.",
    ),
    "Retiring the legacy importer": (
        "The legacy importer is retired. It has been read-only since "
        "March and unused since June, and it is removed entirely at the end "
        "of the month.",
        "If you still have a scheduled job pointing at the old endpoint, it "
        "has been returning a redirect since March and will start returning "
        "410 on the 30th. The migration guide covers the two cases that are "
        "not a straight swap: custom field mappings, and anything relying on "
        "the old importer's silent truncation of long titles.",
        "That second one matters more than it sounds. The old importer cut "
        "titles at 128 characters without telling anyone. The new one keeps "
        "them, so if you were relying on truncated titles as identifiers, "
        "they are about to get longer.",
    ),
    "On-call, six months in": (
        "We changed the rotation in the spring: one week in eight instead of "
        "one in four, two people on at a time instead of one, and a hard "
        "rule that whoever is on call does no planned work.",
        "The last part was the one people argued about and the one that "
        "mattered most. Before, being on call meant doing your normal work "
        "badly while interrupted. Now it means being available, and the "
        "week is planned as if that person is away.",
        "Page volume is down 40%, which is less impressive than it sounds "
        "— about half of that is one noisy alert we finally fixed rather "
        "than any structural improvement. The number I care about more is "
        "that nobody has swapped out of a shift since May.",
    ),
    "Welcome to the new starters": (
        "Four people joined this month, which is the largest intake we have "
        "had in one go. Rather than the usual round of introductions in "
        "chat, we asked each of them to write a line about what they are "
        "looking forward to.",
        "They are spread across three teams and two time zones, and all four "
        "are pairing with someone for their first fortnight. If you are that "
        "someone: the onboarding checklist was rewritten in August and the "
        "old one is out of date in several places, so please use the current "
        "version rather than the copy in your bookmarks.",
        "One ask of everyone else. The single most common piece of feedback "
        "from the last intake was that internal docs assume context nobody "
        "writes down. If a new starter asks you something and the answer is "
        "not findable, that is a documentation bug — please file it.",
    ),
    "Introducing the new dashboard": (
        "The new dashboard is available to everyone from today. The old one "
        "stays reachable for another two months.",
        "The main change is that it answers the question people actually "
        "arrive with. The old dashboard opened on a system-health view, "
        "which is what we cared about when we built it and almost never what "
        "anyone else needs. The new one opens on your own recent activity, "
        "with system health one click away.",
        "Also new: saved views. If you have a filter combination you rebuild "
        "every Monday, save it once and it is in the sidebar.",
        "We are aware the export button has moved and that this is annoying. "
        "It is in the overflow menu at the top right, and it will move back "
        "into the toolbar in the next release now that we have seen how "
        "often it is used.",
    ),
    "Faster search, fewer clicks": (
        "Search results now come back in under 200ms for the large majority "
        "of queries, down from something closer to a second and a half.",
        "The change is that we stopped searching everything by default. The "
        "old behaviour queried every content type and every archive, then "
        "ranked the combined set. In practice people search within a scope "
        "they already have in mind, so the scope selector is now applied "
        "before the query rather than as a filter afterwards.",
        "Two smaller things people asked for repeatedly: pressing Enter on a "
        "single result opens it directly instead of showing a list of one, "
        "and recent searches persist across sessions.",
    ),
    "What's new this release": (
        "A short release, most of it maintenance, with two changes worth calling out.",
        "Attachments over 100MB are now supported. The previous limit was "
        "not a deliberate design decision, just the point at which the "
        "upload timed out, which is a bad reason for a limit to exist.",
        "Second, exports now include comment threads by default. Previously "
        "you had to opt in, and the option was buried far enough that most "
        "people discovered it after their first export was missing the "
        "discussion they wanted.",
        "Full changelog is linked below as usual.",
    ),
    "Feedback we heard and acted on": (
        "We ran the survey in July and got 214 responses, which is roughly "
        "triple the last one. Here is what came back and what we did.",
        "<b>“I cannot tell whether an export finished.”</b> The "
        "clearest single theme. Exports now report progress per item rather "
        "than going quiet until they finish, and a completed export says so "
        "rather than just stopping.",
        "<b>“The naming is confusing.”</b> Also fair. “Sync”, "
        "“import” and “capture” were used interchangeably for "
        "three different things. We picked one word per concept and the docs "
        "have been rewritten around them.",
        "<b>“It is too slow on large archives.”</b> Real, and only "
        "partly addressed. Listing is faster; opening a very large archive "
        "still is not. That work is scheduled rather than done, and we would "
        "rather say so than claim it.",
    ),
    "A cleaner onboarding flow": (
        "Setting up for the first time used to take nine screens. It now "
        "takes three, and two of those are optional.",
        "Most of what we removed was configuration that had a sensible "
        "default nobody ever changed. Asking somebody to make a decision on "
        "their first day, before they have context to decide with, does not "
        "make the product more flexible — it just moves the decision to "
        "the worst possible moment.",
        "Everything removed from setup is still available in Settings. "
        "Nothing was taken away; it stopped being a gate.",
    ),
    "Dark mode is here": (
        "Dark mode ships today, following the system setting by default with "
        "a manual override in Settings.",
        "It took longer than it should have because the first attempt was an "
        "inverted filter over the existing styles. That is quick and it "
        "looks wrong in a way that is hard to point at — shadows invert, "
        "images go strange, and every brand colour shifts hue. The version "
        "shipping today is a real second palette.",
        "Two known rough edges: user-supplied content keeps its own colours, "
        "so a light-background image still looks light against a dark page, "
        "and a handful of status badges need another pass on contrast.",
    ),
    "Roadmap check-in": (
        "Halfway through the year, so: what we said we would do, and where that actually stands.",
        "<b>Shipped.</b> The export pipeline rewrite, dark mode, the "
        "onboarding rework, and comment capture. All roughly on the timing "
        "we gave in January.",
        "<b>Slipped.</b> Search ranking, which we have now started twice and "
        "stopped twice. It is genuinely harder than we scoped and we are "
        "going to leave it out of commitments until there is a design we "
        "believe in.",
        "<b>Dropped.</b> The plugin API. Not because it is a bad idea, but "
        "because every conversation about it turned into a different feature "
        "request, which usually means the underlying need is not what the "
        "name suggests. We are going back to talk to the people who asked.",
    ),
    # -- Team Notes (an internal team blog: warm, first-person plural) --
    "How we run retros now": (
        "We changed the retro format in April, after a run of meetings that "
        "reliably produced a list of complaints and no changes.",
        "The shape now is fifteen minutes of writing in silence, ten minutes "
        "grouping what came out, and the remaining half hour on exactly one "
        "theme. Everything else is kept, in writing, and deliberately not "
        "discussed.",
        "Picking one theme is the part that made the difference. A retro that "
        "ends with nine actions ends with nine actions nobody does, and the "
        "next retro opens by reading them out again, which is how a meeting "
        "teaches people it is theatre.",
        "The facilitator now rotates in alphabetical order rather than by "
        "volunteering, because volunteering meant the same two people every "
        "time. Nobody has needed the guide we wrote for it, but it is there.",
    ),
    "New faces on the team": (
        "Two people joined us in the last fortnight: one on the ingestion "
        "side, one splitting their week between support and documentation.",
        "Both are pairing rather than being handed a starter ticket. We tried "
        "starter tickets for a year and the honest result was that people "
        "learned the tracker rather than the system.",
        "The one thing to ask of everybody else: we use about thirty "
        "abbreviations in chat and roughly six of them are written down "
        "anywhere. If you catch yourself using one this month, spell it out "
        "once. It costs you four words.",
    ),
    "Our hybrid-work experiment": (
        "We ran a fixed-days experiment for a quarter — two set days in the "
        "office, the rest wherever suits — and we are keeping it, with one "
        "change.",
        "Fixed days beat the previous arrangement, which was “come in when "
        "it is useful”. That sounds more flexible and in practice meant "
        "everybody travelled in to sit on calls, because coordinating four "
        "calendars informally is harder than agreeing on a day once.",
        "The change is moving the second day from Thursday to Tuesday. "
        "Thursday collided with the design review and with roughly half the "
        "team's school runs, so attendance was consistently thin and the "
        "people who came in felt like they had wasted the trip.",
        "We will look at it again in January. Nothing here is a policy; it is "
        "a thing we are trying that has so far worked.",
    ),
    "Notes from the offsite": (
        "Two days, thirty-one people, and one whiteboard photograph that nobody can now read.",
        "The best session was not on the agenda. A conversation over lunch "
        "about why nobody trusts the staging environment turned into an hour "
        "with everyone who touches it in one room, which is the first time "
        "that has happened.",
        "The concrete outcome: staging gets a real owner and a weekly reset, "
        "and if it is broken it gets fixed or removed — the state we have "
        "been in for a year, where it is up but nobody believes it, is worse "
        "than not having it.",
        "Photographs and the raw notes are in the shared folder. The notes "
        "are unedited, so they contradict themselves in places; where they "
        "do, the wiki page is the version that was agreed.",
    ),
    "What we're reading this month": (
        "The reading group has settled into a rhythm, so here is what has "
        "been going round the team this month.",
        "A paper on queueing theory that explains, in about six pages, why "
        "our build queue gets dramatically worse at exactly the point people "
        "start complaining rather than gradually before it. Two of us have "
        "now used the word “utilisation” in a planning meeting.",
        "Also a long write-up of somebody else's failed migration, which is "
        "more useful than the successful ones because it is specific about "
        "the point at which they should have stopped and did not.",
        "Next month's pick is open. Add suggestions to the page; the only "
        "rule we have kept is that it has to be readable in an hour, because "
        "anything longer quietly becomes nobody reading it.",
    ),
    "A day in the life of support": (
        "One of us sat with the support team for a full shift last week and "
        "wrote down what actually came in.",
        "Forty-one conversations. Six were defects. The rest split roughly "
        "evenly between “where is the thing”, “I do not have permission "
        "to the thing”, and people checking that something that worked "
        "correctly had in fact worked.",
        "That third category is the interesting one for us. Every one of "
        "those is a message the product should have sent and did not. An "
        "export that finishes silently generates a support ticket about as "
        "reliably as an export that fails.",
        "The takeaway we are acting on: before adding anything new this "
        "quarter, we are going through the flows that end in silence and "
        "giving each of them an ending.",
    ),
    "Hiring: what we look for": (
        "We have three roles open and get asked what the loop is, so here it is in full.",
        "Two conversations and one piece of work. The work is a small change "
        "in a codebase you have never seen, with a deliberately incomplete "
        "description, done together rather than alone. What we are watching "
        "is what you do when the description runs out.",
        "We stopped asking algorithm puzzles two years ago. Not on principle "
        "— they were simply uncorrelated with anything. The people who did "
        "well on them did well and badly here in the same proportion as "
        "everyone else, which is an expensive way to learn nothing.",
        "If you are referring somebody: the single most useful thing you can "
        "write is what you have seen them do, not what you think they are "
        "like. One paragraph of the former beats a page of the latter.",
    ),
    "Saying goodbye to a teammate": (
        "After six years, one of our longest-serving colleagues is moving on "
        "at the end of the month.",
        "Most of what they built is invisible, which is the nature of the "
        "work: the retry logic that stopped us losing overnight runs, the "
        "release checklist everybody grumbles about and nobody skips, and "
        "about four years of patient answers in the support forum.",
        "Handover has been going for six weeks and is genuinely done, which "
        "is rare enough to be worth naming. Ownership of the ingestion "
        "pipeline moves to the platform group; the runbooks were rewritten "
        "rather than copied, and the on-call rota is already updated.",
        "Drinks on the last Thursday. Everyone welcome, including people who "
        "only ever met them in a review thread.",
    ),
    # -- Release Notes (terse, factual, no reflection) --
    "Version 8.0: what's inside": (
        "8.0 is available now. The upgrade procedure is in a separate post; "
        "this is the list of what changed.",
        "<b>Added.</b> Incremental exports. Comment capture, on by default. "
        "Per-item progress reporting. A dark theme that follows the system "
        "setting. Attachment retry limits, configurable per run.",
        "<b>Changed.</b> The attachment size ceiling is now 200MB, up from "
        "100MB. The default delay between requests is 250ms, previously "
        "none. Archive metadata is written after each item rather than at "
        "the end of a run.",
        "<b>Removed.</b> The legacy importer. The v1 status endpoint. The "
        "“compact” archive format, which had no known users and no test "
        "coverage.",
    ),
    "Patch notes: stability fixes": (
        "8.0.3. Defect fixes only — no schema changes, no API changes, no configuration changes.",
        "Fixed: a crawl aborting instead of continuing when a page returned "
        "an empty body. Fixed: the attachment retry limit being read but not "
        "applied. Fixed: progress reporting able to exceed 100% on runs where "
        "an item was counted twice.",
        "Also fixed: the archive index growing an empty entry for every "
        "skipped item, which made a large archive slower to open for no "
        "benefit. Existing archives are repaired on first open.",
        "Upgrade at your convenience. Nothing here is a security fix.",
    ),
    "Deprecating the old API": (
        "The v1 API is deprecated as of 8.0. It will be removed in 9.0, and "
        "not before twelve months from today.",
        "Affected: the entry listing, the attachment metadata endpoint, and "
        "the status endpoint. The remaining v1 paths were already redirects "
        "and are unchanged.",
        "Every v1 response now carries a Deprecation header and a Sunset "
        "date. If you have a client you are not sure about, look for those "
        "headers in its logs rather than auditing the code.",
        "The v2 equivalents are documented, and the shapes are the same "
        "except for pagination, which is cursor-based rather than page-based. "
        "That is the one change that needs real work on the client side.",
    ),
    "Breaking change: auth headers": (
        "This release breaks existing clients. Please read before upgrading.",
        "The token header changes from a custom header to standard bearer "
        "authorization. The token value itself is unchanged; only where you "
        "put it changes.",
        "Both forms are accepted through 8.1. From 8.2 only the standard form "
        "is accepted, and the custom header is ignored rather than rejected, "
        "so a client that sends only the old header will look unauthenticated "
        "rather than misconfigured.",
        "A request carrying both is rejected outright. We considered "
        "preferring one, and rejected that: silently picking a winner between "
        "two credentials is how you get a client that has been sending an "
        "expired token for a year without anyone noticing.",
    ),
    "Performance improvements this release": (
        "Measured on the reference dataset: 4,000 pages, 1,900 attachments, one wiki.",
        "Listing the wiki: 38s to 6s. Full export including attachments: "
        "41 minutes to 17. Opening the finished archive in the reader: "
        "9s to under 2.",
        "Two changes account for almost all of it. The page feed is now "
        "requested with pagination rather than in one unbounded call, which "
        "is what made listing slow and memory spiky. And attachment metadata "
        "arrives with the listing instead of being fetched per item, "
        "eliminating one round trip per attachment.",
        "No configuration change is required to get any of this. Peak memory "
        "is down by roughly a third as a side effect.",
    ),
    "Bug bash results": (
        "Two hours, nineteen participants, 64 issues filed.",
        "Of those: 41 accepted, 12 were duplicates of open issues, 7 were "
        "already fixed on the main branch, and 4 turned out to be correct "
        "behaviour that reads as a bug — which we have logged separately, "
        "because a thing four people misread in two hours is a design defect "
        "even if the code is right.",
        "Seven were fixed during the session itself. The most-reported single "
        "issue was the export button's new location, filed independently by "
        "five people.",
        "Everything is tagged so you can filter to it. Thanks to everyone who "
        "spent an afternoon deliberately breaking things.",
    ),
    "Upgrade guide for 8.0": (
        "Upgrade in place. Back up the archive directory before you start — "
        "not because we expect trouble, but because the metadata format "
        "changes and 7.x cannot read the new one.",
        "Steps: pause scheduled crawls, upgrade the package, run the archive "
        "migration once, resume. The migration is idempotent and reports what "
        "it converted.",
        "Rollback: 7.x will open an 8.0 archive and ignore the comment index, "
        "so you lose comment search until you upgrade again but you do not "
        "lose data. Anything captured by 8.0 after the downgrade will be "
        "invisible to 7.x.",
        "Expected downtime: none for the reader. Scheduled crawls should be "
        "paused for the duration of the migration, which takes about a minute "
        "per 10,000 items.",
    ),
    "Known issues and workarounds": (
        "Open issues in 8.0, with workarounds where one exists. This page is "
        "updated as they are fixed rather than superseded by a new post.",
        "Titles containing combining marks are normalised inconsistently "
        "between the listing and the archive index, so such a page can be "
        "exported and then not found by search. Workaround: search by label "
        "rather than title. Fix expected in 8.0.4.",
        "A scheduled crawl authenticated with a token older than its lifetime "
        "fails partway with a transport error and no detail. Workaround: use "
        "a service account for scheduled runs. The error message is being "
        "fixed regardless.",
        "The reader renders a blank page for archives written by a 7.x "
        "pre-release. Workaround: run the archive migration, which rewrites "
        "the index. Affected archives are rare; the pre-release in question "
        "was available for nine days.",
    ),
    # -- Field Report (first person, on site, observational) --
    "Notes from a customer visit": (
        "Spent Tuesday on site with a team that has been running this for "
        "about eight months. Writing it up while it is fresh, unedited.",
        "The first thing I noticed is that nobody uses the dashboard. They "
        "have a wall-mounted screen showing a spreadsheet that somebody "
        "updates by hand each morning from our export. When I asked why, the "
        "answer was that the spreadsheet has the two numbers they care about "
        "and the dashboard has forty.",
        "The second thing: they run every export twice. Once to see what "
        "fails, once for real. That is a rational response to a tool that "
        "cannot tell you in advance what it will not be able to reach, and it "
        "doubles their load on their own server for no benefit to anyone.",
        "I came back with a shorter list than I expected. A dry-run mode and "
        "a way to pin two metrics would address most of what I saw, and "
        "neither is on the roadmap.",
    ),
    "What our biggest client taught us": (
        "Our largest deployment by an order of magnitude has been running for "
        "two years, and almost everything we believe about scale we learned "
        "from them rather than from testing.",
        "The lesson that reshaped the product: their bottleneck was never "
        "throughput. It was the review step afterwards. We had optimised the "
        "part that takes an hour and left untouched the part that takes three "
        "people a week, because the hour was the part we could see.",
        "Second lesson, harder to act on: they never upgrade to a release "
        "with no changelog entry they care about. Which means they skip "
        "versions, which means every upgrade they do is a jump of four, which "
        "is where the risk actually accumulates.",
        "We have started writing the changelog for someone deciding whether "
        "to upgrade rather than for someone who already has.",
    ),
    "Rolling out to 500 users": (
        "The rollout finished last week: 500 people across six departments, "
        "over eleven weeks, with two rollbacks.",
        "What worked was going department by department in ascending order of "
        "how much they cared. Starting with the enthusiasts is tempting and "
        "teaches you nothing, because enthusiasts route around problems "
        "without reporting them.",
        "Both rollbacks were the same cause: a permissions model that made "
        "sense centrally and did not survive contact with a department that "
        "shares one mailbox between eleven people. We are not going to "
        "generalise from that, but we should have asked.",
        "Eleven weeks was roughly double the estimate. The estimate assumed "
        "the training would be the slow part. The slow part was waiting for "
        "each department's own change window.",
    ),
    "Lessons from a failed pilot": (
        "A three-month pilot ended in March without going forward. Writing it "
        "up because we learn more from these and publish them less.",
        "The proximate cause is easy: the content they wanted to archive "
        "lived in a system we do not support and, after looking at it "
        "properly, should not try to. We knew that in week two and spent nine "
        "more weeks not saying it.",
        "The real cause is that the pilot had no stated failure condition. "
        "Everyone involved was measured on it going well, so the weekly "
        "meeting steadily converged on discussing the parts that were going "
        "well. Nobody lied about anything.",
        "Every pilot we start now names, in writing and before it begins, the "
        "thing that would make us stop. It has already stopped one, six weeks "
        "earlier than the old process would have.",
    ),
    "Support tickets we learned from": (
        "Read a quarter's worth of tickets end to end rather than as a queue. "
        "Different picture entirely.",
        "The single largest category is not a problem with the software at "
        "all: it is people asking whether something is finished. Second "
        "largest is permissions, where the software behaves correctly and "
        "reports nothing useful about why.",
        "One ticket is worth quoting almost in full: “It worked yesterday, "
        "it did not work today, and I cannot tell you what I did "
        "differently.” That was three separate customers in the same month, "
        "and in all three cases the difference was a credential quietly "
        "expiring.",
        "None of this is exotic. The pattern is that the tool knows the "
        "answer and does not say it out loud, and every instance of that "
        "becomes somebody's afternoon.",
    ),
    "A week shadowing the help desk": (
        "Five days on the help desk, not answering, just watching and taking "
        "notes. I have worked on this product for three years and I have "
        "never seen it used by someone who did not want to use it.",
        "The thing I keep thinking about is how much of the day is spent "
        "reconstructing context. Somebody writes in with a screenshot of an "
        "error, and the first twenty minutes go on establishing which "
        "version, which deployment, and what they had actually clicked.",
        "The fix is not a better error message, or not only. It is that the "
        "error should be copyable as text that carries its own context. Every "
        "screenshot in that queue is a message we failed to make quotable.",
        "Second observation, less actionable: the help desk knows exactly "
        "which parts of the product are bad, in priority order, and has never "
        "been asked.",
    ),
    "What partners are asking for": (
        "Summarising six partner conversations from the last quarter, since "
        "they have converged more than I expected.",
        "All six asked for the same thing in different words: a stable, "
        "documented way to be told when an export finishes, so they can start "
        "their own work without polling. Three had already built polling and "
        "described it apologetically.",
        "Four asked about running against multiple deployments from one "
        "configuration. That is a bigger change than it sounds — it touches "
        "authentication, archive layout and the reader — and it is the one I "
        "would prioritise, because everyone asking is already doing it badly "
        "with shell scripts.",
        "Nobody asked for a plugin API. It is worth saying so plainly, "
        "because it is what we assumed they wanted.",
    ),
    "Field notes: the EU rollout": (
        "The EU deployment went live in June. Notes from the six weeks around "
        "it, mostly on the things that were not technical.",
        "The technical part was a data residency configuration and a second "
        "archive location, and it took about a fortnight. The other four "
        "weeks were spent establishing who was allowed to approve that "
        "configuration, which turned out to be a question with three "
        "plausible answers and no owner.",
        "Practical detail worth passing on: date formats in exported "
        "filenames caused more confusion than anything else, because two "
        "teams were reading the same name as day-month and month-day. We now "
        "write dates one way everywhere and it is not either of those.",
        "Everything is running. If you are doing something similar, budget "
        "the approval conversation as work, with a name against it, rather "
        "than as a thing that will happen alongside the work.",
    ),
    # -- Customer Stories (third person, about a customer, honest) --
    "How Acme cut onboarding time in half": (
        "Acme's engineering group brings on roughly forty people a year, and "
        "until last spring each of them spent their first fortnight hunting "
        "for documentation across three wikis and a shared drive.",
        "The change was not a tool. It was deciding that one of the three "
        "wikis was authoritative and archiving the other two, in a form "
        "people could still read, so that “it is in the archive” was a real "
        "answer rather than a way of saying it is gone.",
        "Their own measure of onboarding time — days until a new joiner "
        "merges a change without pairing — went from eleven to five. Their "
        "lead is careful to point out that the archive gets some of that "
        "credit and the pairing rota gets the rest.",
        "The part they would do differently: they archived before agreeing "
        "what was authoritative, which meant a month where nobody was sure "
        "whether the archive or the wiki was current.",
    ),
    "A migration that just worked": (
        "It is worth writing up the boring ones. A 12,000-page migration ran "
        "over a weekend and finished with no incidents, no rollback, and a "
        "reconciliation that matched on the first attempt.",
        "They did three things unusually. They ran the whole migration "
        "against a copy first, and not as a smoke test — the full set, timed. "
        "They fixed the seventeen failures that produced before touching the "
        "real run. And they wrote the reconciliation query before the "
        "migration rather than after.",
        "That last one is the trick. A reconciliation written afterwards "
        "tends to check the things that went well, because you already know "
        "what happened.",
        "Total effort was about three weeks for two people, most of it before "
        "the weekend in question. The weekend itself was, by their account, "
        "dull.",
    ),
    "From spreadsheets to a real workflow": (
        "A twelve-person team tracked every archive request in one "
        "spreadsheet for four years. It had 900 rows, six colour conventions, "
        "and two columns whose meaning was known to one person.",
        "What they replaced it with is deliberately small: a request form, a "
        "queue, and a status that is either waiting, running or done. They "
        "resisted adding priorities, and say that was the hardest decision "
        "and the right one — the spreadsheet's priority column had been "
        "ignored for years while still being filled in.",
        "The measurable outcome is that the median request now takes four "
        "days instead of nineteen, almost entirely because requests stop "
        "sitting unnoticed.",
        "They kept the spreadsheet, read-only, as history. Their advice: do "
        "that, and put a link to it at the top of the new thing, or you will "
        "spend six months answering questions from it anyway.",
    ),
    "Scaling support without scaling headcount": (
        "Their user base roughly tripled over eighteen months. The support "
        "team went from four people to five.",
        "Most of that is one habit: every ticket that could have been "
        "answered by a page ends with someone writing or fixing that page, "
        "and the time to do it is part of the ticket rather than a "
        "nice-to-have afterwards. Their handling time went up. Their volume "
        "went down more.",
        "The second thing is a public queue. Customers can see existing "
        "reports, which cut duplicate tickets by about a third and, less "
        "expectedly, cut the temperature of the ones that remained — a "
        "problem with four other people on it reads differently from one that "
        "feels ignored.",
        "What they warn about: this only works if the documentation is "
        "genuinely editable by the support team. Where it needed engineering "
        "review, the habit died within a month.",
    ),
    "Why they switched from the old tool": (
        "A mid-sized customer moved off a tool they had used for six years. "
        "They were candid about why, and it was not features.",
        "The old tool could not tell them what it had failed to capture. It "
        "produced an archive, the archive was mostly right, and there was no "
        "way to know what was missing without checking by hand. That is "
        "tolerable until an auditor asks.",
        "Their evaluation was essentially one test: break something on "
        "purpose, run the export, and see whether the output says so. Two of "
        "the four tools they tried reported the failure clearly. That "
        "narrowed the choice faster than any feature comparison.",
        "They also note that migrating six years of archives took longer than "
        "the evaluation, and that they would build the export-from-old-tool "
        "step into the trial next time rather than treating it as a detail.",
    ),
    "Small team, big rollout": (
        "Three people, 2,000 users, eight weeks. The interesting part is what they did not do.",
        "They did no training sessions. Instead they wrote four short pages, "
        "put them where people already look, and sat in an open call for an "
        "hour a day for the first three weeks. Attendance at the call was "
        "usually zero and occasionally decisive.",
        "They did not migrate historical content at first. New work went into "
        "the new system, old content stayed where it was and was archived "
        "later, once nobody was watching. This is the choice most teams find "
        "hardest and it removed the deadline pressure entirely.",
        "Their summary: “Two thousand users is not two thousand decisions. "
        "It is about six decisions and a lot of patience.”",
    ),
    "The integration that saved us hours": (
        "A customer's platform team wired their ticket system to their "
        "archive so that closing a ticket attaches the relevant exported "
        "thread to it automatically.",
        "It is perhaps 200 lines. It replaced a step where somebody found the "
        "thread, exported it, downloaded the file, and attached it by hand — "
        "about four minutes, done maybe thirty times a week, which is a "
        "person-week a quarter spent on copying.",
        "The detail worth stealing: it attaches a link plus a rendered copy, "
        "not just a link. Links to a system the person reading the ticket in "
        "two years may not have access to are a familiar kind of "
        "disappointment.",
        "They have offered the code to anyone who asks. It assumes their "
        "ticket system, but the shape transfers.",
    ),
    "What success looks like a year in": (
        "We went back to a customer twelve months after their rollout to ask "
        "what had actually changed. The answers were smaller and more "
        "convincing than the ones we get at three months.",
        "Nobody talked about time saved. What they talked about was that "
        "arguments now end differently: somebody looks up what the page said "
        "in March rather than asserting what it said in March, and the "
        "conversation is over in a minute.",
        "The usage numbers are unremarkable and have been flat since month "
        "four. Their view is that this is what a utility looks like, and that "
        "a tool with growing engagement in year two would worry them.",
        "One warning from them: the archive is now load-bearing, and nobody "
        "has tested restoring it. They said this to us, unprompted, and then "
        "scheduled the test. Consider this your reminder.",
    ),
    # -- Design Diary (reflective, first person singular, in progress) --
    "Rethinking the empty states": (
        "I have been redrawing the empty states this week, which is one of "
        "those jobs that looks like polish and turns out to be a design "
        "review of the whole feature.",
        "The old ones all said a version of “Nothing here yet”. That is "
        "accurate and useless. An empty screen is almost always one of three "
        "situations — you have not done the thing, the thing is in progress, "
        "or a filter is hiding everything — and they need entirely different "
        "answers.",
        "The third case is the one that was quietly costing us. A filtered "
        "list with no results looked identical to a system with no data in "
        "it, which is how somebody concludes their export produced nothing.",
        "Still unresolved: whether an empty state should offer the action "
        "that fills it. It reads as helpful in the first case and pushy in "
        "the other two, and I have not found a phrasing that works for all "
        "three.",
    ),
    "A new type scale": (
        "We had eleven distinct font sizes, which is nine more than anyone "
        "chose. Most of them arrived as a one-off decision that then got "
        "copied.",
        "The new scale has six steps. Picking the ratio took an afternoon; "
        "deciding what each step is <i>for</i> took a fortnight, and that is "
        "the part that will keep it from drifting back. A step with a stated "
        "job is one somebody can argue with.",
        "The visible change is smaller than I expected. Body text is a "
        "half-step larger, headings are a full step smaller, and the gap "
        "between them narrowed — which reads as calmer without reading as "
        "flat.",
        "The thing I got wrong first time: I set the scale in isolation and "
        "it fell apart against real content, where a nine-word heading wraps "
        "and a two-word one does not. Second attempt was built against the "
        "longest titles in the demo data.",
    ),
    "Why we simplified the nav": (
        "The navigation had nine top-level items. It now has five, and two of "
        "the four that went away were things I personally argued for adding.",
        "What convinced me was watching people use it. Nobody scanned the "
        "list. They went to the item they already knew and, if it was not "
        "there, used search — which means the other eight items were paying "
        "rent as decoration for everyone who was not looking for them.",
        "The four that moved are not gone; they live under the section they "
        "belong to. That is worse for someone who used them daily and I have "
        "not found a way to avoid that trade rather than just soften it.",
        "The measurable effect so far is a small increase in search use and "
        "no measurable change in anything else, which is roughly what I would "
        "expect from a change like this if it is working.",
    ),
    "Prototyping in the open": (
        "We started putting unfinished prototypes where anyone can see them, "
        "with a banner saying what is fake. Six weeks in, some notes.",
        "The feedback got better and less comfortable. When people see a "
        "finished-looking mock they comment on colour; when they see "
        "something obviously rough they tell you the flow is wrong. I knew "
        "this was true and did not really believe it until the comments "
        "changed.",
        "It also surfaces disagreements early, which is the point and is "
        "unpleasant in week one. Two prototypes were abandoned after a day "
        "because somebody who was not in the room said the thing that ends "
        "them.",
        "The rule we have added: a prototype gets a date after which it is "
        "deleted. Without that they accumulate, and an old prototype that "
        "looks plausible is a rumour with a URL.",
    ),
    "Color contrast, take three": (
        "Third attempt at the palette. The first two passed the automated "
        "check and were still hard to read, which is a useful thing to have "
        "learned twice.",
        "The check tells you a pair of colours meets a ratio. It does not "
        "tell you that the badge using that pair is eleven pixels tall, sits "
        "on a tinted row, and is the only thing on screen carrying that "
        "information. All three of our worst offenders passed.",
        "So this pass was done at the component level with real content, and "
        "the palette came out of that rather than the other way round. Fewer "
        "colours, two of them noticeably heavier than I would choose on a "
        "swatch page.",
        "Dark mode remains harder. A palette that is comfortable in a bright "
        "room is washed out in a dim one, and we have no way to know which "
        "the reader is in. For now it is tuned for the dim room, on the "
        "grounds that people who choose dark mode usually have a reason.",
    ),
    "Notes from a usability session": (
        "Five people, forty minutes each, one task: find what an export "
        "failed to capture and explain why.",
        "Nobody completed it unaided. Four found the failure list; none could "
        "say why an item was on it, because the reason is a status code we "
        "show verbatim. One person read “403” aloud and then guessed, "
        "correctly, and told me they were guessing.",
        "The interesting failure was not the jargon. It was that the list is "
        "presented as an outcome rather than as something you can act on, so "
        "three of the five treated it as a report they were meant to file "
        "somewhere rather than a problem they could fix.",
        "Changing the words is the cheap half. The other half is that the "
        "list needs a next step per row, and I do not yet know what that step "
        "is for the permission case, which is most rows.",
    ),
    "Small tweaks, big difference": (
        "A collection of ten-minute changes from the last month, none of "
        "which is worth its own post and several of which people noticed "
        "immediately.",
        "The progress bar now shows what it is currently working on. Same "
        "data, already on screen three lines below, moved into the place "
        "people were already looking.",
        "Timestamps became relative for anything under a day and absolute "
        "after that, with the exact value on hover. The previous scheme was "
        "relative all the way back, which produced “11 months ago” where "
        "the actual date was the only useful thing.",
        "And the row you clicked stays highlighted when you come back from a "
        "detail view. Two lines of state, and it removes the small "
        "re-orientation everybody was doing without mentioning it.",
    ),
    "Retiring the old icon set": (
        "The old icons are gone as of this release. They were drawn for a "
        "denser interface at a smaller size and had been scaled up twice.",
        "The tell was that we had stopped using several of them. When an icon "
        "is ambiguous, people quietly add a text label next to it, and once "
        "the label is there the icon is decoration. Four of the old set had "
        "acquired permanent labels.",
        "The new set is smaller — twenty-two icons rather than fifty-one. "
        "Everything that could not be recognised without a label in testing "
        "was removed rather than redrawn, which is a rule I would recommend "
        "and which cost me two I was fond of.",
        "If something looks wrong at a small size, please say so with a "
        "screenshot. Several were only checked at the size I happened to be "
        "working at.",
    ),
    # -- Platform Notes (infrastructure, precise, unsentimental) --
    "Incident review: what changed": (
        "Following the review of last month's degradation, four changes are "
        "now in place. This is the follow-up post that these reviews usually "
        "do not get.",
        "The alert that should have fired now exists: queue depth over a "
        "threshold for more than two minutes. The reason it did not exist is "
        "that we alerted on error rate, and a queue that is growing has no "
        "errors right up until it does.",
        "Connection pool settings moved into reviewed configuration, with "
        "bounds checking on the values. A setting that accepts an "
        "out-of-range number and behaves catastrophically is not a "
        "configuration option, it is a trap.",
        "The two remaining actions are open: dashboard consolidation, and a "
        "load test that reproduces the failure mode. Both are assigned with "
        "dates, and if they slip that will be said here rather than quietly.",
    ),
    "Capacity planning for next quarter": (
        "Numbers for next quarter, so the reasoning is visible rather than "
        "living in one spreadsheet and one head.",
        "Current peak is about 340 concurrent crawls, against a comfortable "
        "ceiling near 600 — where “comfortable” means the point at which "
        "the queue stops draining faster than it fills. Growth has been "
        "roughly 8% a month for five months.",
        "That gives us until roughly February before peak meets the ceiling, "
        "and February is when we should be adding capacity, not learning that "
        "we need it. The plan is one additional worker group in December, "
        "sized so it can be halved again if growth flattens.",
        "The assumption most likely to be wrong is that growth stays smooth. "
        "Two of the last five months were single customers onboarding, which "
        "is a step function wearing a trend line's clothes.",
    ),
    "Why we added rate limiting": (
        "Requests to the export API are now rate limited per credential. The "
        "limits are deliberately generous and you should not notice them.",
        "The reason is not abuse. It is that a single misconfigured client, "
        "retrying immediately on failure, can generate more load than every "
        "other user combined — and it did, twice, both times by accident and "
        "both times taking a shared queue with it.",
        "Responses over the limit carry a retry-after value and a clear "
        "reason. Any client that respects it will be slower, never broken. "
        "Clients that retry immediately will get further behind, which is the "
        "intended incentive.",
        "Limits are published rather than secret. A limit you cannot see is "
        "indistinguishable from a bug, and we would rather answer questions "
        "about the number than about mysterious failures.",
    ),
    "Notes on the database migration": (
        "The schema migration ran over three weekends. It is done, and "
        "nothing about it was interesting, which took real effort.",
        "It was done in the boring way: add the new columns, write to both, "
        "backfill in batches with a rate limit, read from the new ones behind "
        "a flag, then drop the old. Six weeks of elapsed time to avoid one "
        "long lock.",
        "The backfill was the only part that misbehaved. Batches of 10,000 "
        "were fine in testing and caused replication lag in production, "
        "because production has an index that testing did not. We now build "
        "the test database from a production schema dump, which we assumed we "
        "already did.",
        "Total write amplification during the dual-write window was about "
        "40%, well inside headroom. Read latency was unchanged throughout, "
        "which was the number we were actually watching.",
    ),
    "Monitoring: what we watch and why": (
        "We cut the alert list from 61 to 18 this quarter. This is what "
        "survived and the rule we used.",
        "The rule: an alert must name something a human can do at the moment "
        "it fires. “Disk usage above 80%” fails that — it is a graph, and "
        "waking somebody to look at a graph teaches them to acknowledge and "
        "go back to bed, which is how a real page gets missed.",
        "What we watch now is mostly symptoms at the boundary: is work "
        "arriving, is it draining, is it finishing correctly, and can a "
        "customer authenticate. Every one of the eighteen has a runbook, and "
        "an alert whose runbook says “investigate” is not finished.",
        "The 43 we removed are still recorded as metrics with dashboards. "
        "Deleting the alert does not mean deleting the data, and about half "
        "of them are genuinely useful five minutes after a page.",
    ),
    "The postmortem process, revisited": (
        "We have written 31 postmortems in two years. I went back and read "
        "all of them, which is a strange afternoon and worth doing.",
        "The documents are good. The follow-through is not: of 94 action "
        "items, 61 were completed, 14 were consciously dropped, and 19 are in "
        "an unclear state where nobody would say they are abandoned and "
        "nobody has touched them for a year.",
        "So the process changes in one way only. Action items now have an "
        "owner and a date at the time of writing, and an item with neither "
        "does not go in the document — it goes in the narrative as something "
        "we considered and did not commit to. Being honest about that is "
        "better than a list that decays.",
        "We are not changing the blameless framing, the template, or the "
        "meeting. Those work. The gap was never in the writing.",
    ),
    "Scaling the ingestion pipeline": (
        "Ingestion now handles about four times the throughput it did in "
        "spring, on the same hardware. Here is where the ceiling actually "
        "was.",
        "Not CPU, which sat under 30% throughout. The limit was that each "
        "item took a lock on a shared counter to update progress, so the "
        "pipeline serialised on the one operation that exists purely to be "
        "displayed to a human.",
        "Progress is now aggregated per worker and merged on a timer. "
        "Displayed progress can lag reality by up to two seconds, which "
        "nobody can perceive and which bought the entire improvement.",
        "The next ceiling is the archive write path, at roughly six times "
        "current volume. We know where it is and are deliberately not "
        "fixing it yet.",
    ),
    "On call again already": (
        "Eight weeks around, so it is my week again. Some notes on what the "
        "rotation looks like from inside it now that we have changed it "
        "twice.",
        "Volume is genuinely low. This week produced two pages, both "
        "actionable, both resolved inside twenty minutes with a runbook. "
        "Three years ago that would have been a quiet day rather than a quiet "
        "week.",
        "What has not improved is the handover. Fifteen minutes of "
        "conversation at the start of the week is not enough to transfer "
        "“this thing has been flapping and we are watching it”, and the "
        "written handover note tends to record what happened rather than what "
        "to keep an eye on.",
        "Trying something next rotation: the note has one section that is "
        "only about things which have not happened yet. If it works I will "
        "write it up properly.",
    ),
}


# --------------------------------------------------------------------
# Forum topics, keyed by title (`synth._FORUM_TOPIC_TITLES`)
# --------------------------------------------------------------------

TOPIC_BODIES: dict[str, tuple[str, ...]] = {
    "How do I reset my password?": (
        "I have been locked out since this morning. The reset link in the "
        "sign-in page sends the email, but the link in it takes me back to "
        "the sign-in page rather than a reset form.",
        "Tried in two browsers and a private window. Same thing. Is the "
        "reset flow different for accounts on the corporate directory?",
    ),
    "Export is stuck at 90%, any ideas?": (
        "Started an export of a fairly large wiki about two hours ago. It "
        "moved steadily to 90% in around fifteen minutes and has not moved "
        "since. No error, the progress bar just sits there.",
        "The log shows it is still fetching attachments — same three "
        "filenames over and over, roughly one attempt a minute. Is it "
        "retrying something it will never get, and if so is there a way to "
        "make it skip and carry on?",
    ),
    "Best practice for large wiki exports?": (
        "We are about to export a wiki with roughly 4,000 pages and a lot of "
        "attachments. Before I set it running overnight I would rather learn "
        "from people who have already done this.",
        "Specifically: is it better to run one large export or split it by "
        "section? And does the request delay setting actually matter at this "
        "size, or is it mostly there to be polite to the server?",
    ),
    "Anyone else seeing slow blog feeds?": (
        "Blog feeds have been noticeably slower since about Tuesday. Wiki "
        "and forum feeds are unchanged, so it does not look like the network "
        "on our side.",
        "A feed that used to come back in a second or so is now taking "
        "fifteen to twenty. It does complete, so nothing is broken exactly, "
        "but it makes a full capture take most of an afternoon.",
    ),
    "Where do I find my API token?": (
        "The documentation refers to an API token in the profile settings, "
        "but I cannot find that section — my settings page has "
        "Notifications, Appearance and Connected Apps and nothing else.",
        "Is this something an administrator has to enable per account, or am "
        "I looking at an older version of the docs than the version we run?",
    ),
    "Attachments not downloading, help?": (
        "Pages come through fine but attachments consistently fail. In the "
        "reader they show with the right filename and size, and clicking one "
        "gives an empty file.",
        "This is the same for every attachment I have tried, across three "
        "different wikis, so I suspect it is something about how I am "
        "authenticating rather than the content itself.",
    ),
    "How to migrate off the old importer?": (
        "We have three scheduled jobs still pointing at the old importer and "
        "a deadline at the end of the month. The migration guide covers the "
        "straightforward case well, but ours is not quite that.",
        "Two of our jobs use custom field mappings. Is there a way to export "
        "the existing mapping rather than reconstructing it by hand? And "
        "does anything break if the old and new importers run against the "
        "same content during a transition period?",
    ),
    "Why did my scheduled crawl fail?": (
        "The nightly crawl failed twice this week, both times about forty "
        "minutes in. It succeeds when I run the same configuration by hand "
        "during the day.",
        "The only thing in the log is a transport error with no detail. Is "
        "there a more verbose mode, or somewhere the full error is kept? "
        "Happy to share the configuration if that helps.",
    ),
    "What's everyone working on this week?": (
        "Weekly thread — what has your attention this week?",
        "I am finally replacing the spreadsheet we use to track archive "
        "requests. It has four owners, three of whom no longer work here, "
        "and a tab nobody can explain.",
    ),
    "Favorite productivity tips?": (
        "Not looking for the usual advice about inbox zero. More interested "
        "in the small specific habits people have that actually stuck.",
        "Mine: I keep a file per project with nothing but open questions in "
        "it. Not tasks — questions. It turns out most of what blocks me "
        "is not knowing something rather than not having done something.",
    ),
    "Anyone attending the user conference?": (
        "Looks like a few of us are going in November. Worth arranging a "
        "meetup on one of the evenings?",
        "I am there for the full three days and mostly interested in the "
        "migration and archiving sessions. Happy to organise something on "
        "the Tuesday if there is interest.",
    ),
    "Show and tell: what did you build?": (
        "Long-running thread for things people have built on top of this, however small.",
        "To start: a script that watches the archive directory and posts a "
        "summary to our team channel whenever a capture finishes — what "
        "was captured, how long it took, and anything that failed. About "
        "forty lines and it has ended more “did the export run?” "
        "conversations than any documentation I have written.",
    ),
    "Coffee chat: remote work setups": (
        "The obligatory desk thread. Show us what you are working from.",
        "After three years I have concluded the monitor matters much less "
        "than the chair, and both matter less than having a door.",
    ),
    "What tools do you swear by?": (
        "Interested in the ones you would actually miss, not the ones you have installed.",
        "For me it is a clipboard history manager. Deeply unglamorous, and I "
        "notice within about a minute when I am on a machine without it.",
    ),
    "Weekend reading recommendations": (
        "Anything good lately? Loosely work-adjacent is fine, entirely unrelated is also fine.",
        "I have been going through old incident write-ups from other "
        "companies, which sounds grim but is genuinely the most useful "
        "reading I have done this year.",
    ),
    "Introduce yourself here": (
        "New here? Say hello. Where you are, what you work on, and something "
        "you are trying to figure out at the moment.",
        "I will start: platform team, based in Lisbon, currently trying to "
        "work out how to archive fifteen years of a wiki that three separate "
        "teams believe they own.",
    ),
    # -- Dev Q&A (specific, technical, someone mid-implementation) --
    "How do I configure single sign-on?": (
        "Setting up SSO against our identity provider and I am stuck at the "
        "point where the assertion comes back. The login redirects correctly, "
        "the provider says it issued the assertion, and we land back on the "
        "sign-in page with no error shown anywhere.",
        "The provider's log shows the attribute we send as the user "
        "identifier, and it is an email address. Does the mapping have to be "
        "the short account name, or is that configurable?",
    ),
    "Best way to paginate the REST API?": (
        "I am walking an entry feed with page and page-size parameters and "
        "getting duplicates — the same entry appears at the end of one page "
        "and the start of the next, and occasionally one goes missing "
        "entirely.",
        "The content is being edited while I read it, so I assume that is "
        "related. Is there a stable ordering I should be requesting, or a "
        "cursor form I have missed in the documentation?",
    ),
    "Rate limits on the export endpoint?": (
        "Trying to find documented numbers for the export endpoint's rate "
        "limits. The guide mentions that limits exist and does not say what "
        "they are.",
        "For context: I want to run four exports in parallel against the same "
        "deployment, and I would rather know the ceiling in advance than "
        "discover it at three in the morning.",
    ),
    "Anyone hit a race condition in the crawler?": (
        "Running the crawler with concurrency above one, I occasionally get "
        "an archive where a page has two entries with the same label and "
        "different bodies. Roughly one run in twenty, always on wikis with "
        "deep trees.",
        "I cannot reproduce it deliberately, which is what makes me think it "
        "is a race rather than a data problem. Has anyone seen this, and is "
        "there a known-safe concurrency setting?",
    ),
    "How to mock the fake server in tests?": (
        "Our test suite currently spins up the fake server on a port and "
        "talks to it over the loopback interface. It works and it is slow — "
        "about nine seconds of the suite is process startup.",
        "Is there a supported way to drive it in-process instead? I can see "
        "the app object is constructed separately from the server, which "
        "suggests yes, but I would rather not depend on something that is not "
        "meant to be public.",
    ),
    "Recommended retry strategy for 429s?": (
        "My client currently retries three times with a fixed one-second gap. "
        "Under load that clearly makes things worse, and I would like to fix "
        "it properly rather than raise the number.",
        "The responses do carry a retry-after value. Is honouring that "
        "sufficient on its own, or should I be adding backoff and jitter on "
        "top of it? Everything I find online argues both ways.",
    ),
    "Why is my UUID not matching the spec?": (
        "The identifiers coming back from the topics feed do not validate as "
        "version 4 UUIDs in my parser, but they are accepted everywhere else, "
        "including by the reference client.",
        "The values look right — thirty-two hex digits, hyphens in the usual "
        "places — but the version nibble is not 4 on some of them. Am I "
        "wrong to expect version 4 here, or is something generating these "
        "incorrectly?",
    ),
    "Best practice for idempotent imports?": (
        "I need an import that can be re-run after a partial failure without "
        "creating duplicates. Currently I dedupe on title, which breaks the "
        "moment two pages legitimately share one.",
        "What is everyone else keying on? I would rather learn the right "
        "answer now than discover in six months that my key was not stable.",
    ),
    # -- Announcements (posted, not asked; informational) --
    "Proposal: standardise our runbooks": (
        "We have around sixty runbooks across four teams, in at least three "
        "formats, and the only thing they reliably share is that the contact "
        "listed at the top has moved on.",
        "Proposing a single short template: what this covers, when to use it, "
        "the steps, and how to tell it worked. Two weeks for comments here, "
        "then we convert the ten most-used ones and see whether anybody "
        "objects to the result.",
    ),
    "New release: 8.0 is out": (
        "8.0 is published and available now. The full notes are on the "
        "release blog; this thread is for questions and for anything you hit "
        "on upgrading.",
        "Two things worth repeating here because they change behaviour: the "
        "authentication header has changed, with the old form accepted only "
        "through 8.1, and comment capture is now on by default, which will "
        "make your first export larger than the last one.",
    ),
    "Scheduled maintenance this weekend": (
        "The deployment will be read-only from 22:00 Saturday to roughly "
        "04:00 Sunday for a storage migration. Reading and exporting continue "
        "to work throughout; editing, commenting and uploads will be "
        "unavailable.",
        "Scheduled crawls in that window will succeed. If you have anything "
        "that writes, please move it either side of the window rather than "
        "letting it fail and retry.",
    ),
    "Deprecation notice: old API sunset": (
        "The v1 API is deprecated from today and will be withdrawn in "
        "twelve months. This is the first of the reminders and there will be "
        "several.",
        "From now on every v1 response carries a deprecation header and the "
        "sunset date. If you are not sure whether anything you own still uses "
        "it, that header in your own logs is a faster answer than reading "
        "code.",
    ),
    "Welcome our newest moderators": (
        "Three people have volunteered as moderators and have taken up their "
        "permissions this week. Between them they cover European and Asian "
        "working hours, which closes a gap where a question could sit "
        "unanswered overnight.",
        "The moderation guidelines have not changed. If you disagree with a "
        "moderation decision, say so in the thread first — that is normal "
        "and welcome, and it is how the guidelines get better.",
    ),
    "Survey: help us prioritize the roadmap": (
        "The half-year survey is open until the end of the month. It is "
        "eleven questions and takes about six minutes; two questions are free "
        "text and those are the ones we actually read closely.",
        "Last time we published the results here with what we did about each "
        "theme, including the ones we decided against. We will do that again, "
        "so answering it is not shouting into a hole.",
    ),
    "Office hours starting next month": (
        "Starting in October there will be an open hour every second "
        "Wednesday. No agenda: bring a problem, a half-formed idea, or "
        "nothing at all.",
        "This exists because the questions we get in writing are the ones "
        "people have already worked out how to phrase. The interesting ones "
        "tend to start “this is probably obvious, but”, and those do not "
        "get typed.",
    ),
    "Changelog now published weekly": (
        "The changelog moves from per-release to weekly, published every "
        "Friday whether or not anything shipped. A week with nothing in it "
        "will say so.",
        "The reason is that release-time changelogs are written by whoever is "
        "doing the release, at the end of a long day, from commit messages. "
        "Weekly entries get written while the change is still fresh to the "
        "person who made it.",
    ),
    # -- Feature Requests (a proposal with a reason behind it) --
    "Please add bulk export for forums": (
        "Exporting forums one at a time is fine for two and painful for "
        "forty. We archive our whole forum set quarterly and it is currently "
        "forty separate runs, each of which has to be started by hand.",
        "What I would like is to select several forums, or a whole category, "
        "and get one archive out. Sequential is completely fine — the ask is "
        "about not babysitting it, not about speed.",
    ),
    "Request: dark mode for the reader": (
        "The exported reader is bright white and a lot of the reading it gets "
        "used for happens late, during or after an incident.",
        "The application already has a dark theme, so I assume most of the "
        "work is deciding what to do with author-supplied content that brings "
        "its own colours. Even a version that leaves page bodies alone and "
        "darkens the surrounding chrome would be a real improvement.",
    ),
    "Can we get CSV export too?": (
        "The archive format is good for reading and awkward for counting. "
        "Every quarter somebody on my team writes a throwaway script to turn "
        "it into a table, and it is a slightly different script each time.",
        "One flat file per content type — identifier, title, author, dates, "
        "counts — would cover everything I have ever needed. Not the bodies, "
        "just the metadata.",
    ),
    "Support for nested tags?": (
        "We have around 400 tags and no structure, so the tag list is now "
        "less useful than search. Half of them are variations on the same "
        "idea that grew apart.",
        "I am not asking for a taxonomy anyone has to maintain. Just the "
        "ability to write a tag with a separator in it and have the interface "
        "group on that separator would do most of the work.",
    ),
    "Would love a keyboard shortcut for search": (
        "Reaching for the mouse to click the search box is the single thing I "
        "do most often in the reader and the only one I cannot do from the "
        "keyboard.",
        "The usual convention would be fine. The one request I would add: "
        "make it work from inside the document body without stealing keys "
        "from a page that has its own handlers.",
    ),
    "Request: webhook on export complete": (
        "We currently poll the status endpoint every thirty seconds to find "
        "out when an export has finished, which is wasteful for us and for "
        "the server, and gives us up to half a minute of delay for no reason.",
        "A single POST to a configured address on completion, carrying the "
        "run identifier and whether it succeeded, would replace all of it. "
        "Retries would be nice; at-least-once delivery is fine and we can "
        "deduplicate on the identifier.",
    ),
    "Please support custom timestamps": (
        "When we import historical content, everything lands with the import "
        "date rather than the date the content was written. A fifteen-year "
        "archive then shows as fifteen years of nothing followed by one very "
        "busy Tuesday.",
        "I understand why the created date is not writable in general. But "
        "for an import there is an obvious original value, and losing it "
        "destroys the one thing an archive is for.",
    ),
    "Idea: diff view between versions": (
        "Version history shows me a list of revisions and lets me open each "
        "one. To find what changed I open two tabs and read.",
        "A side-by-side or inline diff between any two selected versions "
        "would turn a five-minute job into a five-second one. The data is "
        "clearly all there — this is a rendering feature, not a capture "
        "one.",
    ),
    # -- Bug Reports (precise: steps, expected, actual, environment) --
    "Export crashes on unicode titles": (
        "Version 8.0.1, exporting a wiki that contains a page titled with "
        "Hebrew text and an emoji. The run aborts about four seconds in with "
        "an encoding error and no archive is written.",
        "Steps: create a page with a right-to-left title containing a "
        "combining mark, export the wiki. Expected: the page is exported with "
        "its title intact. Actual: the run stops and the partial archive is "
        "removed. A wiki with the same page renamed to plain ASCII exports "
        "fine, so it is the title and not the body.",
    ),
    "Broken link in the exported wiki": (
        "In an exported archive, links between pages in the same wiki resolve "
        "correctly except where the target page title contains an ampersand. "
        "Those produce a not-found page in the reader.",
        "Reproduced with a page called “Backup &amp; Restore”: the link in "
        "the body points at a label with the ampersand escaped twice. The "
        "page itself is present in the archive and opens fine from the index, "
        "so it is the link that is wrong, not the capture.",
    ),
    "Forum replies out of order after export": (
        "A thread that reads correctly on the server comes out of the export "
        "with the nested replies flattened into publication order, so an "
        "answer appears before the question it answers.",
        "It looks like the parent reference survives — I can see it in the "
        "archive data — and the reader is not using it. Threads with only "
        "top-level replies are unaffected, which fits.",
    ),
    "Timestamps off by one hour?": (
        "Every timestamp in the exported archive is exactly one hour behind "
        "what the web interface shows, for content created since late March.",
        "Anything older than late March matches exactly. That date is when "
        "daylight saving started here, so I assume something is applying a "
        "fixed offset rather than a zone. Content created in winter and "
        "exported now is correct, which is the detail that makes me think it "
        "is the write side and not the display.",
    ),
    "Duplicate attachments after re-run": (
        "Re-running an export into an existing archive directory adds a "
        "second copy of every attachment, with a numeric suffix, rather than "
        "recognising the existing one. Pages and comments are correctly "
        "deduplicated; only attachments do this.",
        "Third run produces a third copy, so it is not a one-off. The files "
        "are byte-identical. Archive size roughly triples, which is the "
        "reason I noticed at all.",
    ),
    "Blog comments missing after migration": (
        "After migrating from 7.4 to 8.0 and re-exporting, blog posts come "
        "through with their comment count intact and no comment bodies. The "
        "count in the entry metadata says nine; the archive has none.",
        "Wiki page comments are fine in the same run, and forum replies are "
        "fine. It is specifically blog entry comments. A 7.4 archive of the "
        "same blog, taken last month, has them.",
    ),
    "Reader shows blank page on load": (
        "Opening the exported reader gives a completely blank page. No error, "
        "no partial render, nothing in the page source but the shell.",
        "The archive itself looks fine — the content is all there on disk "
        "and the index file is valid. This happens with archives written by "
        "one particular older build; archives from the current release open "
        "normally in the same browser.",
    ),
    "Crawler hangs on empty pages": (
        "A wiki containing a page with an empty body causes the crawl to stop "
        "making progress at that page. It does not error and does not move "
        "on; the run sits there until I stop it.",
        "Confirmed by deleting the empty page and re-running, which completes "
        "in four minutes. Creating a new page and saving it without content "
        "reproduces it every time.",
    ),
    # -- Off Topic (loose, social, no resolution required) --
    "What are you reading right now?": (
        "Non-work thread. What is on your bedside table or in your bag at the moment?",
        "I am halfway through a very long novel about a family running a "
        "hotel, which I picked up because the cover was nice and have "
        "stayed with for entirely different reasons.",
    ),
    "Best coffee near the office?": (
        "New to this office and the machine on the third floor has already "
        "let me down twice. Where is everyone actually going?",
        "Walking distance preferred, and I will accept a queue if it is worth queueing for.",
    ),
    "Recommend a podcast for commutes": (
        "Forty minutes each way, mostly on a train with unreliable signal, so "
        "something that downloads well and does not need my full attention.",
        "I have run out of the obvious ones. Slightly niche is very welcome — "
        "the more specific the subject the better, in my experience.",
    ),
    "Anyone else into home automation?": (
        "I have reached the stage where the lights work perfectly and nobody "
        "else in the house can turn them on, which I am told is a known "
        "milestone.",
        "Curious what people have automated that they actually still use "
        "after a year, as opposed to the impressive thing they demonstrated "
        "once and quietly disabled.",
    ),
    "Favorite conference talk this year?": (
        "The recordings from the spring conferences are all up now and there "
        "is far too much of it to work through unguided.",
        "Looking for the one talk you have sent to somebody else. That seems "
        "like a better filter than a rating.",
    ),
    "Board game night, anyone in?": (
        "Thinking of the last Thursday of the month, somewhere near the "
        "office, starting around seven. Bring a game or bring nothing.",
        "Everyone welcome regardless of experience. The only house rule is "
        "that we do not spend forty minutes explaining a game to one person "
        "while everybody else looks at their phone.",
    ),
    "What's your desk setup like?": (
        "Not the aspirational one, the real one. Mine currently has two "
        "monitors, one of which is at a slightly wrong angle that I have been "
        "meaning to fix since February.",
        "Especially interested in what people did about cables, because that "
        "is the part every photograph on the internet appears to have solved "
        "by magic.",
    ),
    "Best team lunch spot?": (
        "Looking for somewhere that can take nine people without a booking "
        "two weeks in advance, and where a conversation is possible at normal "
        "volume.",
        "Two of the nine do not eat meat and one is gluten-free, which has "
        "quietly eliminated most of our usual list.",
    ),
    # -- Admin Corner (administrative notices, mildly bureaucratic) --
    "Reminder: renew your access badge": (
        "Badges issued in the first quarter of last year expire at the end of "
        "this month. If yours is one of them, the expiry date is printed on "
        "the reverse, bottom right.",
        "Renewal is done at the front desk and takes about five minutes, "
        "including the photograph. You do not need an appointment, but the "
        "queue between 12:00 and 14:00 is real.",
    ),
    "Please update your on-call info": (
        "The on-call contact list has not been reviewed since the "
        "reorganisation, and we know at least four entries point at people "
        "who have changed teams.",
        "Please check your own entry this week: phone number, escalation "
        "contact, and which rota you are on. If you are not on a rota, say so "
        "explicitly rather than leaving it blank — blank and “not on call” "
        "look identical and only one of them is true.",
    ),
    "New expense policy starting next quarter": (
        "The expense policy changes from the first of next quarter. The "
        "headline change is that anything under a modest threshold no longer "
        "needs pre-approval, only a receipt.",
        "The trade is that receipts have to be submitted within thirty days "
        "rather than the current ninety. Claims older than that will need a "
        "manager's note, which is a conversation nobody wants to have about a "
        "sandwich.",
    ),
    "Security training due this month": (
        "The annual training module is open and due by the end of the month. "
        "It takes about forty minutes and can be done in more than one "
        "sitting.",
        "It is genuinely different from last year's — the phishing section "
        "was rewritten after the incident in spring, and the examples are "
        "taken from messages that actually reached people here.",
    ),
    "Office move: what you need to know": (
        "We move to the new floor over the weekend of the fourteenth. Desks, "
        "monitors and chairs are moved for you; anything in a drawer is not.",
        "Please clear personal items and label anything you want to keep by "
        "Friday afternoon. Unlabelled items go into storage for a month and "
        "then to charity, which is the same policy as last time and did "
        "genuinely claim somebody's guitar.",
    ),
    "Parking changes starting Monday": (
        "The lower level closes for resurfacing from Monday for "
        "approximately three weeks. Spaces there are reallocated to the "
        "overflow area behind the building for the duration.",
        "There are about thirty fewer spaces overall during the works. If you "
        "can share a car or come in on a different day during those three "
        "weeks, this is the month to do it.",
    ),
    "Please RSVP for the town hall": (
        "The quarterly town hall is on the twelfth, in person with a stream "
        "for anyone remote. Numbers are needed by the eighth for catering and "
        "seating.",
        "There is a question box open now. Anonymous questions are read out "
        "in full, in the order they arrive, and the ones that do not get "
        "answered in the hour are answered in writing afterwards.",
    ),
    "Benefits enrollment closes Friday": (
        "The enrolment window closes at end of day Friday. If you make no "
        "changes, last year's selections roll over automatically — which is "
        "fine for most people and worth checking if anything has changed at "
        "home.",
        "Two options are new this year and are not selected by default, so "
        "rolling over will not get you them. They are listed at the top of "
        "the form with a short explanation of each.",
    ),
}


# --------------------------------------------------------------------
# Fallbacks for titles without written bodies
# --------------------------------------------------------------------

_BLOG_SHAPES: tuple[tuple[str, ...], ...] = (
    (
        "A short note on {title_lower}, mostly so it is written down "
        "somewhere other than a chat thread.",
        "The change itself is small. The reason it took a while is that it "
        "touched the one part of the system everybody had been avoiding, and "
        "avoiding it turned out to be the expensive option.",
    ),
    (
        "We have been asked about {title_lower} enough times this month that "
        "it is worth a post rather than another reply.",
        "The short answer is that it works, with one caveat that matters if "
        "you are running at any scale: the defaults are tuned for the common "
        "case, and the common case is smaller than yours.",
    ),
    (
        "Notes from this week on {title_lower}.",
        "Nothing here is final. Posting early because the alternative is "
        "posting in three months when the decisions are already made and "
        "feedback is only theoretically welcome.",
    ),
)

_TOPIC_SHAPES: tuple[tuple[str, ...], ...] = (
    (
        "Hoping somebody has hit this before. {title_stripped}",
        "I have read through the documentation and the existing threads "
        "without finding a match, so either I am searching for the wrong "
        "words or this is less common than I assumed.",
    ),
    (
        "{title_stripped} — asking because the answer is probably "
        "obvious to somebody who has done it once.",
        "Happy to write up whatever the answer turns out to be, since I "
        "could not find it laid out anywhere.",
    ),
    (
        "Starting a thread on this rather than derailing the other one.",
        "{title_stripped} It comes up often enough in passing that it "
        "deserves somewhere to point people.",
    ),
)


def _shape_index(title: str, count: int) -> int:
    """Deterministic per title, so the same demo seed yields the same text."""
    return int(hashlib.sha256(title.encode("utf-8")).hexdigest()[:8], 16) % count


def varied_blog_body(title: str) -> tuple[str, ...]:
    """Paragraphs for a blog post with no written body."""
    shape = _BLOG_SHAPES[_shape_index(title, len(_BLOG_SHAPES))]
    lowered = title[0].lower() + title[1:] if title else title
    return tuple(p.format(title_lower=lowered) for p in shape)


def varied_topic_body(title: str) -> tuple[str, ...]:
    """Paragraphs for a forum topic with no written body."""
    shape = _TOPIC_SHAPES[_shape_index(title, len(_TOPIC_SHAPES))]
    stripped = title if title.endswith(("?", ".", "!")) else title + "."
    return tuple(p.format(title_stripped=stripped) for p in shape)


def blog_body(title: str) -> tuple[str, ...]:
    """Paragraphs for a blog post, written where we have one."""
    return BLOG_BODIES.get(title) or varied_blog_body(title)


def topic_body(title: str) -> tuple[str, ...]:
    """Paragraphs for a forum topic's opening post."""
    return TOPIC_BODIES.get(title) or varied_topic_body(title)


# --------------------------------------------------------------------
# Forum replies, keyed by topic title -- an ordered conversation
# --------------------------------------------------------------------
#
# Replies are written per thread rather than drawn from a generic pool by
# hash. A pooled reply can open a thread with "This worked for me, thanks for
# asking the question" before anybody has answered anything, and a thread that
# does not follow its own question is the clearest tell that a demo is filler.

TOPIC_REPLIES: dict[str, tuple[str, ...]] = {
    "How do I reset my password?": (
        "If your account comes from the corporate directory, the reset link "
        "will always bounce you back -- password changes happen upstream, "
        "not here.",
        "That is almost certainly it. The giveaway is that the email arrives "
        "at all: the link is generated before the account type is checked.",
        "OP| Confirmed, that was the problem. Changed it in the directory portal "
        "and I am back in. Thank you both.",
        "Worth a documentation note. This is the third time this month.",
        "Agreed, I have opened a docs issue for it.",
    ),
    "Export is stuck at 90%, any ideas?": (
        "Ninety percent is usually where attachments start. What does the "
        "log say it is fetching when it stalls?",
        "Three filenames repeating once a minute sounds like a retry loop on "
        "something you do not have permission to read. The export itself "
        "cannot tell the difference between “slow” and “never”.",
        "OP| That matches -- they are all in a restricted space I can see but not download from.",
        "Then set a retry limit and it will record them as failed and carry "
        "on. You get the archive plus an explicit list of what it could not "
        "reach, which is much better than a silent gap.",
        "OP| That worked. Finished in another twelve minutes with four "
        "attachments listed as unavailable. Exactly what I needed.",
    ),
    "Best practice for large wiki exports?": (
        "We did about 6,000 pages last year. One run, overnight, and it was "
        "fine -- splitting it by section mostly buys you more things to "
        "keep track of.",
        "Seconded, with one caveat: split it if the sections have different "
        "owners, because then you can hand each one off separately.",
        "The delay setting is not only politeness. Above roughly two "
        "requests a second we started seeing throttling, which made the "
        "whole run slower than if we had just waited.",
        "One more thing at that size: check you have disk headroom for the "
        "attachments before you start rather than at 90%. Ours is usually "
        "twice what we estimate from the page count.",
        "OP| That is the part I would not have guessed. Thanks -- leaving it at the default then.",
    ),
    "Anyone else seeing slow blog feeds?": (
        "Yes, since Tuesday. Blogs only, wikis and forums are normal.",
        "Tuesday was the search index rebuild. Blog feeds go through it for "
        "the aggregation counts; wikis do not, which explains why only blogs "
        "are affected.",
        "OP| Any idea how long a rebuild runs?",
        "It finished here overnight and feeds went back to normal in the "
        "morning. If it is still slow in a day, worth raising.",
        "OP| Back to a second or so this morning, so that was it. Glad I "
        "asked before opening a ticket.",
    ),
    "Where do I find my API token?": (
        "Tokens are off by default. An administrator has to enable them for "
        "the account before the section appears at all -- the documentation "
        "does not mention that, which is why nobody finds it.",
        "OP| That explains it. Who counts as an administrator here?",
        "Whoever manages your space. If you are not sure, the Admin Corner "
        "forum is the right place to ask.",
        "One thing before you generate it: the token is shown once and never "
        "again, so put it somewhere durable before closing the page.",
        "OP| Got it enabled, and the section is there now. Thanks.",
    ),
    "Attachments not downloading, help?": (
        "Right filename and size but an empty file usually means the "
        "metadata came through and the bytes did not -- so authentication "
        "worked for the feed and not for the download endpoint.",
        "They are separate paths, and some deployments protect them "
        "differently. Are you using a token, or a session cookie?",
        "OP| A token. Should I be using something else?",
        "For downloads on that setup, yes. Tokens are accepted on the API "
        "but not on the attachment path -- a session works for both.",
        "OP| Switched, and attachments are coming through with real content now.",
    ),
    "How to migrate off the old importer?": (
        "You can dump the existing mappings -- there is an export flag on "
        "the old importer that nobody advertises. Much better than "
        "reconstructing them by hand.",
        "OP| That alone saves me an afternoon. Thank you.",
        "On running both: it is safe as long as they write to different "
        "destinations. Pointed at the same one, the old importer's "
        "truncation will fight the new importer's full titles and you will "
        "get duplicates.",
        "We ran in parallel for two weeks with separate destinations and "
        "compared the output before switching. Worth the extra step.",
        "OP| Dumped the mappings, ran both at separate destinations for a "
        "week, and cut over this morning. All three jobs are on the new "
        "importer with the deadline to spare.",
    ),
    "Why did my scheduled crawl fail?": (
        "Fails at night, works by hand during the day, about forty minutes "
        "in -- that pattern is usually a credential expiring rather than "
        "anything about the crawl.",
        "Forty minutes is suspiciously close to a token lifetime. What are "
        "you authenticating with?",
        "OP| A token issued when I set the schedule up. That would be months ago now.",
        "There it is. A long crawl outlives a short-lived token, and the "
        "renewal only happens for interactive sessions -- which is why it "
        "works when you run it yourself.",
        "OP| Switched the scheduled job to a service account. Two clean runs so "
        "far. Thanks for the quick diagnosis.",
    ),
    "What's everyone working on this week?": (
        "Finally deleting a service nobody has called since 2023. The "
        "hardest part has been proving that nobody calls it.",
        "Archiving a wiki ahead of a team merge. Mostly a diplomatic "
        "exercise at this point rather than a technical one.",
        "That spreadsheet story is painfully familiar. Good luck.",
        "Writing documentation for a service I am about to delete, which "
        "sounds absurd and is the only way anyone will believe it is gone.",
        "OP| Four owners down to one and the mystery tab turned out to be a "
        "currency conversion from 2019. Deleted, and nothing broke.",
    ),
    "Favorite productivity tips?": (
        "The open-questions file is a good one. I keep something similar and "
        "the useful part is re-reading it a week later -- half the "
        "questions have answered themselves.",
        "Mine is boring: I write the commit message before the change. If I "
        "cannot describe it in a sentence, I do not understand it yet.",
        "OP| Stealing both of these.",
        "Mine is a hard stop rather than a habit: nothing new after four in "
        "the afternoon. Whatever I start then, I redo the next morning.",
        "OP| That one I have tried and failed at twice. Perhaps the third "
        "time, now that somebody else has admitted to it.",
    ),
    "Anyone attending the user conference?": (
        "I am there Tuesday to Thursday. Tuesday evening works.",
        "Same, and I would be up for that. The archiving session on "
        "Wednesday looks like the useful one.",
        "OP| Three of us then. I will book somewhere and post details here closer to the date.",
        "Make it four -- I am there Tuesday and Wednesday and would rather not eat alone again.",
        "OP| Booked for four on the Tuesday, near the venue. Details sent "
        "round; say if anyone else turns up in the meantime.",
    ),
    "Show and tell: what did you build?": (
        "Forty lines that end a recurring conversation is a better return "
        "than most of what I have shipped this year.",
        "We have something similar but it posts on failure only, which in "
        "hindsight was a mistake -- silence is ambiguous. People could not "
        "tell “it worked” from “it never ran”.",
        "That is a good argument for always posting. Changing mine.",
        "Ours renders the failure list into the message rather than linking "
        "to it. Two extra lines, and it is the difference between people "
        "reading it and not.",
        "OP| Both of those are going in this week. The always-post change is "
        "four characters and I have been arguing about it for a month.",
    ),
    "Coffee chat: remote work setups": (
        "Wholeheartedly agree about the door. Two years of a corner of the bedroom taught me that.",
        "The underrated one for me is a second chair, so that when I am "
        "reading rather than typing I am not in the same position all day.",
        "Mine is a lamp behind the monitor rather than above it. Cheapest "
        "change I have made and the one my eyes noticed.",
        "Nothing in my setup survived contact with a second person working "
        "from the same flat. That is the real variable.",
        "OP| A door and a second chair, then. Both cheaper than the monitor I was about to buy.",
    ),
    "What tools do you swear by?": (
        "Clipboard history, yes. Also a decent diff tool -- the built-in "
        "one is fine until the day it is not.",
        "Anything that makes it trivial to run one test. If that takes more "
        "than a couple of seconds, I stop doing it, and then I stop writing "
        "tests.",
        "A text expander, for the six commands I can never remember the "
        "flags for. It has quietly saved me from reading the same manual "
        "page fifty times.",
        "Everything else I would replace in an afternoon. The one I would "
        "genuinely mourn is the terminal I have had configured the same way "
        "for nine years.",
        "OP| Clipboard history, one-test-fast, and a text expander. That is "
        "a better list than the one I set out with.",
    ),
    "Weekend reading recommendations": (
        "Other people's incident write-ups are genuinely the best technical "
        "reading there is. All the detail nobody puts in documentation.",
        "If you want a specific one, the postmortem on this blog from the "
        "Friday outage is a good short example of the format.",
        "Slightly sideways: a book about the history of shipping standards. "
        "It is about interoperability the whole way through and never says "
        "so.",
        "Anything by somebody who has changed their mind in public. Rarer "
        "than it should be and worth more than the confident stuff.",
        "OP| The Friday outage write-up and the shipping one, then. Both "
        "short enough to finish, which is the only rule I have.",
    ),
    "Introduce yourself here": (
        "Welcome. Fifteen years and three owners is a fairly normal "
        "starting position, unfortunately.",
        "Hello from the docs side. If you hit anything in the guides that "
        "assumes knowledge you do not have yet, please say so -- that is "
        "exactly the feedback we cannot generate ourselves.",
        "Also new here, three weeks in, working on ingestion. Currently "
        "trying to find out who decided the retention window and why.",
        "That question has an answer and it is not written down anywhere, "
        "which is roughly the state of everything in this forum.",
        "OP| Fifteen years and three owners, and the first useful thing I "
        "have learned is to ask here before assuming there is a document.",
    ),
    # -- Dev Q&A --
    "How do I configure single sign-on?": (
        "Landing back on the sign-in page with no error almost always means "
        "the assertion was accepted and then no account matched it.",
        "The identifier attribute has to resolve to an account here. An email "
        "address works only if the directory is configured to index it as a "
        "login attribute, and by default it is not.",
        "OP| So I should be sending the short account name instead?",
        "Either — but whichever you send has to be the attribute the "
        "directory searches. Changing the provider's mapping is usually the "
        "smaller change of the two.",
        "OP| Switched the provider to send the short name and it logs in "
        "first time. The silent bounce was the confusing part; an error there "
        "would have saved me a day.",
    ),
    "Best way to paginate the REST API?": (
        "Duplicates and gaps while paging through changing content is the "
        "classic offset-pagination problem rather than anything specific "
        "here. An edit reorders the underlying set between your two "
        "requests.",
        "Request an explicit sort on something immutable — the published "
        "date or the identifier — rather than relying on the default, which "
        "is by modification time and therefore moves under you.",
        "OP| That would explain why it is always the recently-edited entries "
        "that go missing. Is there a cursor form?",
        "Not on this API version. Sorting by identifier and remembering the "
        "last one you saw gets you the same guarantee with a little more work "
        "on your side.",
        "OP| Sorted by identifier, kept the last seen value, and two full "
        "walks now match exactly. Thanks.",
    ),
    "Rate limits on the export endpoint?": (
        "They are per credential, not per address, which is the part that "
        "catches people running several jobs under one service account.",
        "Four parallel exports on one credential will hit it. Four on four "
        "credentials will not, at least not at any volume I have run.",
        "OP| Everything here uses the one service account, so that is presumably my answer.",
        "The responses do carry a retry-after value, so a client that honours "
        "it will slow down rather than fail. Worth confirming yours does "
        "before you find out at three in the morning.",
        "OP| Split it across three credentials and added retry-after "
        "handling. Ran all four in parallel overnight with no throttling at "
        "all.",
    ),
    "Anyone hit a race condition in the crawler?": (
        "Seen it, and it is deterministic once you know the trigger: two "
        "workers discovering the same page through different parents at the "
        "same time.",
        "Deep trees make it likelier because there are more paths to the same "
        "page. That fits your one-in-twenty and your inability to reproduce "
        "it on demand.",
        "OP| That matches exactly — every duplicate I have looked at is a page with two parents.",
        "It is fixed on the current release; discovery takes a lock per label "
        "rather than per path. Before that, concurrency of one is the only "
        "genuinely safe setting.",
        "OP| Upgraded and ran the same wiki forty times without a duplicate. "
        "Leaving it at four workers.",
    ),
    "How to mock the fake server in tests?": (
        "Yes — the app is deliberately separate from the server so it can be "
        "driven in process. That split exists for exactly this.",
        "Point an in-process transport at the app object and skip the socket "
        "entirely. Our own suite does this and it is the reason it runs in "
        "seconds rather than minutes.",
        "OP| Is that considered public, or am I building on something that will move?",
        "It is public and tested. The construction function is part of the "
        "documented surface; the server wrapper around it is the part that is "
        "not.",
        "OP| Converted the suite this morning. Nine seconds of startup gone "
        "and nothing else changed.",
    ),
    "Recommended retry strategy for 429s?": (
        "Honour retry-after first. If the server tells you when to come back "
        "and you come back earlier, nothing else you do matters.",
        "Add jitter on top of it, though. If ten clients all wait exactly the "
        "stated interval they all return in the same instant, which is how a "
        "throttle turns into a repeating spike.",
        "OP| So retry-after plus a random offset, rather than exponential backoff?",
        "Both: retry-after as the floor, exponential growth for repeated "
        "failures, jitter over the result. Three lines, and it is the "
        "difference between recovering and oscillating.",
        "OP| Implemented as described. Under the same load that used to give "
        "me a wall of 429s, the run now takes nine percent longer and never "
        "fails.",
    ),
    "Why is my UUID not matching the spec?": (
        "You are right that they are not all version 4, and you are also "
        "right that nothing breaks. These identifiers are opaque strings that "
        "happen to be UUID-shaped.",
        "Some of them are version 1 in older content, and a few are derived "
        "deterministically from the source system during migration, which "
        "makes them version 5. Everything downstream compares them as text.",
        "OP| Then my parser is being stricter than the data ever promised to be.",
        "Correct. Validate the shape if you like, never the version, and "
        "definitely do not try to extract a timestamp out of them.",
        "OP| Relaxed the check to the shape only. Whole class of failures gone.",
    ),
    "Best practice for idempotent imports?": (
        "Not the title. Titles are neither unique nor stable, and you will "
        "find that out on the day somebody renames a page.",
        "Key on the source system's own identifier and store it alongside "
        "your record. Then a re-run is an upsert, not a guess.",
        "OP| The source identifier is not visible in the export I am working "
        "from, or I have not found it.",
        "It is in the per-item metadata rather than the body — the "
        "provenance field. It survives renames and moves, which is the whole "
        "reason it is carried through.",
        "OP| Found it, keyed on it, and re-ran a partial import three times "
        "with identical results. Exactly what I needed.",
    ),
    # -- Announcements --
    "Proposal: standardise our runbooks": (
        "Strongly in favour, with one addition: a last-verified date. A "
        "runbook nobody has walked through in two years is a liability "
        "regardless of format.",
        "Agreed on the date. I would also drop “when to use it” into the "
        "title where possible, since that is what people scan.",
        "OP| Both fair. Adding a verified date to the template; leaving the "
        "title convention as guidance rather than a rule.",
        "One concern: sixty runbooks converted by hand is a lot of "
        "volunteering. Can we convert on touch instead, so a runbook gets the "
        "new format the next time somebody uses it?",
        "OP| That is better than my plan. Ten most-used ones converted "
        "up front, the rest on touch, and we will see where we are in a "
        "quarter.",
    ),
    "New release: 8.0 is out": (
        "Upgraded a test deployment this morning. The archive migration took "
        "about four minutes for 40,000 items and reported everything it "
        "converted.",
        "Worth flagging for anyone with automation: the header change is not "
        "optional forever, and a client sending only the old form will look "
        "unauthenticated rather than misconfigured after 8.2.",
        "OP| That is exactly why it is called out in the notes. Both forms "
        "are accepted for two releases, which should be enough for a client "
        "you own and possibly not for one you do not.",
        "Comment capture being on by default caught us — first export was "
        "about 30% larger than the previous one. Not a complaint, just "
        "something to expect.",
        "OP| Noted, and we will put a size note in the upgrade guide. Thanks "
        "both for upgrading early and saying what you found.",
    ),
    "Scheduled maintenance this weekend": (
        "Does read-only include the search index, or will search be stale for the window?",
        "Search keeps working against the index as it stands at 22:00. "
        "Nothing new will appear in it until Sunday morning, which for a "
        "read-only window amounts to the same thing.",
        "Our nightly crawl starts at 01:00 and takes about two hours. Safe to leave it running?",
        "OP| Safe. Reading and exporting are unaffected for the whole window "
        "— only writes are blocked.",
        "OP| Migration finished at 03:20 and everything is back to normal "
        "ahead of schedule. Thanks for the patience.",
    ),
    "Deprecation notice: old API sunset": (
        "Twelve months is generous, thank you. Is there a way to see which of "
        "our own clients are still calling it?",
        "OP| Every v1 response now carries a deprecation header. Grepping "
        "your own client logs for it is faster and more accurate than "
        "auditing the code.",
        "The one to plan for is pagination. The v2 shapes are otherwise the "
        "same, but cursor-based paging is a real change on the client side "
        "rather than a rename.",
        "Confirming that from our migration: everything else was a "
        "find-and-replace, paging took an afternoon and a rethink of how we "
        "resume a partial run.",
        "OP| That matches what we expected, and it is the section of the "
        "guide we will expand first. Please keep reporting the awkward parts "
        "here.",
    ),
    "Welcome our newest moderators": (
        "Welcome, all three. The overnight gap was real — I have posted "
        "questions at 23:00 and had them sit until the next afternoon.",
        "Congratulations. Is there a written note anywhere on what "
        "moderators can and cannot see? It comes up every time and the "
        "answers vary.",
        "OP| Fair point, and the honest answer is that it is documented in "
        "two places that disagree. I will merge them into one page this week "
        "and link it from the guidelines.",
        "Worth saying publicly: moderators can see deleted posts and cannot "
        "see private messages. That is the pair people usually assume "
        "backwards.",
        "OP| Correct, and now written down in one place. Thanks for the prompt.",
    ),
    "Survey: help us prioritize the roadmap": (
        "Done. The free-text questions are the right call — the "
        "multiple-choice ones always make me pick between things I do not "
        "want.",
        "Is it anonymous? I ask because the answer changes what I would write in the free text.",
        "OP| Anonymous unless you choose to leave your name in the last "
        "field. Nothing links a response to an account, and we cannot see who "
        "has responded, only how many.",
        "Then I have rewritten mine at more length. Last year's published "
        "results with the “we decided against this and here is why” "
        "section were the reason I filled it in again.",
        "OP| That section is staying. It is the part that takes the longest "
        "to write and the only part anybody quotes back at us.",
    ),
    "Office hours starting next month": (
        "Good idea. Any chance of one in a time zone that is not European "
        "afternoon? Every open session so far has been at 22:00 for me.",
        "OP| Yes — alternating. The first is at the usual time, the one a "
        "fortnight later is eight hours earlier, and it continues alternating "
        "from there.",
        "Will they be recorded? I would rather not miss the useful part just "
        "because I could not make either slot.",
        "OP| No recording, deliberately. The point is the questions people do "
        "not want to type, and a camera in the room ends that. Anything "
        "generally useful gets written up afterwards without attribution.",
        "That is the right trade. The written summaries from the equivalent "
        "sessions elsewhere are what I read anyway.",
    ),
    "Changelog now published weekly": (
        "Please keep a per-release view as well. Weekly is better for "
        "following along and useless for “what changed between the version "
        "we run and the one we are considering”.",
        "OP| Both. The weekly entries are the source and the release notes "
        "are assembled from them, so the per-release view is a filter rather "
        "than a second document to maintain.",
        "Will empty weeks really be published? I ask because that is the part "
        "that usually quietly stops.",
        "OP| Yes, and you should hold us to it. A week that says nothing "
        "shipped is information; a missing week is ambiguous.",
        "Two weeks in and it has already changed how our upgrade discussions "
        "go. We can point at a specific Friday rather than a version number.",
    ),
    # -- Feature Requests --
    "Please add bulk export for forums": (
        "Forty runs by hand is a strong argument on its own. We have the same "
        "shape of problem at a smaller scale and solved it with a shell loop, "
        "which works and is nobody's idea of a feature.",
        "A category-level selection would cover most of it. Ours are already "
        "organised into four categories and we would archive by category "
        "every time.",
        "OP| Category selection would do everything I need, yes. I do not "
        "care whether it runs them in parallel.",
        "Noted and added to the request list. The archive layout question is "
        "the interesting part — one archive containing many forums, or many "
        "archives produced by one run?",
        "OP| One archive, for us. The whole point is having a single thing to "
        "hand to the records team each quarter.",
    ),
    "Request: dark mode for the reader": (
        "Seconding this. Reading an incident thread at two in the morning on "
        "a white background is its own small punishment.",
        "The author-content problem is real but smaller than it looks: most "
        "captured bodies specify no background at all, so they inherit. It is "
        "the handful with an explicit white that stand out.",
        "OP| Chrome-only would honestly be enough for me. I do not need the page bodies inverted.",
        "That is the version most likely to happen, for what it is worth — "
        "the chrome is ours, the bodies are somebody else's and rewriting "
        "them is a fidelity question rather than a styling one.",
        "OP| Then I will happily take the smaller version. Filed with that "
        "scope rather than the one I originally asked for.",
    ),
    "Can we get CSV export too?": (
        "We wrote that throwaway script too, three times, and threw it away "
        "three times. Happy to contribute what ours extracts if it helps "
        "settle the columns.",
        "Careful with one flat file per type: comments and replies are "
        "hierarchical and flatten badly. A parent identifier column solves it "
        "and is easy to forget.",
        "OP| Agreed, and a parent column is all I would need. I am not trying "
        "to reconstruct threads in a spreadsheet, just count them.",
        "The other one to specify now is dates. If they come out in a locale "
        "format, half the recipients will open the file and get a different "
        "answer.",
        "OP| Good catch. Amended the request: one file per type, a parent "
        "identifier column, and dates in one unambiguous format.",
    ),
    "Support for nested tags?": (
        "Four hundred tags with no structure is where every tag system ends "
        "up. The separator idea is the cheapest thing that helps, because it "
        "needs no migration.",
        "It also degrades well — anything that does not understand the "
        "separator just sees a slightly odd tag name and carries on.",
        "OP| That is exactly why I suggested it rather than a real taxonomy. "
        "Nobody here will maintain a taxonomy.",
        "One thing to decide up front: whether searching for the parent finds "
        "children. If it does not, the grouping is cosmetic and you will be "
        "back here in a year.",
        "OP| Added that to the request as the actual requirement. The "
        "grouping is the visible part; the search behaviour is the point.",
    ),
    "Would love a keyboard shortcut for search": (
        "Agreed, and the convention is well established enough that anything "
        "else would be a surprise.",
        "The awkward case is a captured page that installs its own key "
        "handler. Some author-supplied content does, and it will swallow the "
        "shortcut if the reader listens on the document.",
        "OP| Would listening at the top level rather than on the document solve that?",
        "It solves most of it. The remainder is content that stops "
        "propagation deliberately, which is rare and arguably that page's own "
        "problem.",
        "OP| That is a fine trade for me. Rare and someone else's bug beats "
        "reaching for the mouse forty times a day.",
    ),
    "Request: webhook on export complete": (
        "We would use this immediately. Our polling loop exists purely "
        "because there is nothing to listen to.",
        "Please make the payload small and stable — identifier, status, "
        "timestamp. Every webhook that tries to include a summary of the work "
        "ends up with a payload that changes shape and breaks receivers.",
        "OP| Agreed. I only need enough to go and fetch the real thing.",
        "Signing matters too. An unauthenticated POST that says a run "
        "finished is trivially forgeable, and someone will build automation "
        "on top of it.",
        "OP| Added both to the request: a minimal signed payload, "
        "at-least-once, deduplicated on the run identifier.",
    ),
    "Please support custom timestamps": (
        "This one bites everybody doing a historical import and there is no "
        "workaround worth the name. We ended up with the original date in a "
        "tag, which sorts alphabetically and looks ridiculous.",
        "The general objection is that a writable created date is a "
        "falsifiable audit trail. That is a real concern for live content and "
        "not obviously one for an import.",
        "OP| Would an explicit “original date” field, separate from "
        "created, address the objection?",
        "Almost certainly, yes — and it is honest, because the record really "
        "was created today and really did originate then. Two fields, both "
        "true.",
        "OP| Reframed the request around a separate original-date field "
        "rather than a writable created date. Much more likely to survive "
        "review.",
    ),
    "Idea: diff view between versions": (
        "Two tabs and reading is exactly what I do, and on a long page I "
        "usually give up and take the newest version on faith.",
        "The data is all captured, so this is genuinely a rendering feature. "
        "The hard part is that the stored versions are HTML, and a naive diff "
        "of HTML shows you tag changes nobody cares about.",
        "OP| Would a text-level diff of the rendered content be easier?",
        "Easier and more useful for most cases. You lose the ability to spot "
        "a purely structural change, which matters to about one person in "
        "fifty.",
        "OP| Then that is what I am asking for: a rendered-text diff between "
        "any two selected versions, with the structural case explicitly out "
        "of scope.",
    ),
    # -- Bug Reports --
    "Export crashes on unicode titles": (
        "Reproduced on 8.0.1 with a Hebrew title plus a combining mark. Plain "
        "Hebrew is fine; it needs the combining mark.",
        "The title is normalised on the way into the archive index and not on "
        "the way into the filename, so the two disagree and the write fails. "
        "The emoji is a red herring.",
        "OP| That fits — I have another page with an emoji and no combining "
        "mark and it exports fine.",
        "Fixed on the current build; both paths normalise the same way. "
        "Existing archives are unaffected because the page never got written "
        "in the first place.",
        "OP| Confirmed fixed on 8.0.4. Same wiki, same page, exports and "
        "opens with the title intact.",
    ),
    "Broken link in the exported wiki": (
        "Escaped twice is the giveaway: the label is escaped when the link is "
        "rewritten and again when the page is serialised.",
        "It will affect any title with a character that needs escaping, so "
        "ampersands are just the common one. A title with a quotation mark "
        "does the same thing.",
        "OP| Just tested one with a quotation mark and it breaks identically. Good call.",
        "The workaround until it is fixed is to open the target from the "
        "index rather than the link — the page itself is captured correctly, "
        "which you already found.",
        "OP| Confirmed the fix on the current release. All the ampersand "
        "links in that wiki resolve now, and so does the quotation-mark one.",
    ),
    "Forum replies out of order after export": (
        "The parent reference does survive — you are right that it is in the "
        "data. The reader was sorting by publication date and never "
        "consulting it.",
        "That is why threads with only top-level replies look fine: "
        "publication order and structural order happen to agree when there is "
        "no nesting.",
        "OP| So the capture is correct and only the rendering is wrong. That "
        "is a relief, given we have two years of archives.",
        "Correct, and it means your existing archives are fixed by opening "
        "them in a newer reader rather than re-exporting anything.",
        "OP| Opened a two-year-old archive in the current reader and the "
        "nesting is right. Nothing to re-export. Thank you.",
    ),
    "Timestamps off by one hour?": (
        "Late March plus a fixed offset is daylight saving, yes. The question "
        "is where the offset is applied.",
        "It is the write side, as you suspected. The exporter reads a "
        "zone-aware value and stores it with the offset that was correct when "
        "the archive schema was designed, in January.",
        "OP| Is there any way to tell which of my existing archives are affected?",
        "Anything written between the March and October transitions, for "
        "content created in that period. The repair tool in the current "
        "release detects and corrects it in place, and reports what it "
        "changed.",
        "OP| Ran the repair over four archives. It corrected two, left the "
        "winter ones alone, and the numbers match the web interface now.",
    ),
    "Duplicate attachments after re-run": (
        "Confirmed here. Pages and comments deduplicate on their identifier; "
        "attachments were deduplicating on filename, and the suffix logic "
        "meant they never matched an existing file.",
        "Byte-identical copies is the tell. If it were a genuine "
        "re-download you would at least expect different bytes occasionally.",
        "OP| Third run gave a third copy, so it was clearly not comparing anything.",
        "Fixed by keying on the attachment identifier like everything else. "
        "To clean up an existing archive, run the repair with the deduplicate "
        "option — it keeps the first copy and rewrites the references.",
        "OP| Repair took eleven minutes and the archive is back to a third of "
        "the size. All the references still resolve.",
    ),
    "Blog comments missing after migration": (
        "Count intact and bodies missing means the entry metadata was read "
        "and the comments feed was not.",
        "Blog comments live behind a separate feed per entry, and the "
        "migration changed its address. A 7.4 archive has them because 7.4 "
        "knew the old address.",
        "OP| So the export is asking the old question of a new server and "
        "getting nothing rather than an error?",
        "Getting an empty feed rather than an error, which is why it is "
        "silent. Fixed in 8.0.2 — the address is discovered from the entry "
        "rather than constructed.",
        "OP| Upgraded and re-exported. All nine comments on the entry I was "
        "testing with are there, and the older archive still has them too.",
    ),
    "Reader shows blank page on load": (
        "Blank with a valid index almost always means the reader cannot parse "
        "the manifest and stops before rendering anything.",
        "You mention one particular older build — was that a pre-release? "
        "There was a nine-day window where the manifest was written with a "
        "version marker that the current reader rejects.",
        "OP| It was, yes. That build is exactly the one those archives came from.",
        "Then the archive migration will rewrite the manifest and the reader "
        "will open it. Nothing else in the archive is affected, which is why "
        "the content looks fine on disk.",
        "OP| Migration took under a minute and both archives open normally "
        "now. Worth a line in the known issues, since the failure gives you "
        "nothing to search for.",
    ),
    "Crawler hangs on empty pages": (
        "Not a hang, a retry loop with no ceiling: an empty body is treated "
        "as a failed fetch, so it tries again indefinitely.",
        "Which is why it neither errors nor progresses. Raising the log level "
        "shows the same request repeating.",
        "OP| Confirmed — at debug level it is the same page, once every two seconds, forever.",
        "An empty body is now accepted as a legitimate empty page. Until you "
        "upgrade, a retry limit turns the hang into a recorded failure and "
        "the run completes.",
        "OP| Set a retry limit as a stopgap and the run finished. Upgraded "
        "since, and the empty page now exports as an empty page, which is "
        "what I wanted all along.",
    ),
    # -- Off Topic --
    "What are you reading right now?": (
        "A biography of an engineer nobody has heard of, which is about "
        "three-quarters bridges and entirely gripping.",
        "Currently on a short story collection, one a night. It is the only "
        "format I have managed to keep up since having children.",
        "OP| The one-a-night approach is smart. I keep taking on eight "
        "hundred pages and stalling at two hundred.",
        "Hotel novels are a genre unto themselves. If you get through it, "
        "there is a much older one about a spa town that is the obvious "
        "companion piece.",
        "OP| Ordered the spa one, and I am adopting the one-story-a-night "
        "rule for the month after. Good thread.",
    ),
    "Best coffee near the office?": (
        "The place two streets north, past the bank. Small, no seating worth "
        "the name, and the only one nearby that will make you something "
        "properly.",
        "Seconded, but avoid it between 08:30 and 09:15 unless you enjoy "
        "queueing with everybody else from this building.",
        "The market stall on Thursdays is better than either and only exists "
        "on Thursdays, which is a cruel arrangement.",
        "Worth saying the machine on the third floor is fine if you descale "
        "it, which nobody has done since it arrived.",
        "OP| Went north past the bank at 10:00 and it was worth the walk. "
        "Thursday stall noted for tomorrow.",
    ),
    "Recommend a podcast for commutes": (
        "Anything with an episode list you can dip into rather than a "
        "narrative you have to follow. Interview shows survive a tunnel "
        "better than documentaries.",
        "There is a series about the history of shipping containers that is "
        "far more interesting than that sentence suggests. Downloads cleanly, "
        "each episode stands alone.",
        "For niche: one where two people read out obscure standards documents "
        "and argue about them. It should not work and it does.",
        "Whatever you pick, download two episodes rather than one. The "
        "tunnel always comes at the worst moment.",
        "OP| Downloaded the shipping one for tomorrow and the standards one "
        "out of pure curiosity. Thank you both.",
    ),
    "Anyone else into home automation?": (
        "The still-in-use list at my house is short: lights on a schedule, a "
        "sensor that tells me the freezer door is open, and nothing else has "
        "survived.",
        "The freezer sensor is the one everyone eventually converges on. Mine "
        "has paid for itself once, which is the correct number of times.",
        "Everything voice-activated got quietly disabled here. Everything on "
        "a physical button stayed. There is a lesson in that and I have "
        "chosen not to learn it.",
        "The one I would add: anything that fails closed. A lock that needs "
        "the network to open is a lock that will eventually not open.",
        "OP| The physical button observation matches my house exactly. The "
        "rule seems to be that if it needs explaining, it does not last.",
    ),
    "Favorite conference talk this year?": (
        "The one about deleting code rather than writing it. Twenty-five "
        "minutes, no slides after the first, and I have sent it to four "
        "people.",
        "For me it was a talk on incident communication that spent the whole "
        "time on what to say to people who are not engineers. Nothing "
        "technical in it and the most useful thing I watched.",
        "Slightly against the grain: the best one I saw was a lightning talk "
        "that is not recorded anywhere, which is my annual reminder to go in "
        "person.",
        "The keynote everybody is quoting is worth skipping. The three "
        "talks in the same track after it are not.",
        "OP| That is three, which is exactly the length of list I wanted. "
        "Starting with the deleting-code one on the strength of “I sent it "
        "to four people”.",
    ),
    "Board game night, anyone in?": (
        "In, and I will bring two that take under twenty minutes to explain.",
        "Count me in. Last Thursday is better than last Friday for anyone "
        "with a commute, so good choice.",
        "In for the first one at least. Is there a limit on numbers, or do we "
        "just split into two tables if enough people turn up?",
        "In, and happy to be at the table that gets the game nobody knows. "
        "That is usually the better evening anyway.",
        "OP| Two tables if we go over eight. Nine people so far, so bring "
        "the short ones and we will split.",
    ),
    "What's your desk setup like?": (
        "One monitor, deliberately. I found that with two I put "
        "communication on the second one and then read it all day.",
        "The cable answer is unglamorous: a tray under the desk and half an "
        "hour with a bag of ties, once, three years ago. It has needed "
        "nothing since.",
        "Mine has a monitor at the wrong height rather than the wrong angle, "
        "and a laptop stand still in its box. Solidarity.",
        "Standing desk, used standing for about ten minutes a day, which I "
        "maintain is still worth it.",
        "OP| The under-desk tray is going on this weekend's list. The "
        "one-monitor argument is more persuasive than I want it to be.",
    ),
    "Best team lunch spot?": (
        "The place by the station takes groups without booking and is quiet "
        "enough to talk. Menu is short but has real options for both of your "
        "constraints.",
        "Avoid the big one on the square for nine people — they will split "
        "you across two tables at opposite ends and you will spend the hour "
        "shouting.",
        "The vegetarian place three streets over does a set lunch for groups "
        "and is entirely gluten-free on Tuesdays, which sounds invented and "
        "is true.",
        "Whichever you pick, ring ahead about the gluten-free rather than "
        "asking at the door. Two of the places near here say yes on the "
        "phone and no in person.",
        "OP| Booked the station one for next Wednesday and put the "
        "vegetarian place on the list for the following month. That is both "
        "constraints handled.",
    ),
    # -- Admin Corner --
    "Reminder: renew your access badge": (
        "Mine expired last week and I did not notice until the barrier "
        "stopped opening. The date is printed very small.",
        "The front desk can also check the expiry from your account if you "
        "cannot read it, which is faster than squinting.",
        "Is the photograph retaken every time, or can the existing one be reused?",
        "OP| Reused on request. Say so at the desk before they start, "
        "because the default is to take a new one.",
        "OP| Renewals are running smoothly this week — the midday queue has "
        "been the only complaint. Please come outside 12:00 to 14:00 if you "
        "can.",
    ),
    "Please update your on-call info": (
        "Updated. The form did not let me leave the escalation contact "
        "pointing at a rota rather than a person, which I think is the right "
        "call but worth mentioning.",
        "OP| Deliberate. A rota as an escalation target means a page can loop "
        "back into the same rota it came from, which has happened twice.",
        "What about people who are on a rota only during releases? There is no option that fits.",
        "OP| Use the rota field and put the qualification in the note. A free "
        "text note is read by a human at escalation time; a wrong structured "
        "value is not.",
        "OP| Two hundred and eleven entries updated, nine still outstanding. "
        "Those nine will get a direct message on Friday rather than another "
        "thread.",
    ),
    "New expense policy starting next quarter": (
        "Thirty days is tight for anyone travelling for two weeks with a "
        "week either side of catching up. Any flexibility there?",
        "OP| Thirty days from the date on the receipt, and travel is the case "
        "we expect to need the manager's note. It is a note, not an appeal — "
        "one line is enough.",
        "Does the no-pre-approval threshold apply per item or per claim? Ours "
        "used to be per claim and people split claims to stay under it.",
        "OP| Per item, and yes, we know why you are asking. Splitting a "
        "single purchase across claims is the thing the receipt requirement "
        "is there to catch.",
        "Clearer than the previous policy, which I read three times a year "
        "and never retained. Thank you for writing it in sentences.",
    ),
    "Security training due this month": (
        "Done, and it is genuinely better than last year. The phishing "
        "examples being real messages from here makes an enormous difference "
        "to how it lands.",
        "Can it be done in more than one sitting without losing progress? "
        "Forty minutes uninterrupted is not a thing I have.",
        "OP| Yes — progress is saved per section, and there are five "
        "sections. Closing the tab mid-section loses that section only.",
        "One correction for anyone taking it: the reporting address in the "
        "final section is out of date. Report through the button in the mail "
        "client instead.",
        "OP| Confirmed and being fixed in the module this week. The button is "
        "the right route regardless — it captures the headers, which "
        "forwarding does not.",
    ),
    "Office move: what you need to know": (
        "Is the desk allocation published anywhere, or do we find out on the Monday?",
        "OP| Published Thursday, on the floor plan page. Teams are kept "
        "together; where a team is split across two blocks it is because the "
        "team is larger than a block.",
        "What happens to the monitor arms? Mine is on the desk rather than in "
        "a drawer but it is definitely mine.",
        "OP| Anything attached to the desk moves with the desk. Label it "
        "anyway if it is personal, because the movers cannot tell.",
        "OP| Move completed over the weekend. Two boxes are unclaimed by the "
        "lifts on the new floor — if you are missing something, look there "
        "before assuming it went to storage.",
    ),
    "Parking changes starting Monday": (
        "Is the overflow area lit? It is dark by the time I leave in November.",
        "OP| Lit, and the path back to the side entrance is lit as well. The "
        "side entrance stays badge-accessible until 20:00 for the duration.",
        "Three weeks is optimistic for resurfacing in this weather. Is there "
        "a plan if it runs long?",
        "OP| If it runs past the three weeks we will say so here rather than "
        "letting people discover it. The overflow allocation simply "
        "continues.",
        "OP| It did run long — one extra week, finishing on the 28th. Lower "
        "level reopens the following Monday and everything reverts.",
    ),
    "Please RSVP for the town hall": (
        "Will the stream be available afterwards for anyone in a bad time zone?",
        "OP| Yes, posted within a day, with the questions and answers as text alongside it.",
        "Are anonymous questions really read out in full? Last time two seemed to get summarised.",
        "OP| In full and in order. The two you are thinking of were "
        "duplicates of each other and were merged, which we should have said "
        "at the time.",
        "OP| Catering numbers are set as of Friday. Anyone who missed the "
        "deadline is still welcome — there will be seats, there may not be "
        "sandwiches.",
    ),
    "Benefits enrollment closes Friday": (
        "The rollover being automatic is helpful. Is there a way to see what "
        "last year's selections actually were before deciding?",
        "OP| Yes — the summary at the top of the form is last year's "
        "selections, and it prints. Most people do not scroll to it because "
        "the form opens below it.",
        "Do the two new options replace anything, or are they additional?",
        "OP| Additional. Nothing has been removed this year, which is why "
        "rolling over is safe if you are happy with what you had.",
        "OP| Window is closed. Late changes need a life-event reason and go "
        "through the same form, which stays open all year for exactly that.",
    ),
}

_GENERIC_REPLIES: tuple[str, ...] = (
    "Following this -- we are about to hit the same thing.",
    "Could you share the exact error text? The wording usually narrows it down quickly.",
    "We worked around it by running in smaller batches, though that treats "
    "the symptom rather than the cause.",
    "Linking this thread from the internal notes so the next person finds it faster.",
    "Reproduced on our side, so at least it is not something local to you.",
    "Worth raising as an issue if nobody has already.",
)


#: Marks a written reply as spoken by the topic's original poster. Carried
#: inline rather than in a parallel index, which would drift out of sync with
#: the text the moment a thread is edited.
_OP_MARK = "OP| "


def topic_reply(title: str, index: int) -> tuple[str, bool]:
    """The `index`-th reply in a topic's thread, and whether the ORIGINAL
    POSTER said it.

    Written threads run in order, so the conversation follows its own
    question -- and the person reporting "that fixed it" is the person who
    asked, not a bystander. Beyond the written replies (or for a topic with
    none) the generic pool continues, offset by the title so two topics do
    not fall into the same sequence.
    """
    written = TOPIC_REPLIES.get(title)
    if written and index < len(written):
        line = written[index]
        if line.startswith(_OP_MARK):
            return line[len(_OP_MARK) :], True
        return line, False
    offset = _shape_index(title, len(_GENERIC_REPLIES))
    consumed = len(written) if written else 0
    return _GENERIC_REPLIES[(offset + index - consumed) % len(_GENERIC_REPLIES)], False


# --------------------------------------------------------------------
# Wiki page comments, keyed by page title
# --------------------------------------------------------------------
#
# Page comments came from one pool of ten lines shared by every page in
# every wiki AND by every blog post, picked by a hash of the page label.
# Two things followed. A page acquired comments about things it does not
# contain -- "The screenshot no longer matches the current UI" on a page
# with no screenshot -- and, because the demo's busiest page carries 41 of
# them, the same ten lines appeared four times over on a single page.
#
# These are written per page, in the voice of colleagues who have read
# that page: a correction, a question about ownership, an answer. `OP| `
# marks a comment left by the page's own author (see `page_comment`).

PAGE_COMMENTS: dict[str, tuple[str, ...]] = {
    # -- Engineering Handbook --
    "Onboarding": (
        "The access request in step 4 goes to the old ticket queue, which "
        "nobody watches. It should point at the platform request form.",
        "OP| Fixed, thank you. That queue was closed in March and I clearly missed one link.",
        "Could we say somewhere near the top how long the whole thing takes? "
        "I set aside an afternoon and needed a day and a half.",
        "Half a day of that is waiting for accounts to propagate, which is "
        "worth calling out separately -- it is not work, it is waiting.",
        "New starter here: everything worked except that I did not know what "
        "the abbreviation in step 7 meant, and search did not find it either.",
        "OP| Spelled it out in step 7 and added it to the glossary. That is "
        "the third time this month somebody has hit that one.",
    ),
    "Dev Environment": (
        "The setup script assumes you already have the container runtime "
        "installed. On a clean machine it fails on line one with an error "
        "that does not say so.",
        "Confirmed on a fresh laptop this morning. Adding a check at the top "
        "of the script would be kinder than documenting it here.",
        "OP| Added the check, and it now prints the install command for your "
        "platform rather than just complaining.",
        "Worth noting that the memory requirement in the prerequisites is "
        "optimistic. It runs in 8GB and it is not pleasant.",
        "Ran through this on a machine with an unusual chip architecture and "
        "everything worked except the database image. There is a variant tag "
        "that fixes it -- happy to add a line if that is useful.",
    ),
    "Coding Standards": (
        "Can we drop the line length rule now that the formatter enforces it "
        "automatically? Having it stated here as well means it gets argued "
        "about in reviews.",
        "OP| Removed. Anything the formatter decides should not also be an opinion on this page.",
        "The section on naming contradicts the example directly beneath it, "
        "which is my fault -- I updated one and not the other.",
        "Fixed the example rather than the rule, since the rule is what people quote.",
        "I would like to add something about comments explaining why rather "
        "than what. It is the most common thing I write in reviews and it is "
        "not on this page anywhere.",
        "OP| Please do. Add it with an example of each; the rule on its own does not land.",
    ),
    "Code Review": (
        "The 24-hour expectation is unrealistic for anyone in a time zone "
        "that does not overlap with the reviewers listed here.",
        "Agreed. In practice we treat it as one working day of the reviewer, "
        "not 24 hours of clock, and the page should say that.",
        "OP| Reworded to one working day. Thanks -- it was never meant to mean overnight.",
        "Could this link to the standards page rather than restating three of "
        "the rules? They have already drifted apart once.",
        "Second that. Restating anything is a promise to keep two copies matching, and we do not.",
    ),
    "Release Process": (
        "Step 6 says to notify support before the deploy. Support would like "
        "it to say how long before, because ten minutes is not useful to "
        "them.",
        "OP| Changed to the previous working day for anything user-visible, "
        "and ten minutes is fine for a patch nobody will notice.",
        "The rollback step assumes the migration is reversible. Ours have not "
        "all been, and it would be better to say plainly which kinds are not.",
        "That is a good catch and probably deserves its own section rather than a sentence here.",
        "OP| Agreed. I have added a stub and will fill it in this week -- "
        "better an obvious gap than a confident wrong answer.",
    ),
    "Hotfix Runbook": (
        "Used this at 02:00 on Tuesday. It worked, and the one thing missing "
        "was who to wake if the approval step is blocked.",
        "OP| Added the escalation contact at the top, where you will actually "
        "see it at 02:00 rather than at the bottom.",
        "Suggest adding the expected duration of each step. Knowing the "
        "verification takes twenty minutes stops you assuming it has hung.",
        "Strongly agree. The first time I ran this I restarted it because I thought it was stuck.",
        "Last verified against the current release in September and every step still matches.",
    ),
    "On-Call Guide": (
        "The paging schedule here disagrees with the rota tool, which is "
        "authoritative. Suggest this page links to it rather than repeating "
        "it.",
        "OP| Replaced the table with a link. It has been wrong twice and that "
        "is twice too many for something people read at three in the "
        "morning.",
        "The section on what not to do during a shift is the most useful part "
        "of this page and it is at the bottom.",
        "Moved it up. It was written last, which is not a reason for it to be read last.",
        "Can we add the quiet hours policy? New joiners keep asking whether "
        "they are allowed to not answer chat while handling a page.",
        "OP| Added, in one sentence: while you are handling a page, chat can "
        "wait, and nobody will mind.",
    ),
    "Incident Retro": (
        "The template asks for a root cause. Could we make that plural, or "
        "rename it? Every retro we have run has found several and the "
        "singular pushes people to pick one.",
        "OP| Renamed to contributing factors. It is a small change and it "
        "visibly changed the shape of the last two write-ups.",
        "Please keep the timeline section mandatory. It is the part people "
        "want to skip and the part that is worth reading a year later.",
        "The prompt about what went well is doing real work too. Without it "
        "the document reads like a charge sheet.",
        "One addition I would like: a field for what we decided not to do. "
        "Otherwise it looks, later, like nobody considered the obvious "
        "option.",
    ),
    # -- Product Wiki --
    "Vision 2026": (
        "This is the clearest statement of direction we have had, and it is "
        "also four pages long. Is there a one-paragraph version we can put in "
        "front of it?",
        "OP| Added a summary at the top. If the paragraph and the four pages "
        "ever disagree, the four pages are what we agreed.",
        "The second theme overlaps heavily with what the platform group has "
        "been calling something else for a year. Worth reconciling the "
        "vocabulary before it hardens.",
        "Seconded, and I would go further -- the two teams are describing the "
        "same work and have not noticed.",
        "Which parts of this are commitments and which are aspirations? The "
        "prose reads the same for both and that matters when it is quoted "
        "back at us.",
        "OP| Fair. I have marked the three commitments explicitly and left "
        "everything else as direction.",
    ),
    "Roadmap": (
        "Dates here are a quarter ahead of the ones in the planning "
        "spreadsheet. Which of the two is meant to be right?",
        "OP| This page. The spreadsheet is a working document and I have "
        "added a note at the top of it saying so.",
        "Can we show what came off the roadmap as well as what is on it? The "
        "removals are the interesting part and they currently vanish "
        "silently.",
        "A struck-through section at the bottom would do it and costs nothing.",
        "The search work has moved twice now. Might be more honest to take it "
        "off entirely until there is a design.",
    ),
    "Q3 Themes": (
        "Three themes for a quarter feels like one too many given two of them "
        "need the same three people.",
        "That is the argument I lost in planning, for the record. Happy to be proved wrong.",
        "OP| Noted here rather than in chat so it is on the page: if we slip, "
        "the third theme is the one that moves.",
        "The measures under theme two are activity counts rather than "
        "outcomes. Counting exports run does not tell us whether anyone got "
        "what they needed.",
        "Agreed, and that is fixable this week -- the outcome measure exists, "
        "it is just not the one we wrote down.",
    ),
    "Personas": (
        "Two of these four read like the same person with a different job "
        "title. Their goals, tools and frustrations are nearly identical.",
        "OP| They are, and I have merged them. Four was a target rather than "
        "a finding, which is exactly how personas go wrong.",
        "Where does the evidence for the third one come from? It is the one I "
        "hear quoted most and I cannot find the interviews behind it.",
        "OP| Six interviews from the spring round, now linked at the bottom "
        "of that section. Good challenge -- it should have been there from "
        "the start.",
        "Suggest adding what each persona does not care about. That "
        "has settled more design arguments for us than the goals have.",
    ),
    "Competitor Notes": (
        "The pricing figures for the second entry are from last year and they "
        "have changed their model since.",
        "Updated with this quarter's published figures and a date on each row "
        "so it is obvious when they go stale again.",
        "Can we be careful to keep this factual? A couple of the lines here "
        "read as opinion dressed as observation.",
        "OP| Agreed and edited. Anything we cannot point at a public source "
        "for should be on a different page.",
        "The most useful column is the last one -- what customers say when "
        "they switch. That is the only part of this page I have ever quoted.",
    ),
    "Pricing Rationale": (
        "This explains the current model well. What it does not explain is "
        "why we rejected usage-based pricing, which is the first thing "
        "everybody asks.",
        "OP| Added a section on it. Short version: it prices archiving by the "
        "thing customers cannot control, which punishes exactly the "
        "deployments we want.",
        "The worked example in the middle uses figures that no longer match the price list.",
        "Fixed the example. Left the reasoning alone, since that has not changed.",
        "Worth stating who can approve an exception. It happens, and at the "
        "moment the answer lives in one person's head.",
        "OP| Added, with the threshold above which it needs a second approver.",
    ),
    "Launch Checklist": (
        "We used this for the last launch and finished with three items "
        "unticked because they did not apply. Could items be marked as "
        "conditional rather than everyone deciding individually?",
        "OP| Added an applies-when column. Anything without one is unconditional.",
        "The support hand-off item should come earlier. By the time it "
        "appears here the announcement has already gone out.",
        "Moved it above the announcement step, which is where we actually do it.",
        "Please keep this to one page. The previous version ran to three and "
        "the last page was never read.",
    ),
    "Success Metrics": (
        "Four of the seven metrics here are things we can move by changing "
        "the interface without helping anybody. Worth naming those as "
        "diagnostics rather than goals.",
        "OP| Split into goals and diagnostics. It is a better page for it, and shorter.",
        "The retention definition does not say what counts as active. Two "
        "teams are reporting different numbers and both are right by their "
        "own reading.",
        "Defined it explicitly and added the query we use, so anybody can reproduce the number.",
        "Can we add the number we would be unhappy with, alongside the target? "
        "A target with no floor gets quietly renegotiated.",
    ),
    # -- Operations KB --
    "Runbooks Index": (
        "Six of the runbooks linked here no longer exist. The links have been "
        "dead long enough that nobody notices them any more.",
        "OP| Removed the six and added a last-checked date at the top so this "
        "is visible next time.",
        "Could the index show when each runbook was last verified? An index of "
        "runbooks nobody has walked through is a false sense of security.",
        "That is a better idea than the date on this page. It puts the "
        "staleness next to the thing that is stale.",
        "Sorting by service rather than alphabetically would help during an "
        "incident, which is the only time this page gets opened in anger.",
    ),
    "Backup & Restore": (
        "The restore procedure has never been run end to end on this "
        "deployment, only on the test one. That should be stated plainly at "
        "the top.",
        "OP| Stated, with the date of the last test restore and which "
        "environment it was on. Uncomfortable reading, which is the point.",
        "Step 3 references a storage path that changed in the summer migration.",
        "Corrected, and I checked the other three references to the old path "
        "on neighbouring pages while I was there.",
        "How long does a full restore actually take? The page describes the "
        "steps and not the duration, and the duration is what gets asked in "
        "the meeting.",
        "OP| Added measured timings from the last test: about forty minutes "
        "for the data and another twenty before search is usable again.",
    ),
    "DR Playbook": (
        "The decision to fail over sits with a role that no longer exists "
        "after the reorganisation.",
        "OP| Updated to the current role, and I have named the deputy as "
        "well. A single point of decision is its own kind of outage.",
        "The communications section should include what we tell customers, "
        "not only who we tell internally.",
        "Agreed. There is a template somewhere from the spring incident that "
        "would drop straight in.",
        "Genuine question: has any part of this been rehearsed? A playbook "
        "that has only been read is a document, not a plan.",
        "OP| The failover itself was rehearsed in June. The communications "
        "half has not been, and I have scheduled that rather than pretend "
        "otherwise.",
    ),
    "Network Diagram": (
        "This diagram is missing the second region entirely, which has been live since April.",
        "OP| Redrawn with both regions and the link between them. The old "
        "image is kept in the version history rather than deleted.",
        "Could the source file live alongside the exported image? Nobody can "
        "edit it at the moment without redrawing from scratch.",
        "Attached the source. It needs a specific tool to open, which is noted next to it.",
        "The arrow directions on the two queues are the wrong way round -- "
        "small thing, but it is the detail people trace during an incident.",
    ),
    "Access Requests": (
        "The approval path here skips the data owner for anything classified "
        "as restricted, which contradicts the classification page.",
        "OP| The classification page is right and this one was out of date. "
        "Corrected, and I have linked the two so the next person can check.",
        "Typical turnaround would be a useful thing to state. People chase "
        "after a day because they have no idea whether a day is normal.",
        "It is two working days for standard and up to a week for anything "
        "needing a data owner. Added.",
        "Please keep the “what to do if it is urgent” paragraph. It is the "
        "only reason I have ever found this page.",
    ),
    "Vendor Contacts": (
        "Two of the contacts listed here have left their companies. The "
        "support portal is a better first route for both of them anyway.",
        "OP| Replaced named individuals with the portal and a role address "
        "everywhere. Individuals go stale and portals do not.",
        "Contract renewal dates would be worth having on this page, or at "
        "least a link to where they live.",
        "They are in the procurement system and I would rather link than "
        "copy, given how this page has aged.",
        "The out-of-hours number for the third vendor is missing, and that is "
        "the one we have actually needed out of hours.",
    ),
    "Escalation Paths": (
        "The second tier here is a team that was split in two last quarter. "
        "Neither half currently believes it owns this.",
        "OP| Raised it with both leads and it now sits with the platform "
        "half. Written here rather than agreed in a meeting nobody minuted.",
        "Suggest adding how long to wait at each tier before escalating "
        "further. People sit at tier one for hours out of politeness.",
        "Fifteen minutes for anything customer-visible, an hour otherwise. "
        "Added to each tier rather than as a general note, so it is where you "
        "are looking.",
        "This page and the on-call guide describe the same first step "
        "differently. Not badly, just differently enough to hesitate over.",
        "OP| Aligned the wording with the on-call guide and linked them both ways.",
    ),
    "Change Log": (
        "Three entries in a row have no author against them, so there is "
        "nobody to ask about any of them.",
        "OP| Chased those down and filled them in. The template now has the "
        "author field first rather than last, which should help.",
        "Could we note the change that was reverted as well as the revert? At "
        "the moment the log reads as though nothing happened.",
        "Agreed -- a revert with no record of what it undid is the entry you "
        "most want six months later.",
        "This has been kept up properly for eight months now, which is longer "
        "than any previous attempt. Worth saying so.",
    ),
}

#: The curated demo dataset (`prototype._WIKIS`) spells this page with its
#: full name while the synthesized pool uses the short one. Same page, one
#: written thread -- an alias rather than a second copy, which would drift.
PAGE_COMMENTS["Incident Retro Template"] = PAGE_COMMENTS["Incident Retro"]


# --------------------------------------------------------------------
# Blog post comments, keyed by post title (`synth._BLOG_POST_TITLES`)
# --------------------------------------------------------------------
#
# Blog comments were drawn from the same ten-line pool as wiki page
# comments, so a post about dark mode collected "Should this live under
# Operations instead?" and the demo's six posts shared their remarks with
# twenty wiki pages.
#
# Written per post, three deep, in the order the synthesizer threads them:
# a reader's remark, the author's reply to it (`OP| ` -- the second comment
# is the one `_synthesize_blog` hangs off the first), then a second
# reader's remark on the same post.

POST_COMMENTS: dict[str, tuple[str, ...]] = {
    # -- Engineering Blog --
    "What we shipped in Q3": (
        "Nine minutes for a run that used to take fifty-one is a bigger deal "
        "than the search rewrite would have been. Good call on the ordering.",
        "OP| It felt less impressive to write up, which is probably a lesson "
        "about what we choose to announce.",
        "The filename change is the one that will save my team time. We had "
        "a script purely for mapping hashes back to names.",
    ),
    "Postmortem: the Friday outage": (
        "The detail about the dashboards being green is the part worth "
        "circulating. Green dashboards during an outage is the failure mode "
        "we all have.",
        "OP| It is the thing I would most like people to take from it. We "
        "were measuring health, not work.",
        "Rejecting values below 1000 outright is the right fix. A unit "
        "mistake that produces a plausible number is nearly undetectable in "
        "review.",
    ),
    "Why we moved off the old exporter": (
        "“Every bug report needed a new branch in the same function” is a "
        "sharper diagnostic than most of what gets written about legacy code.",
        "OP| It took us about eighteen months to notice the pattern, so I "
        "would not claim any great insight.",
        "Curious whether the 600 deleted lines came back anywhere else, or "
        "whether the model change genuinely absorbed them.",
    ),
    "Migrating our test suite to pytest": (
        "Two tests passing because a sibling left the right row behind is "
        "exactly the kind of thing a rewrite finds and a refactor does not.",
        "OP| Both had been green for two years. That is the uncomfortable part.",
        "Interested that parallelism helped less than expected. We assumed "
        "the opposite and are now going to go and measure.",
    ),
    "Three lessons from the migration": (
        "The rollback plan failing on its first step in rehearsal is the most "
        "useful sentence in this post.",
        "OP| It is also the cheapest of the three lessons to act on, which is "
        "why I put it last -- people remember the last one.",
        "Running both systems in parallel for six weeks is expensive advice. "
        "Worth saying how you decided it was affordable.",
    ),
    "How we cut build times in half": (
        "A timestamp in the cache key is such a specific mistake that I have "
        "now gone and checked ours. Ours was fine. I would not have looked.",
        "OP| That reaction is the entire reason for writing it up.",
        "Deleting the duplicate lint step is the one I would push back on "
        "slightly -- formatters and linters overlap but not completely.",
    ),
    "Retiring the legacy importer": (
        "The silent truncation at 128 characters is going to bite somebody. "
        "We were absolutely using truncated titles as identifiers.",
        "OP| Then this post has already paid for itself. Please check before "
        "the 30th rather than after.",
        "Could the 410 include a link to the migration guide in the body? "
        "Whoever hits it will be reading a log, not a browser.",
    ),
    "On-call, six months in": (
        "The rule that on-call does no planned work is the whole thing. "
        "Everything else is scheduling.",
        "OP| It was also the only part anybody argued about, which in hindsight was a signal.",
        "Nobody swapping out of a shift since May is a better metric than "
        "page volume and I am stealing it.",
    ),
    # -- Product Updates --
    "Welcome to the new starters": (
        "The point about internal docs assuming context is right, and it is "
        "hardest to see for the people who wrote them.",
        "OP| Which is why we are asking the new starters to file it rather "
        "than asking ourselves to notice.",
        "Four in one month across two time zones is a lot of pairing. Hope "
        "the mentors got some of their week back.",
    ),
    "Introducing the new dashboard": (
        "Opening on my own recent activity rather than system health is the "
        "right default and I am surprised it took this long.",
        "OP| Us too, honestly. The old default was a decision made when the "
        "only users were the team that built it.",
        "Please do move the export button back. I have hunted for it in the "
        "overflow menu four times today.",
    ),
    "Faster search, fewer clicks": (
        "Applying the scope before the query rather than after is a real "
        "change in behaviour, not just speed. Worth flagging it as such.",
        "OP| Fair -- it is in the release notes as a behaviour change and "
        "should have been clearer here too.",
        "Enter opening a single result directly is the small one everybody will feel. Thank you.",
    ),
    "What's new this release": (
        "Comment threads in exports by default is the change we have been "
        "waiting for. The opt-in was buried three screens deep.",
        "OP| It was, and roughly nobody found it before their first export "
        "came out missing the discussion.",
        "Was the old 100MB ceiling really just where uploads timed out? That "
        "is a wonderfully honest thing to admit in release notes.",
    ),
    "Feedback we heard and acted on": (
        "Publishing the item you only partly addressed, and saying so, is "
        "what makes the rest of this credible.",
        "OP| The alternative was claiming the large-archive work was done, "
        "and everyone with a large archive would have known within a day.",
        "214 responses from a base this size is a good return. What changed "
        "between this survey and the last one?",
    ),
    "A cleaner onboarding flow": (
        "Nine screens to three is good, and the reasoning about not asking "
        "people to decide on day one is better.",
        "OP| That was the argument that unlocked it internally. Flexibility "
        "at the wrong moment is not flexibility.",
        "Please make sure the removed options are findable in Settings by the "
        "names they had in setup. Renaming them at the same time would undo "
        "half the benefit.",
    ),
    "Dark mode is here": (
        "The inverted-filter first attempt is a familiar mistake. Glad you "
        "threw it away rather than shipping it.",
        "OP| It survived about a week internally before somebody pointed at a "
        "shadow and asked what was wrong with it.",
        "Status badge contrast is the rough edge I would prioritise. Two of "
        "them are genuinely hard to read on my screen.",
    ),
    "Roadmap check-in": (
        "Dropping the plugin API because every conversation about it became a "
        "different feature request is a good instinct.",
        "OP| We are going back to the people who asked. My guess is there are "
        "three separate needs in there and none of them is a plugin API.",
        "Starting and stopping search ranking twice, said out loud, is worth "
        "more than a green status square.",
    ),
    # -- Team Notes --
    "How we run retros now": (
        "One theme per retro is the change we made too, and the thing nobody "
        "warns you about is how much it hurts to drop the other eight.",
        "OP| It does. Writing them down and visibly not discussing them is what made it bearable.",
        "Rotating the facilitator alphabetically removes a surprising amount "
        "of politics. Recommended.",
    ),
    "New faces on the team": (
        "Pairing instead of starter tickets matches what we found. Starter "
        "tickets teach the tracker.",
        "OP| That phrasing is better than mine and I am going to use it.",
        "The thirty-abbreviations point is painfully accurate. I counted "
        "eleven in one standup last week.",
    ),
    "Our hybrid-work experiment": (
        "Fixed days beating “come in when useful” matches our experience "
        "exactly, and for the same reason.",
        "OP| The coordination cost is invisible until you remove it.",
        "Moving off Thursday because of the design review and school runs is "
        "the kind of detail most write-ups leave out. It is also the reason "
        "these things fail.",
    ),
    "Notes from the offsite": (
        "The unplanned lunch session being the best one is nearly universal, "
        "and nobody schedules for it.",
        "OP| We are going to leave a genuinely empty two-hour block next "
        "time and see whether that works or just feels like a gap.",
        "Staging getting an owner is worth more than the rest of the offsite "
        "combined, from out here.",
    ),
    "What we're reading this month": (
        "The one-hour rule is the reason your reading group still exists. "
        "Ours died on a 400-page book.",
        "OP| We learned that the same way.",
        "Which queueing paper? I have been looking for something short to "
        "hand to people who do not believe in utilisation.",
    ),
    "A day in the life of support": (
        "Six defects out of forty-one conversations is a number every "
        "engineering team should have to look at once a quarter.",
        "OP| The third category -- people checking that something worked -- "
        "is the one that changed what we are doing this quarter.",
        "“An export that finishes silently generates a ticket as reliably as "
        "one that fails” belongs on a wall somewhere.",
    ),
    "Hiring: what we look for": (
        "Dropping algorithm puzzles because they were uncorrelated, rather "
        "than on principle, is a much more persuasive reason.",
        "OP| It is also the only reason that survived the argument.",
        "The referral advice is good: what you have seen someone do, not what "
        "you think they are like.",
    ),
    "Saying goodbye to a teammate": (
        "Six weeks of handover that is genuinely finished is rarer than the "
        "post suggests. Well done to everyone involved.",
        "OP| Most of the credit goes to the person leaving, who started it "
        "before telling anyone the date.",
        "Rewriting the runbooks rather than copying them is the detail that "
        "makes this a real handover.",
    ),
    # -- Release Notes --
    "Version 8.0: what's inside": (
        "Removing the compact archive format with no users and no tests is "
        "the most underrated line in here.",
        "OP| It had one user in 2023, who is on the current format now. We checked.",
        "Attachment ceiling to 200MB is what unblocks us. Our design team's "
        "files were the whole reason we were still on the old exporter.",
    ),
    "Patch notes: stability fixes": (
        "Progress able to exceed 100% is the kind of bug that undermines "
        "confidence in everything around it.",
        "OP| Agreed, which is why it is in a patch rather than waiting for 8.1.",
        "Repairing existing archives on first open, without asking, is the "
        "right choice here. Thank you for not adding a migration step.",
    ),
    "Deprecating the old API": (
        "Twelve months with a header on every response is about as fair as a deprecation gets.",
        "OP| The header is the part we would repeat. Nobody audits code; everybody greps logs.",
        "Cursor-based pagination is the real work. Worth its own guide rather "
        "than a paragraph in this one.",
    ),
    "Breaking change: auth headers": (
        "Rejecting a request that carries both credentials is the right "
        "decision and will annoy exactly the people it should.",
        "OP| We expect a handful of angry tickets and we will take them.",
        "Please make the 8.2 failure message say “old header ignored” rather "
        "than a bare unauthorized. That is the whole difference for whoever "
        "debugs it.",
    ),
    "Performance improvements this release": (
        "38 seconds to 6 for listing is the number that will get us to "
        "upgrade this quarter rather than next.",
        "OP| The pagination change is doing almost all of that, and it needs "
        "no configuration from you.",
        "Peak memory down a third as a side effect is worth its own line -- "
        "that is what lets us run these on smaller machines.",
    ),
    "Bug bash results": (
        "Logging the four “correct behaviour that reads as a bug” items "
        "separately is the best thing in this summary.",
        "OP| Four people misreading the same thing in two hours is data, not user error.",
        "Five independent reports of the export button location should "
        "probably settle that debate.",
    ),
    "Upgrade guide for 8.0": (
        "The rollback section is unusually honest about what you lose. Most "
        "upgrade guides pretend rollback is free.",
        "OP| It is not free, and finding that out during a rollback is the worst possible time.",
        "A minute per 10,000 items matched almost exactly for us -- 41,000 "
        "items, just over four minutes.",
    ),
    "Known issues and workarounds": (
        "Updating this page in place rather than superseding it with a new "
        "post is the correct decision. Please keep doing that.",
        "OP| It is linked from the release notes for exactly that reason.",
        "The scheduled-crawl token issue has bitten us twice. Glad the error "
        "message is being fixed regardless of the workaround.",
    ),
    # -- Field Report --
    "Notes from a customer visit": (
        "A wall screen showing a hand-updated spreadsheet is the most damning "
        "usability finding you can get, and it is not in any survey.",
        "OP| They were slightly embarrassed to show me. They should not have been.",
        "Running every export twice because you cannot know in advance what "
        "will fail is a strong argument for a dry-run mode.",
    ),
    "What our biggest client taught us": (
        "Optimising the hour you can see rather than the week you cannot is a "
        "trap I have fallen into twice.",
        "OP| Three times here, if I am honest about it.",
        "Writing the changelog for someone deciding whether to upgrade is a "
        "small change with a large effect. We did the same last year.",
    ),
    "Rolling out to 500 users": (
        "Starting with the department that cares least is counterintuitive "
        "and obviously right once stated.",
        "OP| Enthusiasts route around problems and then tell you it went "
        "well, which is worse than a complaint.",
        "Both rollbacks coming from a shared mailbox is a good reminder that "
        "the permissions model is where the assumptions live.",
    ),
    "Lessons from a failed pilot": (
        "Publishing a failed pilot at all puts you ahead of most. The stated "
        "failure condition is the actionable part.",
        "OP| It has already stopped one pilot six weeks earlier than we would have.",
        "Knowing in week two and taking nine more weeks to say it is so "
        "familiar it is uncomfortable.",
    ),
    "Support tickets we learned from": (
        "“It worked yesterday and I cannot tell you what I did differently” "
        "is nearly always a credential. Every time.",
        "OP| Three separate customers in one month, all expiry.",
        "Reading a quarter of tickets as a corpus rather than a queue is a "
        "practice I am going to copy.",
    ),
    "A week shadowing the help desk": (
        "The observation that every screenshot is a message we failed to make "
        "quotable is going straight into our error-handling guidelines.",
        "OP| It reframed the whole week for me.",
        "The help desk knowing the priority order of what is bad, and never "
        "being asked, is the sentence that should embarrass us most.",
    ),
    "What partners are asking for": (
        "Six conversations converging on completion notifications is about as "
        "clear a signal as you get.",
        "OP| And three of them had already built polling and apologised for "
        "it, which says something too.",
        "“Nobody asked for a plugin API” is the useful negative result. Worth "
        "putting in front of whoever was planning one.",
    ),
    "Field notes: the EU rollout": (
        "Four weeks establishing who could approve a configuration is the "
        "real story here, and it is the part nobody budgets for.",
        "OP| Which is why it is written down. Next time it is a line item with a name against it.",
        "The date format confusion is so mundane and so expensive. We ended "
        "up at the same answer you did.",
    ),
    # -- Customer Stories --
    "How Acme cut onboarding time in half": (
        "Deciding which wiki was authoritative, rather than buying anything, "
        "is the actual intervention here.",
        "OP| They were clear about that when we spoke to them, and it is why "
        "we wrote it this way round.",
        "Archiving before agreeing what was authoritative is a mistake I can see us making. Noted.",
    ),
    "A migration that just worked": (
        "Writing the reconciliation query before the migration is the trick, "
        "and I had not heard it put that plainly before.",
        "OP| A reconciliation written afterwards checks what you already know went well.",
        "Seventeen failures found on the rehearsal run is the number that "
        "made the real weekend dull. Worth the copy.",
    ),
    "From spreadsheets to a real workflow": (
        "Resisting priorities when the old spreadsheet had a priority column "
        "nobody used takes real discipline.",
        "OP| They said it was the hardest decision in the whole project.",
        "Nineteen days to four, from nothing but requests no longer sitting "
        "unnoticed, is a good argument for visibility over features.",
    ),
    "Scaling support without scaling headcount": (
        "Handling time up, volume down more, is the trade every support "
        "manager should be allowed to make and most are not.",
        "OP| It only works if the documentation is theirs to edit, which is "
        "the caveat at the end and the reason it usually fails.",
        "The public queue lowering the temperature of tickets matches what we "
        "saw. A problem with four other people on it reads differently.",
    ),
    "Why they switched from the old tool": (
        "Evaluating tools by breaking something on purpose and seeing whether "
        "the output says so is the best procurement test I have read.",
        "OP| Two of four passed it, which surprised them and does not surprise me.",
        "Migrating six years of old archives taking longer than the "
        "evaluation is the warning to take from this.",
    ),
    "Small team, big rollout": (
        "No training sessions, four pages, and an open call nobody attends "
        "until it matters. This is the model.",
        "OP| The call being empty most days is the part people find hardest to justify keeping.",
        "Not migrating history at first is the choice that removes the "
        "deadline, and almost nobody makes it.",
    ),
    "The integration that saved us hours": (
        "Attaching a rendered copy as well as a link is the detail that makes "
        "this useful in two years rather than embarrassing.",
        "OP| They learned that from a previous integration that only linked.",
        "Four minutes thirty times a week is a person-week a quarter, and "
        "nobody would ever have staffed a project for it.",
    ),
    "What success looks like a year in": (
        "Flat usage since month four being presented as success rather than failure is refreshing.",
        "OP| Growing engagement in year two for a tool like this would worry "
        "us as much as it worries them.",
        "Arguments ending because someone looks up what the page said in "
        "March is the most concrete benefit in the whole piece.",
    ),
    # -- Design Diary --
    "Rethinking the empty states": (
        "A filtered list with no results looking identical to a system with "
        "no data is a bug wearing a design costume.",
        "OP| That one had been there since the first version and nobody "
        "filed it, which is its own finding.",
        "On offering the action: maybe only in the first case, and phrase it "
        "as information in the other two.",
    ),
    "A new type scale": (
        "Spending an afternoon on the ratio and a fortnight on what each step "
        "is for is exactly the right proportion.",
        "OP| The second part is what keeps it from drifting back to eleven sizes.",
        "Building the second attempt against the longest titles in the demo "
        "data is a good trick. Real content breaks scales.",
    ),
    "Why we simplified the nav": (
        "Watching people go straight to what they know, or search, matches "
        "every study I have seen and still surprises stakeholders.",
        "OP| It surprised me, and I had argued for two of the items that went away.",
        "Being straight about the trade for daily users -- and not "
        "pretending it away -- makes the rest of the post trustworthy.",
    ),
    "Prototyping in the open": (
        "Rough prototypes getting flow feedback and polished ones getting "
        "colour feedback is the oldest lesson in the field and always worth "
        "restating.",
        "OP| Knowing it and believing it turn out to be different things.",
        "The deletion date is the rule I would copy. An old prototype that "
        "looks plausible really is a rumour with a URL.",
    ),
    "Color contrast, take three": (
        "Passing the automated check and still being unreadable is the "
        "experience of everyone who has done this seriously.",
        "OP| All three of our worst offenders passed, which is what finally "
        "moved us to component-level review.",
        "Tuning dark mode for the dim room is the right default, on exactly the reasoning given.",
    ),
    "Notes from a usability session": (
        "Somebody reading “403” aloud and then guessing correctly, while "
        "telling you they were guessing, is a perfect finding.",
        "OP| It is the moment the whole session turned for me.",
        "The list being read as a report to file rather than a problem to fix "
        "is a framing issue that no amount of wording will solve alone.",
    ),
    "Small tweaks, big difference": (
        "Relative under a day, absolute after, exact on hover -- this is the "
        "correct answer and it took our industry twenty years.",
        "OP| “11 months ago” was the specific case that finally annoyed me enough.",
        "Keeping the clicked row highlighted on return is two lines and "
        "removes a small re-orientation everybody was doing silently.",
    ),
    "Retiring the old icon set": (
        "An icon that has acquired a permanent text label is decoration. That "
        "is a useful test I had not seen written down.",
        "OP| Four of the old fifty-one had one, which made the decision easier than it felt.",
        "Fifty-one to twenty-two is a brave cut. Please do listen on the small-size reports.",
    ),
    # -- Platform Notes --
    "Incident review: what changed": (
        "A follow-up post saying which actions are done and which are still "
        "open is rarer than the postmortem itself.",
        "OP| The two open ones have dates, and if they slip that will be said here.",
        "“A setting that accepts an out-of-range number and behaves "
        "catastrophically is a trap, not an option” is going in our review "
        "checklist.",
    ),
    "Capacity planning for next quarter": (
        "Naming the assumption most likely to be wrong is what makes this a "
        "plan rather than a forecast.",
        "OP| Two of the last five months were single customers onboarding, "
        "so the trend line is doing a lot of work.",
        "Sizing the December addition so it can be halved again is a nice "
        "hedge. Most capacity decisions are one-way.",
    ),
    "Why we added rate limiting": (
        "Publishing the limits rather than keeping them secret is the right "
        "call. An invisible limit is indistinguishable from a bug.",
        "OP| And it moves the conversation to the number, which is a conversation we can have.",
        "One misconfigured client outweighing everyone else is so common that "
        "it should be the default assumption in capacity work.",
    ),
    "Notes on the database migration": (
        "Six weeks of elapsed time to avoid one long lock is the trade, and "
        "it is almost always worth it.",
        "OP| Nothing about it was interesting, which took a lot of effort.",
        "Building the test database from a production schema dump is one of "
        "those things everyone assumes they already do.",
    ),
    "Monitoring: what we watch and why": (
        "“An alert must name something a human can do at the moment it "
        "fires” is the cleanest formulation of this I have read.",
        "OP| It removed 43 alerts on its own.",
        "Keeping the removed ones as metrics with dashboards is the part "
        "people skip, and then they miss the data five minutes after a page.",
    ),
    "The postmortem process, revisited": (
        "Nineteen action items in an unclear state, out of 94, is a number "
        "most teams would rather not compute.",
        "OP| Reading all 31 documents in one afternoon is a strange experience and I recommend it.",
        "Putting uncommitted ideas in the narrative rather than the action "
        "list is a small change that keeps the list honest.",
    ),
    "Scaling the ingestion pipeline": (
        "Serialising on a counter that exists only to be displayed to a human is a wonderful bug.",
        "OP| Two seconds of lag nobody can perceive bought the entire improvement.",
        "Knowing where the next ceiling is and deliberately not fixing it yet "
        "is the discipline part.",
    ),
    "On call again already": (
        "Two actionable pages in a week, both closed in twenty minutes with a "
        "runbook, is what good looks like.",
        "OP| Three years ago that would have been a quiet day.",
        "A handover section only about things that have not happened yet is a "
        "genuinely new idea to me. Please do write it up.",
    ),
}


# --------------------------------------------------------------------
# Fallbacks for pages and posts without a written comment thread
# --------------------------------------------------------------------
#
# A single pool would repeat: the demo's busiest page carries 41 comments,
# which is four times round a ten-line pool. These compose an opening
# observation with a closing clause, so a page has 72 distinct comments
# before it repeats one -- and because every opening observation names the
# page it is on, no two pages can produce the same line.

_PAGE_COMMENT_OPENERS: tuple[str, ...] = (
    "Is “{title}” still current? The last substantive edit predates the reorganisation.",
    "“{title}” is the page I send people to, so I would like to keep it to one screen.",
    "Two of the steps on “{title}” are in the opposite order to the way we actually do it.",
    "Can “{title}” be split? It now covers three things that change at different rates.",
    "The prerequisite is missing from the top of “{title}” — everything "
    "below it assumes access you have to request first.",
    "Who owns “{title}” these days? I have a correction and nobody obvious to send it to.",
    "The example halfway down “{title}” no longer matches what the tool prints.",
    "“{title}” and the runbook it links to disagree about one step, and I "
    "cannot tell which is right.",
    "Thanks for writing “{title}” down — it saved me the hour I was about "
    "to spend guessing, and I have added a line at the end.",
    "“{title}” is quoted in a customer-facing document, so edits here are "
    "more visible than they look.",
    "Is there a reason “{title}” is not linked from the index? I found it "
    "through search, by accident.",
    "About half of “{title}” describes a process we stopped using in the "
    "spring, and the other half is still exactly right.",
)

_PAGE_COMMENT_TAILS: tuple[str, ...] = (
    "",
    " Not urgent.",
    " Flagging it here rather than in chat so it does not get lost.",
    " Happy to make the change myself if nobody objects.",
    " I may be missing context, so treat this as a question.",
    " The same applies to the sibling page, for what it is worth.",
)

_POST_COMMENT_OPENERS: tuple[str, ...] = (
    "Read “{title}” twice and got more out of it the second time.",
    "“{title}” answers something I have been asking internally for months.",
    "Would be interested in the numbers behind “{title}” if you are able to share them.",
    "Sending “{title}” to two colleagues who will disagree with it, which is "
    "meant as a compliment.",
    "The middle section of “{title}” is the part I would expand. It is doing most of the work.",
    "We reached the opposite conclusion to “{title}” and I am no longer sure we were right.",
    "“{title}” would be stronger with a worked example. The reasoning is clear and stays abstract.",
    "Bookmarked “{title}”. It is what I will point at the next time this comes up.",
)

_POST_COMMENT_TAILS: tuple[str, ...] = (
    "",
    " Thanks for writing it up.",
    " Either way, a useful read.",
    " Happy to be told I have misread it.",
    " More of these, please.",
)


def _composed(*, openers: tuple[str, ...], tails: tuple[str, ...], title: str, index: int) -> str:
    """One comment from an opener/tail pair, walking every pair before any
    repeats.

    The walk advances BOTH halves each step. Taking the tail as
    `step // len(openers)` also visits every pair, and reads terribly: ten
    consecutive comments end in the same clause. Advancing the tail by one
    extra place per lap keeps the pairing a bijection -- provided
    `len(openers) + 1` and `len(tails)` share no factor, which the two pools
    below satisfy (13/6 and 9/5) and `test_comment_fallback_exhausts_every_
    pair_before_repeating` enforces.

    The starting point is a stable hash of the title, so two pages of the
    same length do not produce the same sequence.
    """
    laps, per_lap = len(tails), len(openers)
    step = (_shape_index(title, laps * per_lap) + index) % (laps * per_lap)
    opener = openers[step % per_lap]
    tail = tails[(step + step // per_lap) % laps]
    return opener.format(title=title) + tail


def varied_page_comment(title: str, index: int) -> str:
    """A comment for a wiki page with no written thread (or beyond its end)."""
    return _composed(
        openers=_PAGE_COMMENT_OPENERS, tails=_PAGE_COMMENT_TAILS, title=title, index=index
    )


def varied_post_comment(title: str, index: int) -> str:
    """A comment for a blog post with no written thread (or beyond its end)."""
    return _composed(
        openers=_POST_COMMENT_OPENERS, tails=_POST_COMMENT_TAILS, title=title, index=index
    )


def _written_comment(written: tuple[str, ...] | None, index: int) -> tuple[str, bool] | None:
    if not written or index >= len(written):
        return None
    line = written[index]
    if line.startswith(_OP_MARK):
        return line[len(_OP_MARK) :], True
    return line, False


def page_comment(title: str, index: int) -> tuple[str, bool]:
    """The `index`-th comment on a wiki page, and whether the page's own
    AUTHOR left it.

    Mirrors `topic_reply`: written threads run in order, so a correction is
    followed by the author's answer to that correction rather than by an
    unrelated remark about a screenshot the page does not have.
    """
    hit = _written_comment(PAGE_COMMENTS.get(title), index)
    if hit is not None:
        return hit
    consumed = len(PAGE_COMMENTS.get(title) or ())
    return varied_page_comment(title, index - consumed), False


def post_comment(title: str, index: int) -> tuple[str, bool]:
    """The `index`-th comment on a blog post, and whether the post's own
    AUTHOR left it.

    The synthesizer threads the second comment as a reply to the first, so
    the written threads put the author's answer there -- the reply reads as
    a reply, by the person being replied to.
    """
    hit = _written_comment(POST_COMMENTS.get(title), index)
    if hit is not None:
        return hit
    consumed = len(POST_COMMENTS.get(title) or ())
    return varied_post_comment(title, index - consumed), False
