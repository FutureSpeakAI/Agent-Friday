# Surface re-evaluation: the audit, the target information architecture and the migration

> **Status:** proposed. Nothing in this document is built; it is the plan and the prototypes
> for a reorganisation of every user-facing surface, sequenced after
> [`unified-shell.md`](unified-shell.md). The design system it applies is
> [`docs/design/hig/README.md`](../hig/README.md) (the Agent Friday™ Human Interface
> Guidelines). The clickable prototypes are in
> [`docs/design/prototypes/surface-reorg/`](../prototypes/surface-reorg/index.html).
> **Last verified:** 2026-09-30 against main `e27ba160`, `feat/unified-shell` (pieces 1 to 7),
> `piece/P-BRAND-0` (BRAND.md) and the Studio session's `docs/studio-integrated` tree.
> **Owns nothing another session owns:** the shell, the top bar and the normalization audit
> belong to the UI session (`unified-shell.md`, `docs/brand/fidelity-audit.md`); the Media
> workspace belongs to the Studio session (`studio-integrated.md`); the avatar and the scene
> belong to the avatar session. This document references their work and sequences after it.

## What the owner asked

"Some of the UI surfaces are becoming messy and cluttered, despite their usefulness. Perhaps
we need a surface re-evaluation across the app, and maybe a reorg around certain professional
standards, like making our settings UI much more like Anthropic's or OpenAI's settings UI,
and making the other workspaces and the system UI look more like Windows and macOS
aesthetics. You tell me."

**The answer, in one paragraph.** Yes to the re-evaluation, and yes to the standards, with
one correction: we borrow the *conventions* of professional software, never its look. The
brand rule stands: bars judge quality and are never a look to copy, and the hologram is
Friday's identity (BRAND.md; AMENDMENTS A1). What the product lacks is not a visual
language, which it has, but *structure*: eighteen workspaces that each invented their own
toolbar, view switcher, empty state and layout; a System page that is nine unrelated cards in
a column; approvals listed in five places with two card implementations; a Settings window
whose nine tabs hide the same switch in three of them; a top bar with sixteen controls; and
no surface at all for the one thing a sovereign AI must show, the ledger of what left the
machine (the route exists, `routes/research.py:107`; nothing calls it). The fix is one design
system with three layers and a small number of templates, applied surface by surface.

## 1. The surface audit

### 1.1 Method

- **Frames.** The UI session's normalization audit captured every registry workspace at
  1600 by 1000 in both contexts (a desktop window and its own tab) plus the start screen: 37
  frames, taken on 2026-09-30 after the fixes of `fidelity-audit.md`. This audit reused that
  set rather than re-shooting it. The surfaces that set did not cover were captured the same
  way, read-only against the running server with every non-GET request stubbed: the Settings
  window and each of its nine tabs, the quick-settings dropdown, the chat tray, the command
  palette, the notifications dropdown, an approval card (a stubbed pending record with
  fictional content), the System page scrolled to its end, and Studio's Podcasts and Files 3D
  views. 57 frames in all, each looked at. The frames stay on the owner's machine: they show
  his mail, calendar and people.
- **Code.** Three inventories of `index.html` (60,182 lines on main; `ui_parts/app.html` is a
  22,222-line partial mirror, see `src/agent_friday/ui/build_ui.py`), the workspace
  registry, the voice tool list, DEFAULT_SETTINGS and the design docs that constrain the
  surfaces: `workspace-ecosystem.md`, `first-run-and-onboarding.md` §7A and §9 (the control
  room), `task-visibility.md`, `tasks-tray-honesty.md`, `v6-wholeness-spec.md` §1.1 and the
  Studio session's `studio-integrated.md`.
- **Bars.** Settings against Claude.ai's and ChatGPT's settings (a category rail, plain rows
  with one line of explanation, search, a clear danger zone). Workspaces against Linear and
  Superhuman (one toolbar, one list-and-detail layout, keyboard first, a command palette with
  actions in it, dense but calm). The desktop against visionOS (chrome appears when relevant,
  content floats on the identity layer, nothing nags). The brand check in BRAND.md.

### 1.2 What every frame has in common

Nine patterns appear across the set. Each is a template decision, not a per-workspace fix.

| # | Pattern | Where it shows | Template decision (HIG §) |
|---|---|---|---|
| P1 | **The 💡 banner.** Every workspace opens with an italic cyan "💡" sentence of status ("0 posts queued · 2 platforms connected", "Draft engine ready. Pick a mode…"). It is a status line dressed as a tip, and it is the first thing the eye lands on. | 15 of 18 | A workspace's status lives in its header's subtitle in `--fr-dim`, one line, never italic, never with a glyph (HIG §4.2). |
| P2 | **A row of five to nine segmented view tabs**, each a `.btn` with an emoji, then a second row of actions. News shows 8 views + 4 actions + View in 3D; Studio 8 views + 5 modes + 2 filters; Career 8 raw-id tabs plus 3 duplicate header buttons; Sites 9; Content 6; Finance 6; Health 5. | 11 of 18 | Views go in a sidebar (list of sections) or a single segmented control of at most 4; actions go in one toolbar at the right; everything else goes in the ⋯ menu and the palette (HIG §4.3). |
| P3 | **No shared layout.** Messages is a proper three-pane; Knowledge is a split; Calendar, Workflows and System are single scrolling columns; Health, Finance, Family and Sites are card grids; Draft and Marketplace are a form in a void. | all | One workspace frame: sidebar, content, optional inspector, with the sidebar collapsible and remembered per workspace (HIG §4.1). |
| P4 | **Empty, loading and error states differ or do not exist.** "Loading your Front Page…" at 16px centred; "No listings yet. Be the first to publish!" at 12px; Knowledge's inline "(empty — will append)"; Family, Health, Finance, Calendar and the People list swallow failures and show nothing. Only Trust, Workflows and Messages show an honest error with a retry. | all | One `EmptyState` component with three modes (empty, loading, failed) and one action; a failure is never shown as zero (HIG §5). |
| P5 | **The window is the tab, scaled down.** A desktop window at its opening size (News, Studio, Messages) wraps the same three rows of controls to five or six rows and leaves a 160px strip of content. | every `desk_*` frame | Container queries, not viewport queries: the sidebar collapses to icons and the toolbar folds into ⋯ under 720px of workspace width (HIG §4.6). |
| P6 | **Settings scattered across the surface.** `show_all_workspaces` has three switches (General, the gear dropdown, the dock editor). Off the record and conversation logging are in Privacy and in the gear dropdown. Knowledge-graph settings are split across three tabs. Cloud consent is in Models and in Privacy. Phone costs are in Accounts and in Spending. | Settings, the top bar | One home per setting, and the gear opens Settings itself; quick toggles are palette actions (HIG §6.1; §2.3 below). |
| P7 | **Approvals in five places, two cards.** The popup, the System card, the tab drawer (`StandaloneApprovals`, which re-implements the card without batch lines or the share body), the condensed widget (which reads `a.id` while records carry `approval_id`, so its buttons do nothing) and notification deep links. | system, tabs, widget | One card component, one queue surface ("Needs you"), one pill in the bar when something is waiting (HIG §7.2). |
| P8 | **Receipts in four places that do not link to each other.** "What Friday did" (governance receipts), "Changes Friday made" (organise receipts with undo), the Activity Ledger overlay (actions, reachable only from the bell), the memory ledger route; and the egress log, which has no UI. | System, bell | One Activity surface with a sidebar: Now, Needs you, Receipts, Left this machine (§2.2). |
| P9 | **Jargon and raw ids in user-facing rows.** Career's tabs are their ids ("quickref", "wallet"); model ids in seats; "all-MiniLM-L6-v2 on this CPU"; Twilio SIDs and "A2P 10DLC"; `.p12`; "Context Compression (Headroom)" as the first card of System; `"(empty — will append)"`. | Settings, System, Career, Knowledge | Plain words in rows; the id in `--fr-font-mono` only in an inspector or a tooltip (HIG §6.2). |

### 1.3 Per-surface findings

Each row: the surface's job as built, what clutters it, what is duplicated elsewhere, what
is misplaced, and the verdict (keep, reshape, merge, retire). Line references are to
`index.html` on main unless named otherwise.

#### Workspaces

| Surface | Job as built | Clutter | Duplicates | Misplaced | Verdict |
|---|---|---|---|---|---|
| **News** (`NewsWS` 27892) | Trust-scored reader with Friday-written editions. | 8 view tabs + 4 actions + View in 3D + a source pill above the fold; three rows in a window. | Its Trust view (source trust) vs the Trust workspace (people). | The shows it makes (every News routine run) are browsed only in Studio → Podcasts (11366, mounted at 21461). | **Reshape**: sidebar of editions; Sources (trust, media diet, customize) in the inspector; a Shows shelf (Studio session's D1). |
| **Messages** (`friday_mail.js`) | Gmail-grade inbox; compose files an approval card. | Four rows of chips above the list (accounts, search, filters, lanes), a yellow permission banner, "Build real Gmail layout" as a loose button at the top. | The dead fallback `MessagesWS` (30761) when the script loads. | Friday's customization action belongs in the workspace's ⋯ menu, not the top of the page. | **Keep as the reference three-pane**; fold chips into one toolbar and a filter popover. |
| **Calendar** (`CalendarWS` 31246) | Today and the week, prep cards, meeting recording. | Quick-add, the Meetings bar and the week strip stack before the day. | Meeting follow-up drafting vs Draft and Messages. | Meetings is a second workspace inside this one. | **Reshape**: sidebar (Today, Week, Meetings, Prep); the Studio session's Media takes drafting. |
| **Family** (`FamilyWS` 18335) | A placeholder: three generic holiday countdowns and four tiles that send chat prompts. | | Countdowns vs the landing cluster (which the shell now feeds from his calendar and wiki, `unified-shell.md` §10.1). | Members are People; routines are Routines; the data is wiki pages. | **Retire** (owner question Q1). |
| **Health** (`HealthWS` 33477) | Read-only cards over JSON files. | | Providers and mechanics are People. | "Fleet Health" (vehicles) is not health. | **Reshape** as a records workspace on the template; vehicles leave. |
| **Finance** (`FinanceWS` 33120) | Positions, card perks, money contacts from files. | | Financial Contacts are People. | The wallet (ψ economy, `WalletPanel` 23231) is mounted here and in Settings; v6 §1.1 keeps the economy off product surfaces. | **Reshape** as a records workspace; the wallet leaves. |
| **Career** (`CareerWS` 11681) | Front end for the external career-ops repo. | 8 raw-id tabs, three of them repeated as header buttons; a setup card that never collapses. | Outreach drafting vs Media; warm leads are People. | | **Reshape** on the template; labels, not ids. |
| **People** (`ContactsWS` 33970) | Contacts with trust, research notes and Forget. | The Relationship-memory panel (sync, import, two Google-save buttons) sits above the list on every visit. | Trust dimensions and evidence vs the Trust workspace; Forget vs Knowledge. | | **Keep**; becomes the home for every person (Trust folds in); the panel becomes a sheet. |
| **Code** (`CodeWS` 27253) | Local developer console. | | Repos vs Sites "discovered repos"; Procs vs System's Top Processes; Files vs Studio's Files 3D. | | **Keep**; Sites stops listing repos. |
| **Sites** (`SitesWS` 24780 or `FuturespeakWS` 17358) | Two components under one label: a site portfolio, or the owner's business dashboard (pipeline, revenue, legal, talent, demos, its own "studio"). | 9 view tabs. | Its "studio" view vs Studio. | A company dashboard under a generic label, shown to anyone with all workspaces on. | **Split**: Sites keeps one job (your websites); the business dashboard becomes the owner's own non-core workspace (Q2). |
| **Draft** (`DraftWS` 32763) | One-shot writer per channel. | | Five text writers exist (Draft; Content Compose and Ideas; Studio's Text mode; Career Outreach; Calendar follow-ups). | | **Merge into Media** (the Studio session's Create segment; `studio-integrated.md` names the three writers and does not resolve Draft; this document proposes Draft's channel modes as Create → Text presets). |
| **Content** (`ContentWS` 38186) | Multi-platform post pipeline. | 11 "connect" chips in the composer; a "Calendar" tab that is not the Calendar. | Compose vs Draft. | | **Merge into Media** (the Studio session keeps Compose; this document proposes it as Media's Publish segment, with Queue, Calendar and Analytics as its sections). |
| **Knowledge** (`KnowledgeWS` 15663) | The wiki's pages and the galaxy. | | Pending updates (wiki approvals) vs the approvals queue. | Its settings are split across three Settings tabs. | **Keep**; pending updates become a "Needs you" kind. |
| **Trust** (`TrustWS` 23613) | A list of people with dimensions and evidence. | | A subset of the People detail page; shares People's 3D source. | Source trust is in News. | **Merge into People** (a Trust tab on a person) and News → Sources. |
| **Studio** (`StudioWS` 21107) | Generation, gallery, files, podcasts. | 8 views + prompt bar with 5 modes + log/daily + 7 file-type chips; a "Family mode" toggle whose banner says it is coming soon (21403). | Production vs Projects; two generators; Files 3D vs Code Files. | Podcasts (News's shows). | **Becomes Media** (Studio session: Library, Create, Projects). |
| **Marketplace** (`MarketplaceWS` 22735) | Listings priced in ψ. | | | v6 §1.1 and `workspace-ecosystem.md` Phase 1: a catalogue at most, purchases disabled. | **Off the dock** until Phase 4; not deleted. |
| **Workflows** (`WorkflowsWS` 24472) | Routines from a sentence, outward actions gated. | A sentence box, a step builder, a notice, "waiting for your OK", "also working on", the list, the built-ins: seven blocks in a column. | "Also working on" vs the tray; "waiting for your OK" vs approvals; built-in News jobs vs News's own buttons. | The control room (§9.1 of the onboarding spec) lists Routines as a Settings section. | **Keep as a workspace, rename Routines** (the word the onboarding spec and the registry's alias list already use); list-and-detail on the template; Settings links to it. |
| **System** (`SystemWS` 25300) | Nine unrelated cards in one column: context compression, weekly self-review, approvals, task records, what Friday did, changes with undo, disk, processes, the context log with delete-range. | Everything; no sidebar, no sections, deep links scroll. | Approvals (P7), receipts (P8), processes vs Code → Procs, task records vs the tray. | Machine telemetry is Health; the context log is a Privacy setting; self-review is a review. | **Becomes Activity** (§2.2); telemetry and the context log move to Settings. |

#### System surfaces

| Surface | Job as built | Finding | Verdict |
|---|---|---|---|
| **Top bar** (`App` 57769–58318; one bar everywhere after the shell's piece 2) | Identity, context, model, and the panels. | Sixteen controls: lockup, scene menu, orb counter, model, clock, bell, Quick Draft, chat, camera, computer-control light, gear, four resource chips, network pill, connection light. The gear opens a quick-settings dropdown, not Settings. The lockup is a hidden door to System. | Eight controls (§2.3). The scene menu and camera stay desktop-only and belong to the avatar session. |
| **Dock** (59701) | Launcher with Life, Work, System groups. | Sound: magnification, auto-hide, badges, ctrl-click to a tab. 19 entries with all workspaces on; "System" is a group name and a workspace name. | Keep; the groups become Life, Work, Friday; the default dock is the core set (§2.1). |
| **Window chrome** (`FWin` 8313) | Title, tools, maximize, open in tab, close; resize from any edge; geometry remembered. | No minimize; no way to tile two windows; the workspace's own tools (chat, talk, history) sit in the title bar beside the window controls. | Keep; add snap-left/right and the shell's fullscreen-with-chat; workspace tools move into the workspace header's ⋯ (§2.4). |
| **Command palette** (`CommandPalette` 10932) | Workspaces, then contacts and routines after typing. | No actions, no settings, no approvals. Only Ctrl+K opens it; nothing in the UI does. The shell's piece 4 added the bar's buttons. | Becomes the action surface: every toolbar action, every setting row, every approval, "open … in a tab" (HIG §8). |
| **Chat tray** (`ChatSurface` 9438) | The chat, docked, windowed or in a tab. | The composer carries nine controls (cite, show sources, mic, audio devices, attach, input, send, plus the header's six). Four chat implementations exist (`ChatSurface`, `WorkspaceChat`, the landing prompt, `ConversationWindow`). | Keep the surface; the HIG sets the composer at input, mic, attach, send, with the rest in ⋯ (HIG §7.4). The fourth implementation is the UI session's call. |
| **Notifications** (bell, 58996) | Reasoning, tasks, failed, running now, notifications, in one dropdown. | Five sections in one 380px dropdown; the Ledger button hides here; the task cards duplicate Activity. | The bell shows notifications and a "Now" summary; everything else is a link into Activity (§2.2). |
| **Approvals** (P7) | | Five surfaces, two cards, one broken. | One card, one queue (Activity → Needs you), one pill. |
| **Toasts** | `fridayToast` plus six local implementations (`WorkspaceChat`, Outreach, Knowledge, System, News, the tab). | | One toast (the shell's piece 4 did this for 18 workspaces on its branch). |
| **Settings** (`SettingsWS` 52508) | Nine tabs in a 184px rail, 860px content. | The rail and the rows are the right shape already. Inside: status displays mixed with settings (Spending is six status blocks and three settings; Advanced opens with a work queue); destructive buttons beside ordinary ones ("Delete my profile" beside "Run the setup chat again"; "Erase everything" after the export buttons); developer knobs beside everyday ones (claws re-pin, keystore wrap, decision shadow mode, a Google Fonts toggle in Privacy); 56 of 123 DEFAULT_SETTINGS keys have no row; two copy promises point at rows that do not exist (`pause_warnings_off`, `voice_room_approvals_require_name`); no search; nothing voice-callable but the chat model seat. | Reshape on the settings row contract (HIG §6): a grouped rail, search, four lines per row, one home per setting, a danger zone, status pages marked as status, every row addressable by voice (§2.5). |
| **The ledger of what left the machine** | | Does not exist as a surface. `GET /api/privacy/left-the-machine` (`routes/research.py:107`) returns every cloud call from the egress log and nothing calls it. The Activity Ledger overlay lists actions, not sends. | Activity → Left this machine (§2.2), and the Privacy map the onboarding spec's §9.4 prototypes. |

### 1.4 Dead or placeholder views

Found and listed so the migration removes rather than restyles them: `FamilyWS` (chat-prompt
tiles); the fallback `MessagesWS`; Studio's Family-mode "coming soon" banner; Music's demo
`.md` when cloud music is unavailable; the `wsMap` "Coming soon" fallback (57737); Career's
"on the roadmap" line (12300); Health and Finance's "edit this JSON file" as their only
editor; the condensed widget's approval buttons (P7).

## 2. The target information architecture

### 2.1 Workspaces: one job each

The registry stays the single source of identity (`static/workspace_registry.js`; BRAND.md
"Per-workspace identity"). Ids are stable: a retired or merged workspace keeps its id as an
alias so deep links, saved layouts and voice phrases still land.

| Group | Workspace (id) | The one job | Sections (sidebar) | What arrives | What leaves |
|---|---|---|---|---|---|
| Life | **News** (`news`) | Read the news Friday trusts, in her editions. | Front Page, Feed, Read Later, Notes, Briefings, Weekly, Editorial, Shows | The News shows (from Studio → Podcasts, by the Studio session's D1) | Source trust and the media diet go to the inspector, not a tab |
| Life | **Messages** (`messages`) | All your mail in one inbox, sorted by what needs you. | Priority, Inbox, Starred, …, Labels | | The fallback `MessagesWS`; "Build real Gmail layout" to ⋯ |
| Life | **Calendar** (`calendar`) | Your day, your week and your meetings. | Today, Week, Meetings, Prep | Family's dated items (as wiki-fed countdowns the landing cluster already ranks) | Follow-up drafting (to Media) |
| Life | **People** (`contacts`) | Everyone you deal with, and what Friday remembers and trusts about them. | All, Household, Follow-ups, Recently met; per person: Profile, Trust, Timeline, Notes | Trust (per person), Finance contacts, Health providers, Career warm leads, Family members | |
| Life, optional | **Health** (`health`) | Your medications, providers, insurance and appointments. | Overview, Medications, Appointments, Insurance | | Vehicles (to Finance → Property) |
| Life, optional | **Finance** (`finance`) | Your money at a glance: positions, perks, property and the people who manage it. | Overview, Portfolio, Perks, Property and vehicles, Quick reference | Vehicles | The wallet (held feature) |
| Work | **Career** (`career`) | Your job search: roles evaluated, applications tracked. | Overview, Tracker, Scanner, Reports, Pipeline, Curated, Interview | | Outreach drafting (to Media); setup becomes an empty-state card |
| Work | **Code** (`code`) | Your repos, git, files and running processes. | Repos, Vibe, Git, Files, Processes, Logs | System's Top Processes | |
| Work | **Sites** (`futurespeak`) | Your websites: status, deploys and new projects. | Sites, Deploys, New project | | The business dashboard (Q2); the repo list (Code has it) |
| Work | **Media** (`studio`, aliases `draft`, `content`) | Make, keep and publish images, video, audio and text. | Library, Create, Projects, Publish (Studio session owns; this document proposes Draft as Create → Text presets and Content as Publish) | Draft, Content, Studio | Podcasts from News routines (to News → Shows) |
| Friday | **Knowledge** (`knowledge`) | Your wiki's pages and the graph that links them. | Pages, Graph, Split | | Pending updates become a "Needs you" kind in Activity (still approved here too) |
| Friday | **Routines** (`workflows`) | What Friday does on a schedule, and what she asks first. | Yours, Built in, Runs | Workflows | "Also working on" and "waiting for your OK" (to Activity) |
| Friday | **Activity** (`system`) | What Friday is doing, what needs you, what she did, and what left this machine. | Now, Needs you, Receipts, Left this machine, Weekly review | The bell's tasks and running-now sections; every approvals list; What Friday did; Changes Friday made; the Activity Ledger; the egress log (new UI); the weekly self-review | Disk, processes, context compression, the context log (to Settings → Health, Privacy) |
| Friday | **Settings** (`settings`) | Everything Friday does, in plain words, one home per setting. | §2.5 | Quick settings; the context log; machine telemetry; voice readiness and privacy check as status pages | |
| Off the dock | **Marketplace** (`marketplace`) | A catalogue, until Phase 4 (`workspace-ecosystem.md`). | | | |
| Off the dock | **Business** (new, owner's) | The owner's company dashboard: pipeline, revenue, legal, talent, demos. | | `FuturespeakWS` minus its sites view | |
| Retired | **Family** (`family` → alias of `contacts`, filter Household) | | | | Members to People, dates to Calendar and the landing cluster, routines to Routines, pets to People (household) |
| Retired | **Trust** (`trust` → alias of `contacts`, tab Trust) | | | | |
| Retired | **Draft**, **Content** (aliases of Media) | | | | |

The default dock on a fresh install is the core set: News, Messages, Calendar, People ·
Career, Code, Sites, Media · Knowledge, Routines, Activity, Settings. Twelve entries in three
groups named **Life, Work, Friday**, which fits the dock's own 1450px row without wrapping
(`index.html:348`). Health, Finance, Business and Marketplace are opt-in from Settings →
Appearance → Dock, which already exists (`DockSettingsPanel` 48845).

### 2.2 Activity: the system workspace with a job

System's nine cards become one workspace on the template, with the sidebar as its spine:

| Section | Contents | Comes from |
|---|---|---|
| **Now** | Running tasks with their `now:` line, step N of M, cost, steer, cancel; live processes marked as such; interrupted tasks with Resume (every string and control `tasks-tray-honesty.md` pins) | The bell's TASKS and RUNNING NOW; Workflows' "Also working on" |
| **Needs you** | Every pending card, one component (`ApprovalCardBody`), grouped by kind: actions, shares, wiki updates, held posts, setup | System's Approvals card, `StandaloneApprovals`, the widget's card, Knowledge's pending updates, Content's held posts |
| **Receipts** | What Friday did (signed receipts) and the changes she made with Undo, one list with a kind filter and "why did you do that?" on each row | `WhatFridayDidCard`, `FridayChangesCard`, the Activity Ledger overlay |
| **Left this machine** | One row per cloud send: when, to whom, what (with "see exact text"), what was scrubbed, why (the routine, the ask, the approval id), the day's holds; the egress-gate layer status at the top, honestly, with a held-calls notice when a layer is down | `GET /api/privacy/left-the-machine` (`routes/research.py:107`, unused); the voice egress chips; `/api/compute/sent` |
| **Weekly review** | Epistemic quality, sycophancy, the reflection, the focus list, Run now | System's Weekly Self-Improvement |

The bell keeps notifications and a two-line Now summary; its other sections become links
into these. The "what is waiting" pill in the bar (the shell's piece 4) opens Needs you.

### 2.3 The top bar and the dock: minimal chrome

After the shell's one bar everywhere, this document proposes the bar's content, for the UI
session to build. Eight controls, left to right:

1. The lockup (desktop: opens nothing on click; the hidden door to System goes).
2. The context slot: the scene menu on the desktop (avatar session's), the workspace's icon and name in a tab.
3. The model selector.
4. The "needs you" pill, only when something is waiting.
5. Search (opens the palette; the palette is also Ctrl+K).
6. Chat.
7. Settings (opens Settings; the quick-settings dropdown goes, its four toggles become palette actions and Settings rows).
8. The connection light, which on hover or click shows the clock, GPU, RAM, CPU and disk, and whether the local seat is held.

The orb counter, Quick Draft (a Media → Create entry in the palette), the camera (desktop
only, avatar session's), the computer-control indicator (it stays, as the HIG's one
exception: a live-danger indicator is never hidden) and the resource chips leave the
default bar. The narrow-bar rules of the shell's §6 still apply.

### 2.4 Windows and tabs

Workspaces are proper app windows. The window chrome keeps title, maximize, open in tab and
close, and gains snap left and snap right (Win+arrow, Cmd+Ctrl+arrow) and the shell's
fullscreen-with-chat. The workspace's own tools (chat about it, talk about it, its earlier
versions, Friday's customization) move from the title bar into the workspace header's ⋯
menu, so the title bar is the OS's and the header is the app's. Every window's layout is
remembered per workspace, and its sidebar state with it.

### 2.5 Settings: the control room, reconciled

Three lists exist: today's nine tabs; the orchestrator's eleven (General, Personalization,
Voice, Models and Brain, Privacy and Data, Connectors, Permissions and Approvals, Appearance
and Hologram, Notifications, Advanced, About); the onboarding spec's control room (Getting
to know you, Models and seats, Permissions and rules, Connections, Routines, What Friday
knows about you, Devices, Backups, Health, Advanced, the Privacy map). They reconcile into
one rail of thirteen pages in four groups, with the control room's four-line row contract
(§9.2), its search-or-say box and voice diff (§9.3), its modes (§9.5) and its privacy map
(§9.4) folded in.

| Group | Page | Holds today's | Control room section | Notes |
|---|---|---|---|---|
| You and Friday | **General** | General: her name, language, startup, the local address, your profile answers | Getting to know you (progress strip, re-run any step) | "Delete my profile" moves to the danger zone |
| | **Personality** | Personality text, the style sliders (verbosity, formality), how she addresses you, proactivity | Getting to know you | New page; today the persona is one textarea |
| | **Voice** | Voice & Tracking's voice half: engine, model, voice, listening, push-to-transcribe, Gemini Live options | | Voice readiness becomes a status block at the top, marked as status |
| | **Appearance & Hologram** | Appearance & 3D: dock, dazzle, constellation grouping; Tracking (head and hand); landing mode (shell §10.4); reduced motion, text size, 3D off | | The scene's own controls stay in the scene menu (avatar session) |
| Thinking | **Models & Brain** | Models: routing mode, the seat per job, the Model Soup, local models, the model browser; Advanced's turn limits; Spending's stuck-model guard; keep-brain-warm and call mode when those settings exist (neither key exists today) | Models and seats | Cloud consent is one row here that links to Privacy & Data, not a second copy |
| | **Spending** | Budgets, hard stops, scheduled jobs without a local model | Models and seats (sub-page) | Kept as its own page: money gets a door of its own; the six status blocks move under a "This month" status header |
| Trust and data | **Privacy & Data** | Vault, cloud consent, wiki sections off the cloud, conversation log and off the record, retention, export, erase; the context log (from System); "What Friday knows about you" (§9.7) | What Friday knows about you; Privacy map | The privacy check becomes a status block; the Google Fonts toggle moves to Advanced |
| | **Permissions & Approvals** | What needs your sign-off, grants for routines, computer control, Friday's rules on this PC (claws), modes (Work, Travel, Off the record) | Permissions and rules | The approvals *queue* is Activity → Needs you, linked from here |
| | **Connectors** | Accounts & Keys: Google accounts, providers and keys, phone, MCP servers, Friday's browser, signing | Connections | Each connector card obeys the onboarding spec's card contract: what she sees, where it runs, read and write apart, how to revoke |
| | **Notifications** | Nothing today: priorities are fixed in Python (`notifications.py`); the bell, OS notices and the audio slots have no settings | | New page: what reaches the bell, the OS and your phone, quiet hours, sounds (BRAND.md's three audio slots) |
| This PC | **Devices & Backups** | Nothing today (federation's paired-device sync is a held feature) | Devices; Backups | New page, from the onboarding spec; marked planned until built |
| | **Health & Diagnostics** | System's disk, processes and context compression; Advanced's "This machine", work queue, provider activity; voice readiness and the privacy check link here | Health | A status page, not a settings page, and labelled so |
| | **Advanced** | Turn limits (duplicated from Models for the curious), MCP, knowledge-graph indexing (all three of today's homes), tracking debug, keystore wrap, decision backend shadow mode, Laya pilot, Google Fonts, developer flags | Advanced | Everything with a developer's name on it |
| | **About** | About: the lockup and the trademark line (shell §9), version, updates, links, acknowledgments | | |

Routines is a workspace (§2.1); the control room's Routines section becomes a link. Every
page has the same anatomy: a header with the page's one-line purpose, status blocks first
and marked "status", then sections of rows, then a danger zone where one exists. Every row
has four lines (meaning, consequence, who changed it and when, undo), a stable id
(`settings.<page>.<key>`), a search entry and a voice phrase. The search box takes a
sentence; a sentence becomes a diff and a yes.

### 2.6 What is deliberately not changed

- The hologram, the scene, the moods, the structures and the camera: the avatar session's.
- The brand tokens, the type, the glass, the triad: BRAND.md's.
- The shell's product name, one bar, fullscreen with chat, the landing cluster and its
  judge: the UI session's, already built on its branch.
- Media's segments and the move of the News shows: the Studio session's.
- Voice tools and the voice tool contract: this document adds settings rows and palette
  actions as *targets*; the tool that sets a setting (`set_setting`) is specified in the HIG
  §6.4 and built by the voice session.

## 3. Decisions taken here, and the questions for the owner

### 3.1 Decisions (engineering and IA, owned by this document; the critic checks them)

| # | Decision | Why |
|---|---|---|
| D1 | Borrow conventions, not the look: no light theme, no system font, no flat grey chrome. The HIG defines density, structure, keyboard behaviour and plain-language rows in Friday's own tokens. | BRAND.md: bars judge quality and are never a look to copy. |
| D2 | One workspace frame (sidebar, content, inspector) and one toolbar for every workspace, applied through a template, not by hand per workspace. | P2, P3, P5. |
| D3 | System becomes Activity, with Left this machine as a first-class section. | P7, P8; the north star's NS-26.22-1; the route exists and nothing calls it. |
| D4 | Trust merges into People; Family, Draft and Content retire into People, Calendar, Routines and Media; their ids stay as aliases. | §1.3; the Studio session's spec. |
| D5 | Workflows is renamed Routines. | The onboarding spec, the registry's aliases and the dock's label rule ("what people say") already use the word. |
| D6 | Settings keeps thirteen pages in four groups, with Spending separate and Notifications, Personality and Devices & Backups as new pages. | §2.5: the three lists reconcile; money gets its own door; the three new pages are rows with no home today. |
| D7 | The gear opens Settings. Quick toggles are palette actions. | P6. |
| D8 | The bar's default content is eight controls; telemetry sits behind the connection light. | §2.3; visionOS's bar: chrome when relevant. The computer-control indicator is the one exception, never hidden while live. |
| D9 | Container queries decide a workspace's density, not the viewport. | P5: the same workspace must work at 560px in a window and 1600px in a tab. |
| D10 | Prototypes use the generated `--fr-*` block byte for byte, and no colour outside it. | BRAND.md: one source of truth per medium. |

### 3.2 Questions for the owner (at most three)

1. **Family.** Retire it (members and pets to People under a Household filter, dates to
   Calendar and the landing cluster, routines to Routines), or keep a Family workspace and
   give it a real job (household members, kids' modes, shared routines, the family lane in
   Messages)? The prototype assumes retire.
2. **The business dashboard under Sites** (pipeline, revenue, legal, talent, demos). Keep it
   in the product as your own non-core workspace, named for the company and hidden on fresh
   installs, or move it out of the product into a vibe-coded bundle when the salon can host
   one? The prototype assumes the first.
3. **The bar you look at all day.** The proposal takes the clock and the four resource chips
   out of the default bar and puts them behind the connection light (one hover shows them
   all). Do you want them back as a "show telemetry in the bar" setting on by default, or
   gone by default?

## 4. The migration plan

Each piece is one commit on a branch with its before and after frames, sized for the critic.
Bars per piece are named. Nothing starts before the unified shell lands, because every piece
sits in its bar and its tab frame.

| # | Piece | Scope | Bar | Depends on |
|---|---|---|---|---|
| M0 | **The HIG and its templates as code**: `WorkspaceFrame` (sidebar, content, inspector), `Toolbar`, `EmptyState` (empty, loading, failed), `SettingsRow` (four lines), `StatusBlock`, `DangerZone`; container-query breakpoints; the palette's action registry; a structural test that every workspace mounts the frame. | New components only; no workspace moves yet. | Linear/Superhuman (one control, one look, one place); the brand check. | the shell |
| M1 | **Messages and Calendar on the frame** (the two in the prototype). Messages' four chip rows become one toolbar and a filter popover; Calendar gains the sidebar (Today, Week, Meetings, Prep). | Two workspaces. | Superhuman for Messages; macOS Calendar and Windows Mail for conventions; keyboard: j/k, Enter, Esc, Ctrl+K already there. | M0 |
| M2 | **Activity**: System becomes Activity with Now, Needs you, Receipts, Left this machine (the first UI on `routes/research.py:107`), Weekly review. One approval card everywhere; the widget bug fixed; the tray's pinned strings kept. Disk, processes, compression and the context log move to Settings (M4). | One workspace, the bell, the widget, the tab drawer. | Claude.ai's and ChatGPT's data-controls pages for Left this machine; `tasks-tray-honesty.md` for Now; the brand check for status hues. | M0; sensitive subsystem review (approvals, egress) |
| M3 | **People absorbs Trust**; Finance contacts, Health providers, Career warm leads link to People records. Trust's id becomes an alias. | Three workspaces touched, one retired. | Linear's list-and-detail; the brand check. | M0 |
| M4 | **Settings on the row contract**: the thirteen-page rail, search, four lines per row, one home per setting, danger zones, status pages labelled; the gear opens Settings; quick toggles become palette actions; the context log and telemetry arrive from System; `pause_warnings_off` and `voice_room_approvals_require_name` get their promised rows. New pages (Personality, Notifications, Devices & Backups) ship with what exists and say what is planned. | Settings, the top bar's gear. | Claude.ai and ChatGPT settings; the brand check. | M0, M2 |
| M5 | **The bar's default content** (eight controls) and the window chrome (snap, ⋯ for workspace tools). | Top bar, `FWin`. | visionOS for the desktop; Windows and macOS window conventions. | the shell's piece 2; the UI session builds it |
| M6 | **News on the frame**: editions in the sidebar, Sources in the inspector, the Shows shelf (Studio session's D1). | One workspace. | Linear/Superhuman; NPR/NYT apps for reading conventions only. | M0; the Studio session's Media piece for Shows |
| M7 | **Media** absorbs Draft and Content (Studio session's plan plus this document's Create → Text presets and Publish). | Three workspaces → one. | the Studio session's own bars. | the Studio session |
| M8 | **Routines** (rename, list-and-detail, runs) and Code (processes from System) and Sites (one job; the business dashboard to its own workspace per Q2). | Three workspaces. | Linear; GitHub Desktop and Vercel for conventions. | M0, M2 |
| M9 | **Records workspaces**: Health and Finance on the frame with a real editor each (today: "edit this JSON file"); vehicles to Finance; the wallet out; Family retired per Q1; Career's labels. | Four workspaces. | Linear; Apple Health and Copilot Money for conventions only. | M0, M3, Q1 |
| M10 | **Dock and defaults**: Life, Work, Friday; the core set; Marketplace off the dock; aliases for every retired id; voice phrases updated. | Registry, dock, settings. | the brand check (dock is a brand surface). | M1 to M9 |
| M11 | **Settings by voice**: `set_setting(path, value)` per the voice tool contract, the diff-and-yes flow, every row's phrase; the palette's settings actions. | Voice tools, settings. | the voice tool contract; Claude.ai's settings search. | M4; the voice session |

Every piece: targeted tests with `-n 0`, frames before and after at 1600 by 1000 in both
contexts, the brand guard, the product-name guard, and the structural tests the task specs
pin. The critic's bars are listed per piece above; the brand check applies to all.

## 5. Verification of this document

- Every claim about today's UI carries a line in `index.html` on main `e27ba160` or a frame
  in the capture set; the three code inventories behind it were cross-checked against the
  frames (for example, the gear's dropdown was found in code and then captured).
- The prototypes were rendered at 1600 by 1000 and looked at; their stills were sent to the
  owner with the before frames beside them.
- The prototypes contain no personal data: every name, address, message and number in them
  is fictional.
