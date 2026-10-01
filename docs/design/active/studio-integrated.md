# Studio, integrated: one library, one door, one frame for everything Friday makes

> **Status:** active (spec plus clickable prototypes; nothing in this document is built
> except the width fix in §1.7, commit `83f5595f` on branch `docs/studio-integrated`).
> **Last verified:** 2026-09-30, against main at `e27ba160`.
> **Implementation:** none of the new surfaces. §1 cites what exists today, line by line.
> **Prototypes:** [`docs/design/prototypes/studio/`](../prototypes/studio/index.html), six
> screens and a map in Friday's brand, keyboard-complete, no server.
> **Builds on:** [`unified-shell.md`](unified-shell.md) and
> [`docs/brand/BRAND.md`](../../brand/BRAND.md) (both on `feat/unified-shell`, not yet on
> main), [`local-podcasts.md`](local-podcasts.md),
> [`content-pipeline-spec.md`](../implemented/content-pipeline-spec.md),
> [`action-creation-layer-spec.md`](action-creation-layer-spec.md), and the north star
> §21.14 (NS-21.14-1, NS-21.14-2) and §21.12 (NS-21.12-1, NS-21.12-2).
> **Owned elsewhere, by agreement:** the podcast player and the move of the News shows into
> News (the podcast session); the top bar, the chat tray and fullscreen-with-chat (the UI
> session). This document states the boundary on Studio's side and builds nothing on theirs.
> **Written:** 2026-09-30
> **Method:** STORM with a simulated expert panel (appendix A). Every claim about today's
> code carries a `file:line` citation (`ih:` is `index.html`, `svc/` is
> `src/agent_friday/services/`, `rt/` is `src/agent_friday/routes/`). Registers as in
> [`ROADMAP.md`](../ROADMAP.md): VERIFIED (read this pass), INFERRED (follows from verified
> facts), UNKNOWN (the check that would settle it is named).

## 0. The owner's brief, and the answer in one page

The owner, verbatim: "when I opened the studio workspace with podcasts in a full Chrome tab,
it didn't expand all the way horizontally. It looks like it's kind of trapped. And really the
studio workspace is a little bit messy, so I was thinking maybe we could find a way to not
just make podcasts a separate thing. I don't know, how do we make an integrated UI for a
studio to support all the different types of media this thing's creating? Oh, and I guess,
you know, it would be more appropriate to put the podcasts that are based on our briefings
and our front page and our editorials and our weekly over in the news workspace... but I
definitely want creation tools over here."

**The trapped tab** was one line: the Podcasts view capped its own root at 900px with no
centering (`ih:11410`, mirror `ui_parts/app.html:1818`), so in a 1920px tab it hugged the
left edge and left a thousand pixels empty. The shell never caps a workspace; the cap was
Studio's. It is removed and tested (§1.7). The Gallery has the same disease at 680px
(`ih:21543`); the Library below replaces it rather than patching it.

**The mess** is structural, not cosmetic. Studio today is eight views that are really eight
products stacked on one screen: two image generators on the same page, a gallery that is a
folder listing capped at fifty files, a separate podcasts list, a 3D file browser that cannot
see Friday's own output folders, and three panels (Music, Timeline, Production) that each
save to the same folder with nothing tying their results together. Nine kinds of thing
Friday makes have no home on screen at all (§1.4). Only podcasts carry a privacy flag; four
creation paths write no content credentials.

**The answer** is one Studio with three segments and one spine:

1. **Library.** One index of everything Friday has made on this computer, every medium,
   with search, filters by type, source and privacy, grid, list and the existing 3D browser
   as a third view of the same items. Every item shows its credentials and its privacy.
2. **Create.** One door. The owner says what they want; the right tool opens underneath
   already filled in, with the model, the cost and what would leave this computer disclosed
   before it runs. The existing panels are re-housed, not rewritten.
3. **Viewer.** One frame for every medium. The stage changes (an image, a transport bar
   and transcript, a deck's pages, a chart with its data, a 3D orbit, a sandboxed page); the
   rails do not: credentials, privacy, relationships, the same seven actions.

**The spine** is a Library index (§4.2): one record per thing, whoever made it and however
it was made, with its kind, source, credentials, privacy and relationships. Everything else
reads from it. Podcasts stop being "a separate thing": a podcast is an item of kind
`podcast` in the Library, a `Podcast` tool behind the Create door, and a "Make a podcast of
it" verb on every item that has words or numbers.

**The boundary with News** (§4.6): the four News shows (Front Page, The Briefing, The Week,
The Editorial) are `origin: routine` episodes and belong to their News run and to a shelf in
News. Anything made from any sources is `origin: user` and belongs to Studio. One mini
player plays both. Studio search can find a News show; the result is a link into News, never
a copy. The podcast session owns the News side and the player; this spec only promises not
to duplicate them.

**The bar:** Linear-level consistency (one control, one look, one place, everything reachable
from the keyboard) plus the brand check in BRAND.md. The product is Agent Friday™.

## 1. Grounding: what exists on 2026-09-30

### 1.1 Studio's views today (VERIFIED)

`STUDIO_VIEWS` (`ih:21075`): `generate`, `music`, `timeline`, `production`, `gallery`
(the default, `ih:21111`), `projects`, `podcasts` (aliases podcast, episodes, audio
overview), `files` ("Files 3D"; aliases files, file browser, folders). The render is one
switch at `ih:21461-21476`. The `generate`, `gallery` and `podcasts` views also render the
prompt bar, the Creation Log and the whole gallery underneath, because the condition is
"view is not projects/files/music/timeline/production" (`ih:21476`).

`STUDIO_TYPES` (`ih:20434`): `image`, `video`, `music`, `text`, `code-art`, each posting to
`/api/create/<id>`.

### 1.2 Storage roots and indexes (VERIFIED)

| Root | Path | Who writes it | Index |
|---|---|---|---|
| `CREATIONS_DIR` | `~/Desktop/friday-creations`, else `~/.friday/friday-creations` (`core/__init__.py:976`) | creative, music, timeline, showcase, text, code art, PDF forms (`forms/`) | `creations-manifest.jsonl`, append-only, written by only three paths (`svc/creative_store.py:227`, `local_image.py:1132`, `local_video.py:726`); sidecars in `~/.friday/creations_meta/` (`creative_engine.py:56,437`) |
| `DAILY_CREATIONS_DIR` | `~/.friday/creations` (`core/__init__.py:981`) | daily creation JSON; ElevenLabs audio and Higgsfield `save_output` in subfolders (`svc/elevenlabs_tools.py:121-130`, `svc/media_tools.py:309-316`) | none |
| Podcasts | `~/.friday/podcasts/<id>/` with `episode.json`, audio, `captions.vtt`, `charts/` (`svc/podcast_engine.py:4-7,105`) | podcast engine | `episode.json` per episode; `GET /api/podcasts?routine=&run_id=` (`rt/podcasts.py:51-182`) |
| Documents | `~/.friday/documents/` with `_renders/` and `.made-by-friday.json` (`svc/office_engine.py:82,113,335,453`) | office tool | the record file |
| Timelines | `~/.friday/timelines/` (`svc/timeline_engine.py:570-580`); output video in `CREATIONS_DIR` (`:434`) | timeline engine | none |
| Pipelines | `~/.friday/pipelines/runs` (`svc/creative_pipeline.py:42-44`) | production pipeline | run JSON; text stages (script, storyboard) exist only inside it |
| Creative projects | `~/.friday/projects/` (`svc/creative_memory.py`, `rt/projects.py`) | series bible | per project |
| Sites | `~/.friday/futurespeak/*`, `~/.friday/futurespeak_projects.json`, clones in `~/Projects` (`svc/futurespeak.py:41-68`) | Sites workspace | its own |
| Posts | `~/.friday/content_pipeline.db` (`svc/content_pipeline.py:53,118-154`), plus the legacy `~/.friday/content/pipeline.json` and `~/.friday/wiki/content/*.html` (`svc/misc_engine.py:122,633-635`) | Composer, Ideas | sqlite; the legacy store has none |
| Provenance | `~/.friday/provenance/<hash>.jsonld` and a hash-chained `ledger.jsonl` (`svc/provenance.py:38-39`) | whoever signs | the ledger |

There is no sqlite store for creations. No creation carries a privacy or sensitivity tag;
podcasts are the one exception (`svc/podcast_engine.py:249`).

### 1.3 The listing the gallery reads (VERIFIED)

`GET /api/creations` (`rt/creations.py:53-103`) lists `CREATIONS_DIR` without recursing,
plus `~/.friday/documents` and each of its `_renders/*.png` as a separate item (`:95-98`).
A record is `{name, size, modified, type (the extension), source}`: no title, kind, prompt,
provenance, privacy or project. It is capped at fifty (`files[:50]`, `:103`) and takes no
query. The gallery's filter is a row of extension chips, `all html md png mp4 mp3 wav`
(`ih:21522`), which miss jpg, webp, svg, webm, docx, pptx and xlsx. `/creation/<file>`
("Open in Tab", `:136-151`) and the provenance endpoints (`:597-641`) resolve
`CREATIONS_DIR` only, so both 404 for office files.

### 1.4 Every medium Friday makes, and where it lives (VERIFIED unless marked)

| Medium | Creator and route | Lands in | Shown in | Credentials |
|---|---|---|---|---|
| Image | `svc/creative_engine.py` `generate('image')`: local (`local_image.py`, `:724`), Higgsfield (`:756`), KIE (`:770`), Gemini. `/api/create/image`, alias `/api/creations/generate` (`rt/creations.py:379-409`). Tools `generate_image` (`svc/agent.py:5394`), `compare_image_takes` (`rt/creative_pipeline.py:208`) | `CREATIONS_DIR` | GeneratePanel (`ih:18698`), StudioPromptBar (`ih:20493`), the gallery, Files 3D | local path returns before provenance is written (`creative_engine.py:738-744`) |
| Video | `generate('video')`: Veo, local (`:947`), Higgsfield (`:966`). `/api/create/video` (`rt/creations.py:736`) | `CREATIONS_DIR` | the same two generators; `<video>` in the gallery detail | local video unsigned |
| Timeline (FFmpeg) | `svc/timeline_engine.py` `compose`; `/api/create/timeline`, `/api/creations/compose` (`rt/creations.py:534`). Tool `compose_timeline` (`agent.py:5470`) | video in `CREATIONS_DIR`; JSON in `~/.friday/timelines/` | TimelinePanel (`ih:19322`) | signed, with source hashes |
| Production pipeline | `svc/creative_pipeline.py`, templates at `:57-240`; `/api/pipelines/*` | `~/.friday/pipelines/runs` | ProductionPanel (`ih:19754`), ProjectsPanel (`ih:20024`) | text stages are not creations |
| Music | `svc/music_engine.py`; `/api/create/music`, `/available` (`rt/creations.py:412-444`) | `friday-music-*` in `CREATIONS_DIR` (`music_engine.py:512`); when cloud music is unavailable, a `.md` "demo" signed as music (`:532-569`) | MusicPanel (`ih:18999`), the prompt bar's Music chip, AlbumArtPlayer (`ih:19262`) | signed |
| Podcast | `svc/podcast_engine.py` with `podcast_sources/data/news/render`; `/api/podcasts` family (`rt/podcasts.py:51-182`); tools `make_podcast`, `podcast_list`, `podcast_play`, `podcast_source` (`podcast_tools.py:274-312`) | `~/.friday/podcasts/<id>/` | PodcastsView (`ih:11366`), PodcastPlayer (`ih:11109`, mounted `ih:59829`), PodcastButton in chat, Knowledge and the gallery detail (`ih:9628,15968,16537,21305`), PodcastChip on News runs (`ih:29286`), SendTo "Make a podcast" (`ih:11496`) | signed (`podcast_engine.py:806`); `privacy` field (`:249`) |
| Other audio | `speak_text` (ElevenLabs) to `DAILY_CREATIONS_DIR/<folder>` (`elevenlabs_tools.py:121,237`); `/api/voice/tts`, `/api/voice/cloud/tts`, `/api/news/front-page/audio` (`rt/news.py:542`, streamed, never saved) | daily folder, or nowhere | nowhere: the docstring at `elevenlabs_tools.py:24-26` says it "shows up in the gallery"; the gallery never lists that directory | none |
| Text | `/api/create/text` writes `friday-text-*.md` (`rt/creations.py:485-531`); `/api/create/poem` has no UI caller (`:677`) | `CREATIONS_DIR` | the gallery (md) | none |
| Code art | `/api/create/code-art` (`rt/creations.py:644-674`) | `CREATIONS_DIR` | the gallery (html) | none |
| Office (docx, pptx, xlsx) | `svc/office_engine.py` through the `office` and `office_check` tools (`agent.py:5517,5552`); no HTTP create route | `~/.friday/documents/` | the gallery, with every render PNG as a duplicate item; no icon for pptx/docx/xlsx (`ih:21122-21137`); "Open file" and ProvenanceBar 404 | `.made-by-friday.json`; overwrite waits for approval (`governance/action_gate.py:439-448`) |
| PDF | fill and sign only: `fill_pdf_form`, `sign_pdf` (`agent.py:6672-6697`) to `CREATIONS_DIR/forms/` (`svc/pdf_forms.py:32-36`) | `forms/` | nowhere: the gallery does not recurse | none |
| Charts and data | podcast data mode only: SVG in the episode's `charts/` (`svc/podcast_data.py:587-620`), served at `/api/podcasts/<eid>/charts/<name>` | inside the episode | the episode | with the episode |
| Presentation, website | `/api/create/presentation`, `/website` (`rt/creations.py:705-733`) via `svc/showcase_engine.py`; tools `create_presentation`, `create_website` (`agent.py:5497,5573`); no UI button | self-contained HTML in `CREATIONS_DIR` | the gallery (html) | none |
| 3D | nothing creates models; `creative_store` accepts `.glb` (`creative_store.py:64`); no viewer. "Files 3D" is a file *browser*: `static/studio_files3d.js` (`window.Files3DPanel`, `:2575`), `svc/studio_files.py`, `/api/studio-files/*` (`rt/studio_files.py:34-125`); roots Documents, Downloads, Desktop, Creations, extras, `~/Projects` (`studio_files.py:84,107-112`); it excludes `~/.friday` (`:127-146`); Share only under `CREATIONS_DIR` (`:339`) | n/a | Files 3D | n/a |
| Pages and sites | Sites workspace (registry id `futurespeak`, `static/workspace_registry.js:52`): FuturespeakWS (`ih:17358`) or SitesWS, a wrapper around FSStudio (`ih:16970`), chosen by `show_all_workspaces` (`ih:57686`); `svc/futurespeak.py`; deploys through `/api/vibe-code/launch`. There is no `/api/publish` and no `published/` directory; salon publish is spec-only here (`vibe-coding-salon.md:1136-1180`; the Phase 1 artifacts store is on `feat/salon-phase1`) | `~/.friday/futurespeak/*`, `~/Projects` | Sites | its own |
| Codebases | CodeWS (`ih:27253`), `svc/code_engine.py` (`PROJECTS_DIR = ~/Projects`, `:218`), `rt/code.py`; its Files tab is a second browser over `~/Projects` | `~/Projects` | Code | n/a |
| Posts | `svc/content_pipeline.py`, `content_composer.py`, `publisher.py`; `/api/content/*`; posts carry assets, sources, license and a provenance hash (`content_pipeline.py:123,527-551`) | sqlite | ContentWS (`ih:38178-38290`), ContentComposeTab (`ih:35633`, its asset picker reads `/api/creations` and so sees fifty items), QuickPost (`ih:37867,38161`) | hash |
| Daily creation | `svc/creations.py` writes `~/.friday/creations/<date>.json` and materialises a file into `CREATIONS_DIR` (`:149-164`) | both | the gallery; the "Daily creation (agent)" button hard-codes a developer `cwd` (`ih:21170`, the button at `ih:21498`) | signed |
| Meetings | `svc/meeting_capture.py:281`, `rt/meetings.py` | `~/.friday/meetings/<id>` | MeetingsPanel in Calendar (`ih:31393`) | n/a; not Studio's |

INFERRED: nine kinds have no home on screen: podcasts outside their own list, read-aloud
audio, PDF forms, pipeline scripts and storyboards, timeline JSON, presentations and websites
(no button to make them), sites and codebases (Sites and Code only), Draft output (never
saved, `rt/workflows.py:78-131`), and the charts inside episodes.

### 1.5 Actions today (VERIFIED)

Gallery card: open reader, open `/creation/` in a tab, Quick-post (`ih:21853-21898`); no
delete, send-to or podcast action. Gallery detail: Share/Post, PodcastButton, Open in Tab,
ProvenanceBar with license editing (`ih:21290-21316`, `ih:18584`). Prompt bar result: copy,
download, open in tab (`ih:20972-20988`). `SendTo` (`ih:11450`) offers clipboard, trust
graph, calendar, briefing, Gmail draft, Share/Post and Make a podcast, and is used in chat,
Career, Trust, News, Messages, Calendar and Contacts (`ih:9760,12224,23705,28960,31192,
31530,34122`) but nowhere in Studio. Open-in-tab has two mechanisms: the server-framed
`/creation/<file>` page and the standalone tab `/w/studio?creation=…` fed by `useTabState`
(`ih:7610`, `ih:21230`). Organize exists only in Files 3D, through approval cards
(`studio_files.py:599-696`).

### 1.6 What is messy, in one list (INFERRED from §1.1 to §1.5)

1. Two image and video generators on one screen, through different endpoints.
2. Views that leak: Podcasts and Generate render the prompt bar and the gallery underneath.
3. The gallery is a folder listing: fifty items, no title or kind, no search, chips that
   miss half the extensions, office renders as duplicates, 404s for office files.
4. Nine kinds with no home (§1.4).
5. Files 3D cannot see `~/.friday`, so it cannot browse most of what Friday makes.
6. Credentials missing on local image and video, text, poem, code art, presentations,
   websites, office and PDF forms; the manifest is written by three paths; a music "demo"
   `.md` is signed as music.
7. Privacy exists only on podcasts; the minor-mode banner says "coming soon" (`ih:21403`).
8. Dead and orphan entries: `/poem`, `/presentation`, `/website` without UI; a throwaway
   Front Page TTS beside the Front Page show; a developer path in the daily-creation button.
9. Doubled concepts: two "Projects" (`/api/projects` is chat; `/api/creative/projects` is
   creative), Production and Projects both start pipelines, two Sites components, two
   content stores, three text writers (Text, Draft, Compose), two file browsers, two
   open-in-tab schemes, and `workspace_studio` (`svc/workspace_studio.py:35`, `/api/workspace/*`),
   which is workspace customization and will collide with the new name.
10. Inconsistent verbs: SendTo absent from Studio; PodcastButton on the detail view but not
    on cards or in Files 3D; Share in Files 3D only under `CREATIONS_DIR`; PodcastsView has
    no grouping by show although `?routine=` exists.
11. Width: the Podcasts root at 900px (`ih:11410`, fixed in §1.7) and the gallery column at
    680px centered (`ih:21543-21545`), while the shell is full-bleed
    (`.ws-tab-body`, `ih:616-649`; News fills five columns at 1920, `ih:2458`).

### 1.7 The width fix, shipped (VERIFIED, commit `83f5595f`)

`tests/unit/test_studio_tab_width.py` asserts that `PodcastsView`'s root carries no
`maxWidth` or width in either file and that the intro paragraph keeps an `80ch` measure. It
failed at `e27ba160` on both counts and passes after the change; the podcast brand tests
(`tests/unit/test_podcast_ui_brand.py`), which require the podcast block to be byte-identical
in `index.html` and `ui_parts/app.html`, still pass. Rendered at 1920px: the live page's
Podcasts root measured 932px; the fixed page's 1884px, the body minus its 18px padding.
This edit is inside the podcast block, which the podcast session owns; it is one line, and
the orchestrator is told.

## 2. Standing rules

- **The shell owns the frame.** A workspace fills `.ws-tab-body` (full-bleed, 18px padding,
  `--fr-chat-dock` on the right while the tray is docked) and never sets its own width, top
  offset or `100vh` calc. A readable measure goes on text, never on the root. Mark the root
  `.ws-fill` to take the frame's height (`unified-shell.md`, the layout contract).
- **One top bar, one tray, one fullscreen.** Studio adds nothing to the bar beyond its name
  and tools in the context slot; its own controls live in its own head row.
- **Brand.** `--fr-*` tokens only; Inter for text, Orbitron display-only with a sans-serif
  fallback, JetBrains Mono for data; the `--fr-text-*` scale; amber only where something
  needs the owner; status never by colour alone; `.btn.active` with `aria-pressed` for a
  selected segment; `.btn-magenta` only to refuse, stop or remove; `fridayToast` for notices;
  every Studio control also in Ctrl+K. The product is Agent Friday™; a brand surface never
  prints her given name in its place; "Made with Agent Friday™" on anything published.
- **Governance.** Create is Class 1 (internal, reversible, confined to Friday's folders,
  `action_gate.py:374-389`). Publish, send, delete and any cloud model that takes the prompt
  off this computer go through the one gate and a card. A voice "yes, but" never approves a
  card as shown.
- **Privacy.** Nothing leaves this computer without a card and an egress line. The Library
  shows, for every item, whether it ever has.
- **The repository is public.** Prototype items and this document use synthetic titles. No
  owner paths, names or places.
- **Deterministic rules live in code.** The brand guard, the product-name test, the
  tab-width test and the mirror test are the enforcement; this document only explains them.

## 3. STORM: questioning it from seven perspectives

### 3.1 The owner, a journalist who makes things all day

"I do not think in endpoints. I made a chart this morning, a podcast about it, and a draft
from the podcast's transcript, and right now those are in three places with three looks. I
want to find the chart by its name next week, know whether it ever left this machine, and
turn it into something else without hunting for the button." The questioning forced the
*relationship* field: an item knows what it was made from and what was made from it
(§4.2), and "make a podcast of it" is a verb on the item, not a view.

### 3.2 A product designer holding the Linear bar

"One control, one look, one place. Count the ways to make an image on the Generate screen:
two. Count the ways to open something in a tab: two. Count the pill styles: four. Every
one of those is a place where the user re-learns the app." The questioning removed the
second generator, made Open in tab one mechanism (the standalone tab, `/w/studio?creation=`),
collapsed eight views into three segments, and wrote the keyboard map (§5.4) before the
mouse map. It also set the rule that the Viewer's rails never change by medium: the user
learns the frame once.

### 3.3 The privacy and governance engineer

"A library that lists everything is a library that *shows* everything. The Library's index
must carry privacy, and the default sort must not put a published page beside a private
medical PDF without saying which is which." The questioning gave every record a `privacy`
field with four values (private, shared-with, published, left-this-PC) backed by the egress
ledger rather than a guess, a filter for it in the rail, and the rule that the index is built
from Friday's own output roots only: it never crawls the home directory. Files 3D keeps its
own roots and its own grants; the Library's 3D view scopes it to the Library's items.

### 3.4 The archivist, who cares about content credentials

"Four creation paths write nothing. A `.md` is signed as music. That is not a credential
system; it is a credential suggestion." The questioning added the backfill (§8, P7): the
indexer signs what it can prove it made (a sidecar, a manifest line, a `.made-by-friday.json`)
and marks everything else **Unsigned** in the open, with the pill, and never pretends.
Signing becomes a property of saving, not of the tool that saved.

### 3.5 The podcast session

"I own the player and the News shelf. If Studio also lists The Briefing, we have two lists
and two truths." The questioning settled the boundary (§4.6): `origin` decides the home,
Studio's search may *find* a routine episode but *opens News*, and the only shared code is
`/api/podcasts` with its `routine` filter and the one global player. Studio's Create →
Podcast is the existing form (`ih:11366`) re-housed, and Studio does not touch the player.

### 3.6 The UI session, building the shell

"Do not give me a second header, a second tray, or a workspace that measures the viewport."
The questioning produced §2's first rule and the `shell.html` prototype, whose live measure
shows the root reflowing when the tray docks. The Studio head row (name, segments, count,
+ Create) is Studio's; the top bar's context slot shows only "Studio" and the two tools the
registry already declares.

### 3.7 The voice-first user

"I say 'make a podcast of the three charts' and I do not want to see a form." The questioning
confirmed that every Create tool is reachable by the tools that already exist
(`make_podcast`, `generate_image`, `compose_timeline`, `office`, `create_website`), added one
search tool for the Library (§6), and kept the rule that a cloud step or a publish always
comes back as a card, spoken as a card.

### 3.8 Synthesis: what the questioning changed

- A fourth segment ("Podcasts") was in the first draft; it is gone. A podcast is an item,
  a tool and a verb.
- "Files 3D" was going to stay a view; it becomes the Library's third view mode, scoped to
  Library items, and keeps its grants for browsing the rest of the PC behind an explicit
  "Browse this PC" root.
- The Library was going to read the filesystem live; it now reads an index that an indexer
  builds from the roots in §1.2, so it can carry title, kind, credentials, privacy and
  relationships, and search transcripts.
- The Viewer's actions were going to vary by medium; the set is fixed and some verbs are
  greyed with a reason (an episode about an episode is noise).
- "Projects" was going to be dropped; it stays as the third segment because Production,
  Projects (series bibles) and Timeline are all multi-item work and the owner uses them.

## 4. Design

### 4.1 Studio is three segments

| Segment | Default | What it is | Replaces |
|---|---|---|---|
| **Library** | yes | the index of everything made here, with search, filters, grid, list and 3D views, and a details rail | Gallery, the Podcasts list, Files 3D as a silo |
| **Create** | | one door: a sentence, a medium guessed and shown as a chip, the tool's fields beneath, disclosure beside | Generate, the prompt bar, Music, Timeline, "Make a podcast" |
| **Projects** | | work that spans items: pipelines, series bibles, timelines in progress | Production, Projects |

The head row is: `Studio` · the three segments as `.btn.active` with `aria-pressed` · a
count line ("212 things · 4 unsigned · 3 published") · `+ Create`. The row is Studio's; the
top bar is the shell's. Prototype: `library.html`.

### 4.2 The Library index: the spine

**A record per thing.** `~/.friday/library/index.sqlite` (one table, `items`, plus
`relations` and `fts`):

| Field | Meaning | Source today |
|---|---|---|
| `id` | stable, from the content hash where one exists | provenance hash, else sha256 of the file |
| `kind` | `image video podcast audio music doc office chart model3d page code post` | extension plus the sidecar/record kind; a podcast is a `podcast`, read-aloud is `audio` |
| `title` | a human title | sidecar prompt title, `episode.json` title, `.made-by-friday.json`, else the filename |
| `path` | where the file is | the roots in §1.2 |
| `created`, `modified` | times | file and record |
| `origin` | `create chat voice daily routine salon pipeline office` | sidecar, episode `origin`, pipeline run, `.made-by-friday.json` |
| `maker` | provider and model, and whether it ran on this PC | sidecar, provenance |
| `cost` | what it cost, if anything | provenance, else null |
| `credentials` | `signed` with the ledger hash, or `unsigned` | `~/.friday/provenance` |
| `privacy` | `private`, `shared` (sent to someone), `published` (a page), each with its ledger lines | the egress ledger and the publish receipts; podcasts' own `privacy` |
| `project` | the creative project, if any | `~/.friday/projects` |
| `prompt`, `sources` | what it was made from | sidecar, episode sources, timeline JSON |
| `text` | searchable words: a transcript, a document body, a chart's data labels | captions, md, office text extract |

`relations(from_id, to_id, how)`: `made_from`, `used_in`, `version_of`, `rendered_from`
(an office render points at its file and is never its own item).

**The indexer** (`svc/library_index.py`) walks only the roots in §1.2, on start, on a
creation event and on a timer; it never crawls the home directory. It reads every record
and sidecar that exists, signs nothing itself, and marks the rest `unsigned`. It is the one
place the fifty-item cap, the non-recursing listing and the duplicate renders die.

**The API** (`rt/library.py`): `GET /api/library?q=&kind=&origin=&privacy=&credentials=&project=&since=&sort=&cursor=`
returns records with a cursor; `GET /api/library/<id>` returns one with its relations;
`GET /api/library/<id>/file` serves it. `/api/creations` stays for a release as a thin view
over the index so Compose's asset picker keeps working, then goes.

### 4.3 Create: one door, the right tool behind it

The create bar takes a sentence. Friday guesses the medium (a small local classifier over
the sentence; the chips show the guess and let the owner change it) and the tool's fields
open beneath, pre-filled from the sentence. Beside the tool, a disclosure card says, before
anything runs: the model and whether it is on this PC, the cost, what would leave this
computer, and that credentials are signed on save. A cloud tool says "asks first" and does.

| Chip | Today's panel, re-housed | Route |
|---|---|---|
| Image | GeneratePanel's fields; the prompt bar's result strip | `/api/create/image` |
| Video | the same | `/api/create/video` |
| Podcast | PodcastsView's "Make a podcast" form (`ih:11366`), unchanged | `/api/podcasts` |
| Music | MusicPanel | `/api/create/music`; when unavailable, say so, write no `.md` demo |
| Text | the prompt bar's text mode | `/api/create/text`, now with credentials |
| Deck · Doc · Sheet | new fields over the `office` tool | the `office` tool through the agent, or a new `/api/create/office` that calls it |
| Chart | new, thin: data mode's chart step on a dataset | a new `/api/create/chart` over `svc/podcast_data.py`'s chart code |
| 3D | a chip only when a provider is configured | the Higgsfield remesh path |
| Page | the showcase engine, which today has no button | `/api/create/presentation`, `/website` |
| Timeline | TimelinePanel, with Library items as the bin | `/api/create/timeline` |
| Code | opens the salon with the brief | the salon's own door |

The second generator (GeneratePanel as a separate panel) and the prompt bar as a separate
strip are removed; their fields survive in the tool cards. Prototype: `create.html`.

### 4.4 The Viewer: one frame

Head: back to Library, the type glyph and word, title, date, previous and next in Library
order. Stage, by kind: an image at its size; a transport bar with waveform, captions and the
transcript below for podcasts and audio; video in place; a deck's pages with a page rail;
Markdown rendered and editable in place; a chart with its data and code beneath; a 3D
orbit for `.glb`; a sandboxed frame for a page (through the frame broker); a file tree with
"open in the salon" for code. Right rail, always the same four cards: **Credentials** (made
by, from, signed hash, cost, license), **Privacy** (now, and whether it has ever left this
PC, with the ledger lines), **Related** (made from, used in, versions), **Actions** (§4.5)
with an **Edit** strip per kind. Playing audio hands off to the global mini player, which
keeps playing when the owner leaves Studio. Prototype: `viewer.html`.

### 4.5 Per-type actions

The same seven verbs on every item, in the same place (the Viewer's rail, the Library's
details panel, the keys): **Open**, **Open in tab**, **Export** (the file and its credential
sidecar), **Send to…** (the shared `SendTo`, finally in Studio), **Make a podcast of it**,
**Add to project**, **Publish…**, and **Delete** in the deny style. Publish and Delete always
raise a card; Publish's card says where, what leaves (bytes), that the credential and "Made
with Agent Friday™" go with it, and how to undo. "Make a podcast of it" is greyed, with the
reason, on podcasts, audio and music. Per-medium meanings and the Edit strips are in
`actions.html`.

### 4.6 Boundaries

**Studio and News.** `origin: routine` episodes (the four shows, `podcast_engine.py:52-58`)
belong to their News run and to a shelf in News, grouped by show; the podcast session builds
that shelf and the move. `origin: user` episodes belong to the Library, whichever surface
asked for them. Studio search may return a routine episode; the result is labelled "In News"
and opens News on that run. News never lists user episodes. One `/api/podcasts`, one player.
The throwaway Front Page TTS (`rt/news.py:542`) is the podcast session's to retire.
Prototype: `boundaries.html`.

**Studio and Content.** A post is a Library item of kind `post` once it exists; Compose keeps
its own workspace and reads assets from the Library index instead of `/api/creations`.

**Studio and Code / Sites.** A codebase or a site made in the salon or Sites is listed in
the Library as a card that opens its own workspace; Studio does not run, deploy or edit it.
When the salon's publish lands (`feat/salon-phase1`), its receipts feed the `privacy` field.

**Studio and Knowledge.** Wiki pages and notes stay in Knowledge; a Studio text is a
creation, not a wiki page, until the owner sends it there.

**Studio and `workspace_studio`.** The customization service keeps its name in code; its UI
label becomes "Customize" wherever it is shown, so "Studio" means one thing to the owner.

### 4.7 Privacy and credentials, everywhere

Every item shows two pills: **Signed** or **Unsigned**, and **Private**, **Shared** or
**Published**. Both are words, not colours. Signing becomes a property of saving: the save
path in `creative_store` signs; the text, code art, showcase and office paths go through it
(P7). The indexer backfills what it can prove and never upgrades a guess to a signature.

### 4.8 Layout in the shell

`StudioWS`'s root becomes `.st-root.ws-fill`: a flex column filling the frame. The
Library's grid is fluid (`minmax(min(100%, max(220px, calc((100% - 60px) / 6))), 1fr)`,
the same shape News uses), so a 1920px tab shows six columns and a tray-docked window
reflows. No `100vh` calcs remain (the viewer iframe's `calc(100vh - 240px)` goes). Prototype:
`shell.html`, with a live measure.

## 5. UI, using existing elements first

### 5.1 What moves where

| Today | Becomes |
|---|---|
| Gallery (`ih:21461+`) and its 680px column | the Library grid |
| PodcastsView list | Library items of kind `podcast`; the form moves behind Create → Podcast |
| GeneratePanel, StudioPromptBar | Create's bar plus the Image and Video tool cards |
| MusicPanel, TimelinePanel | Create → Music, Create → Timeline |
| ProductionPanel, ProjectsPanel | Projects |
| Files3DPanel | the Library's 3D view (scoped), plus "Browse this PC" under its own roots |
| creation detail view (`ih:21260`) | the Viewer |
| ProvenanceBar | the Credentials card |
| Creation Log (`ih:21498`) | Create's "Just made" rail |
| the daily-creation button's hard-coded `cwd` | removed; daily creation is a Library origin |

### 5.2 What is removed

The second generator; the extension chips; the render-PNG duplicates; `/api/create/poem`;
the `.md` music demo; the minor-mode "coming soon" line (the Library's privacy filter and the
existing `minor_mode` content filters are the real thing).

### 5.3 Controls

Segments are `.btn.active` with `aria-pressed`. Pills carry words. The deny style is only on
Delete. Notices are `fridayToast`. Empty states say what to do next ("Make something with
Create, or ask Friday in chat").

### 5.4 Keyboard

`/` search · `← →` move · `Enter` open · `P` make a podcast of it · `S` send to · `Esc`
close · `Space` play/pause in the Viewer · `Ctrl+Enter` make it in Create ·
`Ctrl+Shift+F` fullscreen with chat (the shell's). Every one is in Ctrl+K.

## 6. Voice-first

Existing tools cover Create: `generate_image`, `compose_timeline`, `make_podcast`, `office`,
`create_presentation`, `create_website`. One tool is added, `library_search(q, kind?,
origin?, privacy?)`, answering with the top results and their ids, so "find the chart from
this morning" works; `navigate_to kind=creation` (`svc/desktop_targets.py:668-696`) then
opens it. "Publish the ferry page" raises the same card as the button, read aloud; a
conditional yes becomes "change it". Studio is reachable as today through
`fridayDeclareNav('studio')` (`ih:21102`), with `view` values `library create projects`
and the old names kept as aliases for a release.

## 7. Verification plan

- **Fail-first tests per piece.** The indexer: a fixture tree with one item per root in
  §1.2 yields one record each with the expected kind, origin and credentials, and an office
  render yields a relation, not an item. The API: cursoring past fifty; every filter
  narrows; `q` hits a transcript. The UI: `tests/unit/test_studio_tab_width.py` extends to
  `.st-root` (no width, no `100vh`); the segments use `.btn.active` and `aria-pressed`;
  the mirror test for the podcast block keeps passing; `test_product_name.py` and the brand
  guard pass.
- **Screenshots, actually looked at.** `/w/studio` at 1920, 1440 and 390, in a window and
  in a tab, with the tray closed, docked and in fullscreen-with-chat; each still described
  in one sentence; the Library's grid column count recorded per width.
- **The Linear bar checklist**, run on the stills: one segment control, one pill style,
  one button style, one empty-state style, one toast; every control in Ctrl+K; every
  action reachable by keyboard.
- **Behaviour diff against main**: open, open in tab, export, send to, make a podcast of
  it on one item of every kind, before and after.

## 8. Phased build plan

| Piece | What | Verified by |
|---|---|---|
| P0 (done) | the Podcasts width fix, `83f5595f` | the test, two stills |
| P1 | `svc/library_index.py`, `rt/library.py`, the sqlite index, the indexer on start and on a creation event | fixture-tree tests; `/api/library` cursoring and filters |
| P2 | the Library segment replaces Gallery and the Podcasts list; details rail; grid and list views; Files 3D as the 3D view, scoped | stills at three widths; keyboard; `SendTo` present |
| P3 | Create: the bar, the guess, the tool cards re-housed, the disclosure card; GeneratePanel and the prompt bar removed | each chip posts to its route; a cloud chip raises a card |
| P4 | the Viewer and the seven actions; Open in tab becomes one mechanism | one item per kind opened; publish and delete raise cards |
| P5 | Projects: Production, Projects and in-progress Timelines in one segment | pipelines still start and show their runs |
| P6 | the indexer's 3D view roots and "Browse this PC" | Files 3D still browses its roots with its grants |
| P7 | credentials on every save path; backfill marks the rest Unsigned | no new item is unsigned; the music demo is gone |

P1 touches no UI and can ship first. P2 and P3 land together or P2 first. The podcast
session's News shelf can land before or after P2; the boundary (§4.6) holds either way.

## 9. Costs and failure modes

- **Indexing cost.** The roots are small (hundreds of files); the text extract for office
  files and transcripts runs once per item. UNKNOWN: the cost of extracting text from a large
  deck on the reference machine; measure in P1.
- **A wrong guess in Create** sends the owner to the wrong tool; the chips are one click away
  and the guess is shown, never silent.
- **A stale index** after a tool writes a file without an event; the timer and the
  creation hook cover it, and Open always reads the file, not the index.
- **The boundary drifts** if a future tool writes `origin` wrongly; the podcast tests pin
  `ROUTINES` and `origin`.
- **Naming collision** with `workspace_studio`; the UI label changes, the code does not.

## 10. Decisions: what stays with the owner

- **D1.** Should Studio search show News shows at all (as links into News), or hide them?
  Recommended: show as links, labelled "In News". Product intent.
- **D2.** Is Projects the third segment, or a collection filter in the Library? Recommended:
  a segment, because pipelines and series bibles are work, not items. Product intent.
- **D3.** Does Files 3D survive as its own entry in the dock's Studio tools, or only as the
  Library's 3D view plus "Browse this PC"? Recommended: the latter. Product intent.
- **D4.** Cloud tools (Lyria, Veo, Higgsfield, remesh) as always-visible chips that say
  "asks first", or hidden until a key is present? Recommended: visible, because the
  disclosure card is the honest answer. Money.

Engineering decisions (the index shape, the API, the fluid grid, the single open-in-tab
mechanism, signing on save) are made here and owned by the session.

## 11. What would falsify this

- The owner keeps opening the old views by name; the aliases log would show it.
- The index grows past what one sqlite file handles comfortably on the reference machine.
- The Create guess is wrong more than one time in five on the owner's real sentences.
- The podcast session's News shelf and the Library's search produce two truths for one
  episode; the boundary test would fail.

## 12. Acceptance

The bar is the best shipped media library and creation surface in each area (a photo
library's search and rails; a DAW's one transport; Linear's one control, one look, one
place; Superhuman's keyboard), plus the brand check: the `--fr-*` tokens only, amber only
where something needs the owner, status never by colour alone, Agent Friday™ on every brand
surface, a still hologram except on a real event. A change that puts a width on a workspace
root, a status hue on a hover, or a second way to do the same thing fails.

## Appendix A. The expert panel (STORM)

The owner (§3.1); a product designer holding the Linear bar (§3.2); a privacy and governance
engineer (§3.3); an archivist for content credentials (§3.4); the podcast session (§3.5);
the UI session building the shell (§3.6); a voice-first user (§3.7).

## Appendix B. North-star rows

| Row | Requirement | After this spec |
|---|---|---|
| NS-21.14-1 | prompt and reference management, generation history, provider and model disclosure, cost | Create's references and disclosure card; the Library is the history |
| NS-21.14-2 | content credentials, editable timelines, asset relationships, review and approval, export | credentials on every save; Timeline in Create and Projects; `relations`; cards; Export |
| NS-21.12-1 | documents: creation, conversion, editing, preview | Deck · Doc · Sheet in Create; the Viewer's page rail; "make it a deck" |
| NS-21.12-2 | visual QA, version history, provenance, overwrite approvals, export | `version_of` relations; the office gate stays; Export |
| NS-6.4-4 | never silently turn an internal file into a shared artifact | Publish always cards; privacy shows the ledger |

## Appendix C. Voice tools

Existing: `generate_image`, `compose_timeline`, `make_podcast`, `podcast_list`,
`podcast_play`, `office`, `create_presentation`, `create_website`, `navigate_to`. Added:
`library_search`. Answers follow the voice tool contract; a card is spoken as a card.

## Appendix D. Prototype index

Open any file in a browser; no server needed.

| Screen | File | Shows |
|---|---|---|
| Map | `index.html` | the six screens and the brand line |
| Library | `library.html` | the rail, search, filters, grid, list, 3D toggle, the details panel, keys |
| Create | `create.html` | the bar, the guess, eleven tool cards, disclosure, "just made" |
| Viewer | `viewer.html` | one frame, nine stages, the four rails |
| Actions | `actions.html` | the verb matrix, "make a podcast of it" per medium, the publish card |
| Studio and News | `boundaries.html` | the four shows in News, user episodes in Studio, the link not the copy, one player |
| In the shell | `shell.html` | the top bar, the tray, fullscreen with chat, a live measure of the root |
