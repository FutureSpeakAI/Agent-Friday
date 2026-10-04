# Media: one home for everything Friday makes or helps make

> **Status:** on main since `b0e5a7ed`; the self-building index, the "kept" status and the
> complete source walk (§4.3) are on branch `fix/media-index-fresh` over main `62243240`:
> the Studio tab-width fix (§1.6), the card index and routes (§4.2), the Library with its
> grid, list and 3D layouts, the Pipeline board with the publish gate, the Calendar view and
> the Calendar workspace's Media layer (D6), the editor frame with "turn this into…" for
> posts, episodes, pages, articles, slides and read-aloud, cards in the records-3D view (D4),
> pop-out and own tab, the voice tools, a credential on every save path (P7), and the
> retirement of Draft, Content and Studio into Media (§4.9, §5.1). The owner decided on
> 2026-09-30 ("yes to all", §10).
> **Last verified:** 2026-09-30, against main at `321ec490` (merged into the branch; the content gate
> it carries owns a post card's approval, §4.5) and `feat/unified-shell` at `34e8240d`.
> **Implementation:** as the status says; §1 cites what existed before the build, line by line, and `tests/unit/test_media_index.py`, `tests/api/test_media_routes.py`, `tests/unit/test_media_card_tools.py` and `tests/unit/test_media_workspace_ui.py` are the proofs.
> **Prototypes:** [`docs/design/prototypes/media/`](../prototypes/media/index.html), six
> screens and a map in Friday's brand, one shared set of cards, keyboard-complete, no server.
> **Supersedes:** the Studio-only draft of this document (same branch, earlier commit).
> **Builds on:** [`unified-shell.md`](unified-shell.md) and
> [`docs/brand/BRAND.md`](../../brand/BRAND.md) (on `feat/unified-shell`),
> [`local-podcasts.md`](local-podcasts.md),
> [`content-pipeline-spec.md`](../implemented/content-pipeline-spec.md),
> [`action-creation-layer-spec.md`](action-creation-layer-spec.md), north star §21.14 and
> §21.12.
> **Owned elsewhere:** the podcast player and the News shows (the podcast session); the top
> bar, the chat tray and fullscreen-with-chat (the UI session); the Salon (its own spec).
> **Written:** 2026-09-30
> **Method:** STORM with a simulated expert panel (appendix A). Every claim about today's
> code carries a `file:line` citation (`ih:` is `index.html`, `svc/` is
> `src/agent_friday/services/`, `rt/` is `src/agent_friday/routes/`). Registers as in
> [`ROADMAP.md`](../ROADMAP.md): VERIFIED, INFERRED, UNKNOWN.

## 0. The owner's brief, and the answer in one page

The owner, verbatim, first on Studio: "when I opened the studio workspace with podcasts in a
full Chrome tab, it didn't expand all the way horizontally. It looks like it's kind of
trapped. And really the studio workspace is a little bit messy… how do we make an
integrated UI for a studio to support all the different types of media this thing's
creating? … it would be more appropriate to put the podcasts that are based on our
briefings and our front page and our editorials and our weekly over in the news
workspace... but I definitely want creation tools over here." Then, widening it: "it
should be able to pop out or become its own chrome tab... the media workspace. We could do a
lot with that; perhaps consolidate draft, content, and studio into media and break them out
by cards? ...We could make a much more useful set of UI's if we chase this thread."

**The answer.** One workspace, **Media**, replaces Draft, Content and Studio. Its unit is a
**card**: one per piece of work, whatever the medium (a draft, an article, an episode, an
image set, a video, a page, a chart, a document, a deck, a post, a codebase). A card shows
its type, its status (idea, draft, in review, scheduled, published), its provenance (which
model, on this PC or not, from which sources), its privacy, and where it has been published.
There are **three views of the same cards**: a Library grid, a Pipeline board by status,
and a Calendar of what went out and what is due. The board is where Draft and Content went:
they were never separate products, they were stages. A card opens into **one editor frame**
with the right tool for its medium and a "turn this into…" list: a draft into a podcast,
an article into slides, a chart into a data podcast. Any card, and the whole workspace,
**pops out** into a floating window or its own Chrome tab wearing the shell's one top bar.
It is **voice-callable**: "show my drafts", "turn this into a podcast", "publish this".

**What stays separate.** News is reading: the editions and the four routine shows live
there, on their runs. The Salon is the IDE: a codebase is a card in Media that opens in the
Salon, never edited here. Knowledge keeps the wiki. The Calendar workspace shows Media's
scheduled and published cards as one layer among the owner's events.

**Against the junk drawer.** The Library opens on **Today** (what is due, what needs you,
what you touched), with **In progress**, **Needs you** and **Published** one click away;
every card belongs to a project or to "no project", which is itself a view; search covers
titles, text, sources and transcripts; and the Pipeline's columns are the only status
vocabulary, replacing the four that exist today (§1.4).

**What the fold-in costs, honestly.** Draft loses its one-screen "prompt, text, copy" flow
and gains persistence it never had: today a draft is React state and an HTML file nobody
lists. Content loses its dense Queue tab and its top-level Accounts and Analytics tabs,
which move to settings and to the card; it gains a list of its own drafts, which today have
no view at all. Studio loses nothing it had and gains credentials and privacy on every
creation. The full ledger is in §4.9.

**The width bug** that started this is fixed and shipped (§1.6). The bar for the rest is
Linear and Superhuman (one control, one look, one place, everything from the keyboard) plus
the brand check. The product is Agent Friday™.

## 1. Grounding: what exists on 2026-09-30

### 1.1 Draft (registry id `draft`) (VERIFIED)

- Registry: `static/workspace_registry.js:56-57`, label "Draft", group work, `core:false`,
  aliases drafts, writing, writer.
- `DraftWS` (`ih:32763-33121`) is one page: mode chips from `DRAFT_MODES` (`ih:32259`:
  linkedin_post, email_reply, slack_message, tweet, freeform), a prompt box, a context box,
  Generate and Regenerate, an Edit/Preview toggle (`DraftPreview`, `ih:32233`), Copy, "New
  Tab" and Gmail buttons (`ih:33070-33084`), and a "Recent drafts" list (`ih:33085-33118`).
- **A draft is not an entity.** It is React state. History is the last twenty in memory
  (`ih:32823,32860`) and dies with the window. `POST /api/draft` (`rt/workflows.py:78`)
  spawns an in-memory task (`svc/misc_engine.py:196`) that the page polls, and the worker
  auto-saves a styled HTML copy to `~/.friday/wiki/content/draft-<ts>-<slug>.html`
  (`misc_engine.py:258-267`). That file is the only persistence; it records mode and prompt
  in the HTML and no model or provider anywhere.
- Actions: `POST /api/draft/deploy` (`rt/workflows.py:95-131`) does clipboard, and for
  `gmail_draft` only returns an acknowledgement that the page ignores (`ih:32893`): **the
  Gmail button does nothing**. "New Tab" is a blob URL (`ih:32226`), not `/w/draft`, and
  DraftWS never calls `useTabState`, so moving the window to a tab loses the text.
- Inbound: News "share to draft" (`ih:28689`, `rt/news.py:606`) writes a seed to
  `~/.friday/drafts/from_news/<id>.json` (`svc/news_engine.py:2786`) and opens
  `{workspace:'draft', seedId}`. No agent or voice tool creates a Draft-workspace draft;
  `draft_email` (`svc/agent.py:774`) goes straight to `gmail_send.request_send` and a card.

### 1.2 Content (registry id `content`) (VERIFIED)

- Registry: `static/workspace_registry.js:58-60`, label "Content", group work,
  `core:false`, aliases content studio, posts, publishing.
- `ContentWS` (`ih:38186-38290`), tabs from `CONTENT_TABS` (`ih:38177`): compose,
  calendar, queue, analytics, accounts, ideas. `useTabState('content', {tab})` at
  `ih:38198`.
- **Ideas** (`ContentIdeasTab`, `ih:34507`): a legacy kanban by stages
  `idea, drafting, review, scheduled, published` (`ih:34525`) over
  `~/.friday/content/pipeline.json` (`svc/misc_engine.py:635-654`; routes
  `rt/workflows.py:550-749`), templates, and "Saved drafts", which lists the same
  `wiki/content/*.html` folder Draft writes to. This is the only place Draft output
  resurfaces. `graduate` (`ih:38205`) turns an idea into a v2 post.
- **Compose** (`ContentComposeTab`, `ih:35633`): title and body, platform chips, per-platform
  previews (`/api/content/preview`), attach from `/api/creations` (fifty items), A/B
  variants (`ih:36090`), `ProvenanceBar` on the first asset (`ih:36211`), a schedule panel
  with best-time slots; `ship()` (`ih:35800-35880`) creates or patches the post, adapts per
  platform, then publishes or schedules. There is no "write it for me"; the model step is
  adaptation.
- **Calendar** (`ContentCalendarTab`, `ih:36294`): month and week from
  `/api/content/calendar`, drag-to-reschedule (`ih:36340,36364-36384`), conflict and
  optimal-slot ghosts, click a slot to compose there.
- **Queue** (`ContentQueueTab`, `ih:36709`): upcoming, "Held for your review" with Release
  (`ack:true`, `ih:36954`), cancel, history, and a global Pause (`ih:36736`).
- **Analytics** (`ih:37048`) and **Accounts** (`ih:37407`, OAuth, manual, test, pause,
  staging host, token expiry).
- **The v2 store**: `~/.friday/content_pipeline.db` (`svc/content_pipeline.py:53`, schema
  `:117-162`). `posts` carry title, body, status, schedule, assets, variants, source
  (`kind: compose | idea | flow | creation | chat | recurrence`), license, tags, a
  provenance hash, analytics and timestamps; `targets` carry platform, format, payload,
  status, `post_url` and `platform_post_id`. Post statuses (`:62`): DRAFT, SCHEDULED,
  PUBLISHING, PUBLISHED, PARTIAL, HELD, FAILED, CANCELLED; HELD expires to CANCELLED after
  seven days (`:72`). Thirteen platforms (`:101`). Publish log
  `~/.friday/content/publish_log.jsonl` (`:55`).
- **The publisher** (`svc/publisher.py`): a scheduler builtin every fifteen minutes
  (`:263-290`); `tick` (`:344`) runs moderation (fails closed to HELD), egress
  classification (private-data divergence goes to HELD, `_hold` `:569` notifies), prepare,
  budget, publish, confirm, publish log, provenance (`:147`), distribution (`:170`) and a ψ
  award (`:198`). **Approval cards appear only for regenerated or recurrence text**
  (`_decide_new_text` `:550` → `action_gate.authorize_external`). Post-now and ordinary
  scheduling raise no card (`:672-688`).
- **QuickPost** (`ih:37867`, host `ih:38161`, `window.fridayQuickPost`) is called from
  SendTo "Share / Post…" (`ih:11510`) and Studio's gallery (`ih:21290,21885`).
- **Gap:** nothing in the page calls `GET /api/content/posts` (`rt/content_pipeline.py:289`).
  DRAFT posts made by Compose "Save draft", QuickPost, SendTo or the agent's
  `content_create_post` (`svc/agent.py:7460-7560`) have no list anywhere; they are reachable
  only by deep link (`svc/desktop_targets.py:609`). `voice-cards`, `export` and
  `repurpose` routes have no UI callers.

### 1.3 Studio (registry id `studio`) (VERIFIED)

Registry `static/workspace_registry.js:67`, group system, `core:false`. `STUDIO_VIEWS`
(`ih:21075`): generate, music, timeline, production, gallery (default, `ih:21111`),
projects, podcasts, files. The generate, gallery and podcasts views also render the prompt
bar and the whole gallery underneath (`ih:21476`). Two image generators sit on one screen
(`GeneratePanel` `ih:18698`, `StudioPromptBar` `ih:20493`).

| Medium | Creator and route | Lands in | Credentials |
|---|---|---|---|
| Image, video | `svc/creative_engine.py` (local `:724,:947`, Higgsfield, KIE, Gemini, Veo); `/api/create/image`, `/video` (`rt/creations.py:379-409,736`) | `CREATIONS_DIR` (`~/Desktop/friday-creations`, `core/__init__.py:976`) | local paths return before provenance (`creative_engine.py:738-744`) |
| Timeline (FFmpeg) | `svc/timeline_engine.py`; `/api/create/timeline` (`rt/creations.py:534`) | video in `CREATIONS_DIR`, JSON in `~/.friday/timelines/` (`:570-580`) | signed with source hashes |
| Production pipeline, creative projects | `svc/creative_pipeline.py` (`:57-240`), `svc/creative_memory.py`; `/api/pipelines/*`, `/api/creative/projects/*` | `~/.friday/pipelines/runs`, `~/.friday/projects/` | text stages only inside the run JSON |
| Music | `svc/music_engine.py`; `/api/create/music` (`rt/creations.py:412-444`) | `CREATIONS_DIR` (`:512`); a `.md` "demo" signed as music when cloud music is unavailable (`:532-569`) | signed |
| Podcast | `svc/podcast_engine.py`; `/api/podcasts` family (`rt/podcasts.py:51-182`); tools `make_podcast`, `podcast_list`, `podcast_play`, `podcast_source` | `~/.friday/podcasts/<id>/` with `episode.json`, audio, captions, `charts/` | signed (`:806`); `origin: user | routine` and `privacy` (`:242-258`); routines `front_page, briefing, weekly, editorial` (`:52-58`) |
| Read-aloud audio | `speak_text` to `DAILY_CREATIONS_DIR/<folder>` (`svc/elevenlabs_tools.py:121,237`) | `~/.friday/creations/…` | none; the docstring claims a gallery that never lists it |
| Text, code art | `/api/create/text` (`rt/creations.py:485-531`), `/code-art` (`:644-674`); `/poem` has no UI (`:677`) | `CREATIONS_DIR` | none |
| Office (docx, pptx, xlsx) | `svc/office_engine.py` via the `office` tool (`svc/agent.py:5517`) | `~/.friday/documents/` with `_renders/` and `.made-by-friday.json` (`:82,113,335,453`) | the record; overwrite gated (`governance/action_gate.py:439-448`); every render PNG is a duplicate gallery item (`rt/creations.py:95-98`); Open and ProvenanceBar 404 (`:148,597-605`) |
| PDF | `fill_pdf_form`, `sign_pdf` (`svc/agent.py:6672-6697`) | `CREATIONS_DIR/forms/` (`svc/pdf_forms.py:32-36`), invisible to the gallery | none |
| Charts | podcast data mode only (`svc/podcast_data.py:587-620`) | inside the episode | with the episode |
| Presentation, website | `/api/create/presentation`, `/website` (`rt/creations.py:705-733`), tools only, no button | `CREATIONS_DIR` | none |
| 3D | nothing creates models; "Files 3D" is a browser (`static/studio_files3d.js`, `svc/studio_files.py`) whose roots exclude `~/.friday` (`:127-146`) | n/a | n/a |
| Sites, codebases | Sites (`futurespeak`, `svc/futurespeak.py`), CodeWS (`ih:27253`, `~/Projects`) | their own | their own |
| Daily creation | `svc/creations.py:149-164`; the button hard-codes a developer `cwd` (`ih:21170`) | both roots | signed |

The gallery reads `GET /api/creations` (`rt/creations.py:53-103`): `CREATIONS_DIR`
without recursing plus documents, records of `{name, size, modified, type, source}`, capped
at fifty (`:103`), no query. Only podcasts carry privacy. Provenance lives in
`~/.friday/provenance/<hash>.jsonld` and a hash-chained `ledger.jsonl`
(`svc/provenance.py:38-39`).

### 1.4 Four status vocabularies, three text writers, two calendars (INFERRED)

- Statuses: the legacy kanban's `idea, drafting, review, scheduled, published`; the v2
  post's eight; the v2 target's seven; and Draft's none.
- Text writers that save nothing structured: Draft (`/api/draft`), Ideas
  (`/api/content/draft`), Studio Text (`/api/create/text`). Compose adapts; it does not write.
  Compose "Save draft" is a fourth meaning of "draft".
- Calendars: Content's (`ih:36294`, with drag-to-reschedule) and the Calendar workspace
  (`CalendarWS` `ih:31246`), which has no content integration and reads only
  `/api/calendar/*` and meetings.
- Pop-outs: `/w/<id>` (`rt/core_routes.py:102`, `StandaloneShell` `ih:53788`),
  `fridayOpenWorkspaceTab` from the window's ↗ (`ih:8650`, `ih:52907`), `navigate_to
  new_tab` (`svc/agent.py:709-712`), and Draft's separate blob URL.
- "Where it has gone": per post, `targets.post_url` and the publish log; per asset,
  `/api/provenance/by-file`; cloud egress, `GET /api/privacy/left-the-machine`
  (`rt/research.py:107`) whose entries carry no item id (`svc/egress_gate.py:552`); the
  activity ledger (`rt/activity.py:26`) holds model, provider, workspace and task id per
  model call, which nothing links to a creation today.
- SendTo (`ih:11450-11560`): clipboard, trust graph, calendar notes, briefing, gmail draft
  (a dead end: `~/.friday/flow-queue/`, `misc_engine.py:372`, nothing reads it), Share/Post
  (QuickPost), podcast. Used in seven workspaces; not in Draft, Content or Studio.

### 1.5 Retiring a workspace: the precedent (VERIFIED)

`wiki` became `knowledge`: the registry entry was removed; `WS_ALIASES` (`ih:7560`) maps the
old id to `{workspace, view}` and `dockArrangement` (`ih:7443`) migrates saved dock order
through it; `_WS_TAB_ALIASES` (`rt/core_routes.py:94`) redirects old `/w/` URLs; the old
words moved onto the new entry's aliases, which `tests/unit/test_workspace_aliases.py`
requires to be unique. The server's `resolve()` reads only registry aliases.

### 1.6 Shipped: the Studio tab-width fix (VERIFIED, `83f5595f`)

`PodcastsView` capped its root at 900px with no centering (`ih:11410`; mirror
`ui_parts/app.html:1818`); in a 1920px tab the view measured 932px, left-aligned. The cap
is gone; the intro paragraph keeps an 80ch measure. `tests/unit/test_studio_tab_width.py`
was red before and is green after; the podcast brand tests, which require the block to be
byte-identical in both files, pass. Rendered after the fix: 1884px of 1920. The gallery's
680px centered column (`ih:21543-21545`) is left for Media to replace.

## 2. Standing rules

- **The shell owns the frame.** Media fills `.ws-tab-body` and never sets its own width,
  top offset or `100vh`; a readable measure goes on text; the root is `.ws-fill`.
- **One top bar, one tray, one fullscreen, one pop-out mechanism** (`/w/media?card=<id>`
  with `useTabState`; the window's ↗; `navigate_to new_tab`). Draft's blob tab goes.
- **Brand.** `--fr-*` tokens; Inter, Orbitron display-only with a sans-serif fallback,
  JetBrains Mono for data; the `--fr-text-*` scale; amber only where something needs the
  owner; status is always a word; `.btn.active` with `aria-pressed`; `.btn-magenta` only to
  refuse, stop or remove; `fridayToast`; everything in Ctrl+K; Agent Friday™ on brand
  surfaces, never her given name in its place; "Made with Agent Friday™" on anything
  published.
- **Governance.** Creating and editing are internal and reversible. Publish, send, delete
  and any cloud model that takes text off this PC go through the one gate and a card. A
  spoken "yes, but" becomes "change it".
- **Privacy.** Nothing leaves without a card and an egress line, and the card shows the
  line. The URL of a pop-out carries an id and a view, never a title or text.
- **The repository is public.** Synthetic titles and names throughout.
- **Deterministic rules live in code**: the brand guard, the product-name test, the
  tab-width test, the alias-uniqueness test, and the new status-vocabulary test.

## 3. STORM: questioning it from eight perspectives

### 3.1 The owner, a journalist who makes things all day

"A story starts as a line, becomes notes, a draft, a piece, then a podcast, a deck and three
posts. Today that is five workspaces and I lose the thread between them." The questioning
made **relationships** a first-class field (made from, turned into, version of) and made
"turn this into…" a verb on the card rather than a tool to find. It also set the default
view to Today, not Everything.

### 3.2 A product designer holding Linear and Superhuman

"One status vocabulary. One card. One way to open a thing in a tab. One approval card."
This perspective collapsed four status sets into five words, made the board the only place
status is changed by hand, insisted that the three views be the same cards with the same
details rail, and wrote the keyboard map before the mouse map. Linear's rule that a view is
a saved filter gave the default views and projects their shape.

### 3.3 The privacy and governance engineer

"The publisher raises a card only for regenerated text. Post-now raises none. A board where
dragging to Published is one gesture needs the gate on that gesture." The questioning put
the card on **every** move into Published, kept HELD visible as "In review, held for you",
and gave every card a privacy field backed by the publish log and the egress ledger rather
than by inference. It also fixed the pop-out URL rule.

### 3.4 The archivist

"A draft today records neither the model nor the sources. The activity ledger knows which
model ran under which task id; nothing joins them." The questioning added the join: a card
stores the task ids of the calls that made it, and the provenance panel reads the ledger.
Unsigned stays a visible word, never upgraded by guesswork.

### 3.5 The maintainer of the content publisher

"Do not rewrite the publisher. The v2 store already has statuses, targets, a publish log
and a calendar feed." The questioning made the v2 post the **first citizen** of the card
record rather than inventing a new store: a card of kind `post` *is* a v2 post; its
targets are its "published at". The legacy kanban migrates once into cards and its file
is retired.

### 3.6 The podcast session

"The routine shows stay in News on their runs; the player is mine." The questioning kept
that line: a user episode is a card of kind `episode`; a routine episode is listed as a
card too, so the library is complete and nothing is silently missing, but it is marked
`origin: routine`, its source names the News run that made it, and opening it goes to the
show's tab in News, never into Media's editor. The player stays News's.

### 3.7 The UI session, building the shell

"No second header, no second tray, no viewport maths." The questioning produced the
`popout.html` prototype with its live measure and the rule that Media's own head row holds
its views while the top bar's context slot holds only the name.

### 3.8 The voice-first user

"'Show my drafts' should change the view, not open a form." The questioning made the
default views and filters addressable by one tool (`media_show`), kept "turn this into…"
and "publish" as the existing tools behind a card, and made the spoken result of a publish
a card read aloud.

### 3.9 Synthesis: what the questioning changed

- A separate "Create" segment is gone; **+ New** makes a card, and the medium's tool
  opens inside the editor frame. The prompt-bar idea survives as "Ask Friday" on every card.
- The Library was going to be the only view; the board and the calendar are first-class
  because they are what Draft and Content were for.
- HELD was going to be its own column; it is "In review" with a "held for you" badge, so
  the vocabulary stays at five.
- Accounts, analytics and best-times were going to stay in Media; they move to settings and
  to the card's "after it went out" panel, because they are not pieces of work.
- The v2 content store was going to be read-only; it becomes the post card's store.

## 4. Design

### 4.1 The card

One card per piece of work. On its face: a thumbnail or a type word, the title, the type
glyph and word, the date that matters for its status, the status pill, the privacy pill,
"Unsigned" when it is, and "to …" (targets) or "at …" (where it is). Prototype:
`library.html`.

### 4.2 The record

`~/.friday/media/index.sqlite`, tables `cards`, `relations`, `fts`. A card of kind `post`
is backed by the v2 `posts` row and reads status and targets from it; other kinds are backed
by the file or record in §1.3 and the index carries the rest.

| Field | Meaning | Source today |
|---|---|---|
| `id` | stable; the content hash where one exists | provenance hash, v2 post id, else sha256 of the file |
| `kind` | `draft article episode imageset video page chart doc deck post code music audio model3d` | the creator; a Draft becomes `draft` with its channel (`mode`) kept |
| `title`, `body_ref` | a human title; where the text or file is | sidecar, episode, `.made-by-friday.json`, v2 post, else filename |
| `status` | `idea draft review scheduled published`, with badges `held`, `failed`, `partial` | mapping in §4.3 |
| `when` | the date that matters: due, scheduled, or published | v2 schedule/published_at; file times |
| `project` | a project or none | `~/.friday/projects/`, v2 tags |
| `maker` | model and provider, and whether on this PC; the task ids that made it | the activity ledger, sidecars, episode |
| `sources` | what it was made from | sidecar prompt and references, episode sources, timeline JSON, v2 `source`, the News seed |
| `credentials` | signed hash or `unsigned` | `~/.friday/provenance` |
| `privacy` | `private`, `shared` (sent to someone), `published` (where, with the lines) | v2 targets `post_url`, publish log, mail receipts, egress ledger keyed by card id (new key) |
| `text` | searchable words | body, captions, md, office text |

`relations(from, to, how)`: `made_from`, `turned_into`, `version_of`, `rendered_from`.
The indexer walks only Friday's output roots and the v2 store, on start, on a creation
event and on a timer. `GET /api/media?q=&kind=&status=&project=&privacy=&since=&cursor=`,
`GET /api/media/<id>`, `PATCH /api/media/<id>` (status, project, title), `POST
/api/media/<id>/turn-into`, `POST /api/media/<id>/publish` (raises the card).

### 4.3 One status vocabulary

| Media | Legacy kanban | v2 post | Draft |
|---|---|---|---|
| Idea | idea | — | — |
| Draft | drafting | DRAFT | the only state |
| In review (badge: held for you) | review | HELD, and a card awaiting approval | — |
| Scheduled | scheduled | SCHEDULED, PUBLISHING | — |
| Published (badges: partial, failed) | published | PUBLISHED, PARTIAL, FAILED | — |
| Kept (made and kept on this PC; not a lane) | — | — | every creation, document, ready episode, render or daily file without a publication record |

CANCELLED cards return to Draft with a note. The mapping is a table in code with a test.
"Kept" is the sixth word: a finished thing that stays on this PC. It is never shown as
published; only a provenance manifest that records where it went (or the content store's
receipt) moves a card into Published, so the Published view is honest. The Library's rail
has a "Kept here" view beside Published; the board's five lanes stay five.

The index builds itself. It is built in the background at server start, checked for
freshness on every list (a cheap signature: each source root's and its first-level
folders' mtimes, no file read), rebuilt when a source changed, and re-checked on a slow
periodic pass. While it builds, the Library says "Indexing your library… N so far" and asks
again until it is done; it never shows a silent empty grid over a full creations folder.
Every source is walked: the creations folder, the office documents, every podcast episode
(user and routine), Draft copies, the legacy kanban, v2 posts, Media's own cards, the daily
creations folder, timelines, pipeline runs, creative projects and ComfyUI's output folder.
A file whose type the index has no word for is a generic file card, never a gap.
`tests/api/test_media_index_fresh.py` is the proof.

**Previews and details** (`services/media_previews.py`). Every card gets a real preview,
made locally and lazily by one low-priority worker (one job at a time, a pause between
jobs, waiting while the machine is short of memory), cached under the home, never a
placeholder: a thumbnail, a 2×2 mosaic for a set, a deck's first slide (the office tool,
else a rendered text card of its words), a page's first viewport from a headless browser
that is offline, refuses every request but the file and is closed after the batch, a
video's poster plus an eight-frame strip for hover scrub, an audio file's waveform; a type
with no picture shows its icon and size. The details ride on the card and in the side
panel: a real title, type, size, dimensions or duration or pages, created and modified,
the model or tool, the prompt, the sources, privacy and where it went, the project. A
quick look (Space, or a click on the picture) walks the list with the arrows, plays video
and audio inline, frames a page in a sandbox, and opens the file in its app or its folder.
`tests/unit/test_media_previews.py` is the proof.

**Search inside things** (`cards_fts`, `services/media_transcripts.py`). One local FTS5
index over the title, the card's text, what the preview pass read out of slides, pages
and documents, the transcript, the prompt, the sources, the maker and the project. Every
word is a prefix term; a quoted phrase is kept whole; the hit shows the line with the
words marked. Audio and video get a local transcript (faster-whisper base.en on the CPU,
int8, from the local cache only, one file at a time after the preview pass, memory-aware,
cached), so "find the video where I said X" lands on the card and the quick look starts
the player where the words were said. Nothing is sent anywhere.
`tests/unit/test_media_search.py` is the proof.

**Ask Friday** (`services/media_card_tools.py`). "Find the deck about Covista", "show me
September's videos", "play the last podcast about AI policy": media_show and media_cards
take a time in the owner's words as a window on the date that matters; media_play finds the
newest audio or video for the words and opens the quick look where they were said. One
line per item.

**Organize.** Favourites and tags are overrides, set by hand on a card or on many at once
(`POST /api/media/bulk`: move to a project, tag, favourite). A smart collection is a saved
filter with a name ("this week's podcasts", "decks for Harbour"), kept in
`media/collections.json` and evaluated when opened, so it is always current. The Library's
rail shows Favourites, the collections and the tags with counts; the grid groups by project,
date or type. `tests/api/test_media_organize.py` is the proof.

**Clean-up help** (`services/media_tidy.py`). Friday spots near-duplicate renders (the
preview pass's difference hash for pictures and video posters, Hamming distance at most 6;
word shingles with Jaccard at least 0.9 for documents) and stale drafts (Media's own drafts
and ideas untouched for thirty days with little in them), and OFFERS a tidy-up as one
batched card through the governed-action gate, the same gate publishing uses. Nothing moves
before the owner approves; in a group the favourite, else the larger file, else the newer
one is kept. What moves goes to `<home>/media/trash/<entry>/` with a manifest, the card's
Delete goes the same way, Restore puts an entry back, and Friday never empties the trash:
there is no hard delete anywhere in Media. `tests/api/test_media_tidy.py` is the proof.

**Image → video (MEDIA-I2V).** "Turn this into a video" on a picture runs locally: Wan 2.2
TI2V 5B through ComfyUI, under the arbiter's lease inside `local_video.generate`, with the
picture staged into ComfyUI's input folder and wired to `WanImageToVideo.start_image`. The
card is made at once in Draft with "working"; the clip, its credential and the kept status
land when the GPU is done; a refusal or failure is a "failed" badge with the message on the
card and a notice to the owner. `GET /api/media` carries `turns`, what this PC can turn a
card into and why not, and the editor's menu offers only what works, naming what it cannot
("Not a video here: …"), never a dead button. `tests/unit/test_media_i2v.py` would fail the
moment the menu offered a conversion with no working backend.

**Turn any media into any other** (`services/media_convert.py`). The matrix (in the program
ledger) is filled local-first with what is installed: audio, music and video → a transcript
(timestamped text) and captions (.srt + .vtt) by the local recogniser; audio and music → a
waveform video with the captions burned in (ffmpeg); video → its sound track and a still;
a deck → narration (the local voice reads each slide) and a narrated video (the office tool
renders each page, else a text card; each slide held while it is read); an image → the
words it carries (local OCR, labelled as not a description); a document or a deck feeds the
text-made kinds (article, read aloud, slides) with its own extracted words. Music that is
not Friday's own is never transcribed: lyrics are somebody's work. Every conversion is a
Media-owned card made at once in Draft with "working", an orb with real steps, then the
file, its credential and the kept status, or a "failed" badge with the message and a notice.
`turn_capabilities()` returns the map per menu group; the menu and the voice tool read the
same map, so no item is offered without a working backend. Models not installed (Demucs
for stems, a local vision model for descriptions) are listed in the ledger, not downloaded.
`tests/unit/test_media_convert.py` is the proof.

### 4.4 Three views of the same cards

**Library** (`library.html`): a rail of default views (Today, In progress, Needs you,
Published, Everything), projects, types and privacy; search; grid or list; a details panel
with the same actions as the editor. Today = due today, needs you, or touched today.

**Pipeline** (`board.html`): five columns, the status vocabulary. Drag or `]` advances.
Into Scheduled asks when; into Published always raises the approval card. Filters by
project and type. This replaces the Ideas kanban and the Queue tab; "held for you" cards
sit in In review with a Release-style approve on their card.

**Calendar** (`calendar.html`): only cards with a time: published, scheduled, and in review
with a due date. Two weeks or a month; drag to reschedule, which asks again before it goes.
It is Content's calendar (`ih:36294`) promoted, keeping its feed and its drag code. The
Calendar workspace gets the same feed as an overlay (D6).

### 4.5 The editor frame and "turn this into…"

One frame (`card.html`): title, the status strip, a stage per medium (text editable in
place; audio with transport, chapters, transcript; image set; deck pages; post per
platform; chart with data; code as a tree that opens the Salon), "Ask Friday" under the
stage answering with a diff, and three rails: **Turn this into…**, **Provenance**,
**Privacy and where it is**, then **Actions** (Send to…, Export, Schedule…, Publish…,
Delete). A turn-into makes a new card, status Draft, related `made_from`, using the tool
that exists: `make_podcast` (data mode for charts and sheets), `office` for slides,
`content_repurpose` for posts, `create_website` for a page, `speak_text` for read-aloud.

### 4.6 Pop-out

Any card: `/w/media?card=<id>`; the whole workspace: `/w/media`. The shell renders its top
bar, tray and fullscreen as everywhere. On the desktop the card is an `FWin` window whose ↗
moves it to the tab through `fridayOpenWorkspaceTab`. The URL carries only ids and a view.
Prototype: `popout.html`.

### 4.7 Voice

`media_show(view | status | kind | project | query)` changes the view ("show my drafts",
"what's published this week"). `turn_into(card, kind)` and `publish(card)` sit behind the
same tools as the buttons; publish is a card, read aloud; "yes, but" becomes "change it".
`navigate_to` gains kind `card`.

### 4.8 Boundaries

- **News** is reading. Editions and the four routine shows stay there, on their runs.
  "Share to draft" makes a card of kind `draft` with the story as a source; the seed file
  becomes that card's source record. A routine episode is listed as a card (origin
  `routine`, source "News · show · run") so the library is complete; opening it goes to
  the show's tab in News, and its player stays there.
- **The Salon** is the IDE. A repo the Salon makes is a card of kind `code` with its status
  and last run; Open goes to the Salon; publish receipts from the Salon write onto the card.
- **Knowledge** keeps the wiki; a card can be sent there.
- **Calendar** shows Media's timed cards as a layer, read from the same feed.
- **Sites** stays until the Salon's publish lands; a site is a card of kind `page` that opens
  there.

### 4.9 What folds in, and what it keeps, gains and loses

| Workspace | Keeps | Gains | Loses or moves |
|---|---|---|---|
| Draft | writing in the owner's voice per channel; the five modes as the card's channel; Copy and a mail draft | persistence, status, model and sources (task ids from the activity ledger, the News seed), versions, Today and In progress, turn-into, a real tab | the one-screen prompt-to-copy flow (now + New → a draft card with the same box); the blob "New Tab"; the Gmail button that did nothing |
| Content | compose per platform, previews, variants, hold for review, release with ack, schedule, publish, recurrence, calendar with drag | its own drafts listed at last; posts beside what they were made from; one approval card on every publish, not only regenerated text; the calendar as a top-level view | the Queue tab (becomes the board's In review and Scheduled columns plus a Pause control in the head row); Accounts to Settings → Accounts; Analytics to the card's "after it went out" panel and a Media Insights pane; the legacy kanban and `pipeline.json` (one-time migration) |
| Studio | every creation tool; podcasts from any sources; Files 3D | credentials and privacy on every card; search across everything; turn-into | the Gallery and the podcasts list (cards); the 3D browser becomes a layout of the Library plus "Browse this PC"; the production pipeline becomes a project template; `/poem`, the `.md` music demo and the developer `cwd` go |

### 4.10 Things that are not pieces of work

Connected accounts, tokens, staging host, pause-all and voice cards go to **Settings →
Accounts**. Analytics summaries, insights and best times go to a **Media → Insights** pane
reached from the head row, and per-post metrics sit on the card. Templates become **+ New
from template**.

### 4.11 Privacy and credentials

Every card shows Signed or Unsigned and Private, Shared or Published, as words. The
egress ledger gains a card id so "left this PC" is a fact, not a guess. Signing becomes a
property of saving; unsigned paths (§1.3) go through `creative_store`.

### 4.12 Against the junk drawer

Default views, not Everything. Every card in a project or in "No project", which is a view
the owner is nudged to empty. Search over text and transcripts. The board's columns as the
only statuses. A card that is published and untouched for ninety days drops out of Today
and In progress on its own and stays findable.

## 5. UI, using existing elements first

| Today | Becomes |
|---|---|
| `DraftWS` prompt box, modes, preview | the draft card's stage, with "Ask Friday" |
| `ContentComposeTab` | the post card's stage (platform chips, previews, variants, schedule panel) |
| `ContentCalendarTab` and `/api/content/calendar` | the Calendar view and the Calendar workspace overlay |
| `ContentQueueTab` | the board's In review and Scheduled columns; Pause in the head row |
| `ContentIdeasTab` kanban | the board; `pipeline.json` migrated once |
| `ContentAccountsTab`, `ContentAnalyticsTab` | Settings → Accounts; Media → Insights |
| `QuickPostModal` | unchanged; it creates a post card |
| Studio gallery, PodcastsView list, Files 3D | the Library (grid, list, 3D layout) |
| `GeneratePanel`, `StudioPromptBar`, Music, Timeline | tools inside the editor frame for their kinds |
| creation detail view, `ProvenanceBar` | the editor frame's Provenance rail |
| `SendTo` | present on every card |
| registry entries `draft`, `content`, `studio` | one entry `media` (group work, `core:true`), their labels and aliases moved onto it; `WS_ALIASES`, `_WS_TAB_ALIASES` and `fridayDeclareNav('media')` with views `library board calendar card`; old views kept as aliases for a release |

Keys: `/` search · `N` new · `← →` move · `Enter` open · `T` turn into · `S` send to ·
`O` own tab · `]` advance · `Esc` close · `Ctrl+Enter` ask Friday · `Ctrl+Shift+F`
fullscreen with chat. Every one in Ctrl+K.

## 6. Voice-first

Added: `media_show`. Reused: `make_podcast`, `content_create_post`,
`content_schedule_post`, `content_repurpose`, `office`, `create_website`, `speak_text`,
`navigate_to` (new kind `card`), the approval card for publish. Phrases: "show my drafts",
"what needs me", "turn the ferry story into a podcast", "schedule Monday's post for nine",
"publish this" (a card, spoken). Answers follow the voice tool contract.

## 7. Verification plan

- **Fail-first tests per piece.** The status mapping table (every legacy and v2 state maps
  to one of five, with its badge). The indexer on a fixture tree with one item per root
  and one v2 post: one card each with kind, status, maker and privacy, and a render is a
  relation not a card. The API: cursoring, every filter narrows, `q` hits a transcript.
  The registry: `media` present, the three old ids absent, aliases unique, old `/w/` URLs
  redirect. The board: a move into Published without an approval id is refused by the
  server. The UI: `.st-root` has no width and no `100vh`; segments use `.btn.active` with
  `aria-pressed`; product-name and brand guards pass; the tab-width test keeps passing.
- **Screenshots, actually looked at.** `/w/media` and `/w/media?card=` at 1920, 1440 and
  390, in a window and in a tab, tray closed, docked and fullscreen-with-chat; each still
  described in one sentence; the grid's column count per width.
- **The Linear and Superhuman checklist** on the stills: one segment control, one pill
  style, one button style, one empty state, one toast, one approval card; everything in
  Ctrl+K; every action by keyboard.
- **Behaviour diff against main** for one card of every kind: open, own tab, send to, turn
  into, schedule, publish (card raised), delete (card raised).
- **Migration proof:** `pipeline.json` and `wiki/content/*.html` from a fixture home become
  cards with the right statuses; nothing is lost; the originals are kept until the owner
  deletes them.

## 8. Phased build plan (after the owner decides)

| Piece | What | Verified by |
|---|---|---|
| P0 (done) | the Studio tab-width fix, `83f5595f` | the test, two stills |
| P1 | the card index and API over existing stores; the status mapping; the activity-ledger join; the egress ledger's card id | fixture tests; no UI change |
| P2 | the `media` registry entry, aliases and redirects; the Library view with the details panel; the three old workspaces still present behind it | registry tests; stills |
| P3 | the Pipeline board with the publish gate on every move; the one-time migration of the legacy kanban | the refused-move test; migration proof |
| P4 | the Calendar view from Content's calendar; the Calendar workspace overlay | drag-to-reschedule still asks |
| P5 | the editor frame for text and posts (Draft and Compose re-housed); "turn this into…" for the text kinds | behaviour diff |
| P6 | the editor frame for episodes, images, video, decks, charts, code; pop-out; `media_show` | stills in a tab and a window |
| P7 | retire `draft`, `content`, `studio`; Accounts to Settings; Insights pane; credentials on every save path | alias tests; brand guard |

## 9. Costs and failure modes

- **Scope.** This is three workspaces and two stores. P1 to P3 give most of the value; the
  rest can wait. UNKNOWN: the size of the legacy kanban and HTML drafts on the owner's
  machine; measure in P3.
- **The publisher's gates** must stay exactly as they are; the board only adds a card in
  front of them. A refactor of `svc/publisher.py` is out of scope.
- **Two meanings of "draft"** survive for a release (a draft card; a v2 DRAFT post is a
  post card in Draft status). The mapping table is the truth.
- **The index drifts** when a tool writes a file without an event; the timer and Open
  always reading the file cover it.
- **A wrong "turn this into…"** makes a bad card, which is a draft and can be deleted; it
  never publishes.

## 10. Decisions, taken by the owner on 2026-09-30

The owner read the summary and the prototypes and said "yes to all". Each decision below
records the option taken.

- **D1.** Draft, Content and Studio all fold into Media. Decided: all three.
- **D2.** Five statuses with badges; "held for you" is a badge on In review, not a column.
- **D3.** Accounts and analytics leave Media: accounts to Settings → Accounts, analytics to
  a Media → Insights pane and the card's "after it went out" panel.
- **D4.** Files 3D becomes a layout of the Library plus "Browse this PC", not its own entry.
  *Amended:* Media keeps its 3D layout for what Friday makes. The 3D file browser and
  "Browse this PC" moved to the Library workspace (`library.md`), which holds the documents the
  owner added for Friday to read; Media is what Friday makes.
- **D5.** Cloud tools are shown as chips that say "asks first".
- **D6.** Media's timed cards appear as an overlay in the Calendar workspace.
- **D7.** Media is `core:true` for fresh installs, since it replaces three workspaces.

Engineering decisions (the index shape, the API, the fluid grid, the single open-in-tab
mechanism, signing on save, the status mapping table) are made here and owned by the
build.

## 11. What would falsify this

- The owner keeps using Draft's one-screen flow in preference to a draft card.
- The board's five statuses do not fit a real post's life (a partial publish, a retry).
- "Today" is wrong more often than it is right for the owner's day.
- Two truths for one episode between Media search and News.

## 12. Acceptance

The bar is the best shipped work tracker and media library in each area (Linear's one
control and saved views; Superhuman's keyboard; a photo library's search and rails; a
DAW's one transport; a publishing tool's calendar) plus the brand check. A change that puts
a width on the workspace root, a status hue on a hover, a sixth status, or a second way to
do the same thing fails.

## Appendix A. The expert panel (STORM)

The owner (§3.1); a product designer (§3.2); a privacy and governance engineer (§3.3); an
archivist (§3.4); the content publisher's maintainer (§3.5); the podcast session (§3.6);
the UI session (§3.7); a voice-first user (§3.8).

## Appendix B. North-star rows

| Row | Requirement | After this spec |
|---|---|---|
| NS-21.14-1 | prompt and reference management, generation history, provider and model disclosure, cost | the card's provenance rail; the Library is the history |
| NS-21.14-2 | content credentials, editable timelines, asset relationships, review and approval, export | credentials on every save; relations; the board's gate; Export |
| NS-21.12-1/2 | documents: creation, editing, preview, versions, provenance, overwrite approvals | deck and document cards; `version_of`; the office gate stays |
| NS-6.4-4 | never silently turn an internal file into a shared artifact | Publish always cards; privacy shows the lines |
| NS-18.4-2 | approval cards for publishing | the board and the editor raise one on every publish |

## Appendix C. Prototype index

Open any file in a browser; no server needed. The same twenty cards feed every screen
(`media.js`).

| Screen | File | Shows |
|---|---|---|
| Map | `index.html` | the six screens and the brand line |
| Library | `library.html` | default views, projects, types, privacy, search, grid and list, the details panel, keys, a voice hook |
| Pipeline | `board.html` | five columns, drag to advance, the approval card on Published, filters |
| Calendar | `calendar.html` | two weeks of timed cards, a peek panel, reschedule |
| Card | `card.html` | the editor frame for eight kinds, the status strip, turn-into, provenance, privacy, actions, Ask Friday |
| Own tab | `popout.html` | a card or the workspace in its own tab with the shell's bar, tray and fullscreen, and a live measure |
| Boundaries | `boundaries.html` | News, Media and the Salon side by side; what the three workspaces keep, gain and lose |
