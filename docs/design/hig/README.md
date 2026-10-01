# Agent Friday™ Human Interface Guidelines

> **Status:** proposed, with [`surface-reorg.md`](../active/surface-reorg.md) (the audit
> that motivates it and the migration that applies it) and the clickable prototypes in
> [`docs/design/prototypes/surface-reorg/`](../prototypes/surface-reorg/index.html).
> **Last verified:** 2026-09-30 against BRAND.md on `piece/P-BRAND-0` and
> `unified-shell.md` on `feat/unified-shell`.
> **Relationship to BRAND.md:** BRAND.md owns the look (palette, type, glass, the mark, the
> hologram, the voice). This document owns the structure: how surfaces are laid out, how
> they behave, what every workspace and every settings row must have, and what the critic
> measures them against. Where the two seem to disagree, BRAND.md wins and this document is
> corrected.

## 0. The principle

Borrow the conventions of professional software; keep Friday's own look. The bars (Linear,
Superhuman, Claude.ai's and ChatGPT's settings, visionOS, Windows and macOS) judge quality:
structure, predictability, density, keyboard behaviour, plain language. They are never a
look to copy. Cyan is the brand, glass is the panel, the hologram is her face, and nothing in
this document changes a colour, a face or a shader.

Three layers, one system:

| Layer | What it is | Its bar | Its rule |
|---|---|---|---|
| 1. The holographic desktop | Identity and ambience: the scene, the landing cluster, the dock, the top bar. | visionOS | Minimal chrome; only relevant things, at relevant moments; the content floats on the identity layer and never hides it. |
| 2. Workspaces | Proper app windows: one job each, one frame, one toolbar, keyboard first. | Linear, Superhuman, plus OS window conventions | Dense, calm, predictable; the same frame everywhere; every action in the palette. |
| 3. System surfaces | Settings, approvals and cards, notifications, the ledger of what left the machine. | Claude.ai and ChatGPT settings | Calm lists; plain-language rows with meaning and consequence; search; one home per setting; every setting callable by voice. |

## 1. Vocabulary

- **Surface**: anything the user looks at: the desktop, a workspace, a panel, a card.
- **Workspace**: a registry entry (`static/workspace_registry.js`) with one job, rendered in
  a window on the desktop or in its own tab at `/w/<id>`.
- **Frame**: the workspace template: header, toolbar, sidebar, content, inspector.
- **Section**: an entry in a workspace's sidebar; the unit of navigation inside a workspace.
- **Row**: a settings control with its four lines (§6.1).
- **Card**: a bounded piece of content with its own actions (an approval, a creation, a
  receipt). A card is content; a panel is chrome.
- **Needs you**: anything waiting on the user's decision. Amber, always with a word.
- **Status page**: a page that shows state and changes nothing. Labelled as such.

## 2. Tokens and what they may do

Only the generated `--fr-*` block (BRAND.md; `brand.css_root_block()`) names a colour, a
face or a size. The HIG adds no colour. It adds a small set of structural tokens that a
template reads:

| Token | Value | Use |
|---|---|---|
| `--fr-rail-w` | 184px | The width of a sidebar or a settings rail at full density. |
| `--fr-rail-w-narrow` | 48px | The sidebar collapsed to icons. |
| `--fr-inspector-w` | 320px | The inspector. |
| `--fr-row-h` | 32px | The height of a list row at full density. |
| `--fr-row-h-dense` | 28px | A list row in a narrow container. |
| `--fr-toolbar-h` | 40px | One toolbar. |
| `--fr-header-h` | 48px | The workspace header. |
| `--fr-gap` | 8px | The unit. Paddings and gaps are multiples of it. |
| `--fr-radius` | 10px | Panels and cards. Controls use 6px. |
| `--fr-reveal` | `0.35s cubic-bezier(0.2, 0.8, 0.3, 1)` | The one reveal timing (the shell's §10.3). |
| `--fr-focus` | `0 0 0 2px var(--fr-cyan-soft), 0 0 0 1px var(--fr-cyan)` | The focus ring. Every focusable thing shows it. |

Status is carried by a reserved hue *and* a word or an icon (BRAND.md's semantic rule). A
template never puts a status hue on decoration: a selected tab is `--fr-cyan`, a destructive
action is `--fr-error`, a refusal is `--fr-deny`, and "needs you" is `--fr-warn` with the
words "needs you" or a count.

## 3. Layer 1: the holographic desktop

1. **The scene is the ground.** Nothing opaque covers it by default. Panels are glass
   (`--fr-glass`, `--fr-glass-blur`, `--fr-glass-edge`).
2. **Chrome appears when relevant.** The top bar is always present (the shell's piece 2);
   the dock hides after idle and returns at the bottom edge; the landing cluster shows when
   the shell's judge says so (§10.2). Nothing else is on the desktop until the user opens it.
3. **The top bar carries eight controls** (`surface-reorg.md` §2.3): lockup, context slot,
   model, needs-you pill (only when something is waiting), search, chat, settings,
   connection light. Telemetry (clock, GPU, RAM, CPU, disk, seat held) is one hover or click
   away behind the connection light. The one exception to "hide what is not relevant" is a
   live-danger indicator (computer control active): it is never hidden while live.
4. **The dock is a launcher**, three groups (Life, Work, Friday), one row, labels under
   icons, a dot for an open window, a badge only for "needs you" counts. Ctrl-click opens a
   tab. The default dock is the core set; the rest is opt-in in Settings.
5. **Motion only on a real event.** A window opens from and folds into its dock icon; the
   cluster fades with `--fr-reveal`; nothing idles, pulses or shimmers to look busy
   (the avatar rule applies to chrome too).
6. **Windows are windows.** Title, maximize, snap left and right, open in a tab, close.
   Double-click the title to maximize. Geometry and sidebar state are remembered per
   workspace. The window's title bar holds the OS's controls; the workspace's own tools live
   in its header (§4.2).

## 4. Layer 2: the workspace frame

### 4.1 Anatomy

```
┌ title bar (window only: icon · name · ⤢ snap ⧉ tab ✕) ──────────────────┐
│ header: [icon] Name · one-line status            [toolbar actions] [⋯] │
├──────────┬───────────────────────────────────────────────┬─────────────┤
│ sidebar  │ content                                       │ inspector   │
│ sections │ list, list+detail, calendar, page, canvas     │ (optional)  │
│ …        │                                               │ details of  │
│          │                                               │ the selected│
│ footer   │                                               │ item, or    │
│ counts   │                                               │ sources     │
└──────────┴───────────────────────────────────────────────┴─────────────┘
```

- **Header** (`--fr-header-h`): the workspace icon and name (Inter 600, `--fr-text-lg`),
  then its one-line status in `--fr-dim` (never italic, never a glyph: "158 conversations ·
  2 need you"), then the toolbar at the right, then ⋯.
- **Toolbar** (`--fr-toolbar-h`): at most four visible actions, the primary one first and
  `.btn.active`-styled only when it is a toggle that is on. Everything else is in ⋯ and in
  the palette. A segmented control (at most four segments) may sit in the toolbar when the
  content has modes (Calendar: Day, Week); views that are *places* go in the sidebar instead.
- **Sidebar** (`--fr-rail-w`): the workspace's sections, each with a count where one is
  meaningful, grouped with small caps labels (`--fr-track-label`, `--fr-text-2xs`). It
  collapses to icons at `--fr-rail-w-narrow` and remembers its state. The footer holds the
  workspace's totals or its connector state.
- **Content**: one of five patterns: list; list and detail (the default for records: mail,
  people, routines, receipts); calendar; page (a reading view with a measure of 72ch);
  canvas (the graph, the gallery). A pattern is chosen per section, not per workspace.
- **Inspector** (`--fr-inspector-w`): the selected item's details, or the section's
  sources and settings (News → Sources). Optional, toggled from the toolbar, remembered.
- **⋯ menu**: the workspace's own tools in a fixed order: Open in tab, Pop out, Fullscreen
  with chat, a separator, Chat about this, Talk about this, Earlier versions, Friday's
  changes to this workspace, a separator, Customize…, then the section's extra actions.

### 4.2 The header replaces the 💡 banner

The status line is a sentence of state in `--fr-dim`. It never carries a glyph, an
exclamation, a tip or italics. It may carry one count in `--fr-warn` with the words "need
you". If a workspace needs setup, that is an empty state in the content (§5), not a card
above it.

### 4.3 One toolbar

A visible action is a verb the user does often. Four at most. Ranked: the primary action
(Compose, New page, Make episode), the refresh if the data can go stale, a view toggle if
the content has modes, the inspector toggle. Destructive actions are never in the toolbar;
they live on the item (row menu, inspector) and in ⋯, styled `--fr-error`, with a
confirmation that names the thing.

### 4.4 Lists

- Row height `--fr-row-h`, `--fr-text-md` body, `--fr-text-sm` meta, one line each; a
  second line only in a list-and-detail list.
- Selection is `--fr-cyan-soft` ground with a 2px `--fr-cyan` left edge; hover is a lighter
  ground. Focus is the ring.
- Keys: ↑ ↓ or j k move, Enter opens, Esc backs out, Space previews where a preview exists,
  x selects, Shift-click ranges. The same keys in every list.
- A row's actions appear on hover and focus at the right, and in the row's context menu.
- Counts are right-aligned in `--fr-font-mono` so columns of numbers line up.

### 4.5 Reading views

A page (an article, an editorial, a wiki page, a receipt) sets a 72ch measure, `--fr-text-base`
body, Inter, 1.55 line height, headings in Inter 600 (Orbitron is display only: the
greeting, the wordmark, state chips, card titles).

### 4.6 Density by container, not by viewport

A workspace is the same code in a 560px window and a 1600px tab. The frame uses container
queries on the workspace root:

| Width | Sidebar | Toolbar | Inspector | Rows |
|---|---|---|---|---|
| ≥ 1100px | open | all four actions | can be open | `--fr-row-h` |
| 720 to 1099px | open, collapsible | three actions, rest in ⋯ | overlays the content when opened | `--fr-row-h` |
| < 720px | icons | primary action only, rest in ⋯ | sheet | `--fr-row-h-dense` |

No workspace sets its own breakpoints.

### 4.7 Pop-out and tabs

Every workspace opens in a tab (the shell: `/w/<id>`, one bar everywhere). A tab's header is
the same frame header; the bar's context slot shows the workspace. Settings opens in a tab
too (today it refuses: `index.html:8641`). The palette's "Open <workspace> in a tab" and
Ctrl-click on the dock do the same thing.

## 5. States

One component, three modes, one action, in the content area, centred, never above it:

| Mode | Line 1 (Inter 500, `--fr-text`) | Line 2 (`--fr-dim`) | Action |
|---|---|---|---|
| Empty | What there is none of, plainly: "No episodes yet." | What makes one appear: "Every News routine run makes one, and you can make your own." | The primary action, if one applies |
| Loading | What is being read: "Reading your front page…" | Omitted, or the source | None; never a count ("0 pages" while reading is the Knowledge bug the audit fixed) |
| Failed | What failed, as a fact: "Couldn't read mail." | The cause in one line, if known: "Gmail refused the token." | Try again, and Open Connectors when the cause is a connection |

A failure is never shown as zero, as an empty list, or as nothing. A skeleton may stand in
for a list while loading, in `--fr-glass-edge` blocks, with no shimmer.

## 6. Layer 3: system surfaces

### 6.1 The settings row contract

Every setting is one row with four lines and a control. The control sits at the right
(toggle, select, segmented, field, button); the lines at the left:

1. **Label** (Inter 500, `--fr-text`): a noun phrase in plain words. "Keep vault content off the cloud."
2. **Meaning** (`--fr-dim`, `--fr-text-md`): what it does, in one sentence a journalist would write.
3. **Consequence** (`--fr-label`, `--fr-text-md`): what changes if you change it, including money and what stops working. A row without a consequence sentence does not ship.
4. **Provenance and undo** (`--fr-faint`, `--fr-text-sm`, `--fr-font-mono` for the time): who last changed it and when (you, Friday by a proposal you accepted, a mode, a default, a reset), and an Undo link that holds for thirty days.

Rows are grouped in sections with a sentence-case title. Section titles are never all caps.
A page's header has the page's one-line purpose. Status blocks (readiness, health, usage)
come first on a page, carry a "status" tag, and change nothing. A danger zone, when a page
has one, is the last section, titled "Danger zone", with `--fr-error` buttons that each
confirm by naming what they destroy.

### 6.2 Plain words

- Rows never show an id, a model string, a file extension or a protocol name as the label.
  The id belongs in a tooltip or an inspector in `--fr-font-mono`.
- Tabs and sections are words, not keys ("Quick reference", not "quickref").
- The second person throughout, in Friday's voice (BRAND.md): "what leaves this machine",
  not "egress"; "needs you", not "pending approval".
- One home per setting. Another surface may *link* to it ("set in Settings → Privacy &
  Data") but never shows a second control for it.

### 6.3 Search, and saying it

Settings has one search box at the top of the rail. It matches labels, meanings and
phrases, and it jumps to the row. A sentence typed or spoken into it ("stop reading my work
email after 7") becomes a diff: the rows it touches, old and new values, the consequence
sentences, and a Yes. Nothing applies before the Yes (the onboarding spec's §9.3).

### 6.4 Voice-callable, by construction

Every row has a stable path (`settings.<page>.<key>`) and at least one phrase. The voice
tool `set_setting(path, value)` (per the voice tool contract: declared, governed, internal
ring, says SETTING_SET or SETTING_NEEDS_YES with the diff) sets exactly one row, and the
page shows the same diff the search box shows. A conditional spoken yes never applies a
change; it becomes a revised diff.

### 6.5 Approvals and cards

- One card component (`ApprovalCardBody`) everywhere a card appears; no surface draws its
  own.
- Anatomy: the title (what Friday wants to do), the exact thing that would leave the machine
  or change (with "see exact text"), provenance (where the request came from; taint flags),
  the kind and policy in `--fr-dim`, the expiry as a time, then the buttons: Approve
  (`--fr-ok`), Change (plain), Deny (`--fr-deny`). "Later" folds, never dismisses.
- One queue: Activity → Needs you. One summons: the bar's amber pill. The card also appears
  in chat when the request came from the conversation.

### 6.6 Notifications and toasts

- The bell lists notifications and a two-line Now summary; each entry has one deep link.
- A toast is one line, bottom right, four seconds (fifteen for a failure), one implementation.
- OS notifications carry the product name and one sentence.

### 6.7 The ledger of what left the machine

A list in Activity, one row per cloud send: when, to whom (the provider and the model),
what (a short description, with "see exact text"), what was scrubbed, why (the routine, the
ask, the approval id), and the day's holds. The egress gate's layer status heads the page;
when a layer is down the page says that cloud calls are held. Nothing on this page is
sampled or summarised by a model.

## 7. Chat and voice surfaces

- One chat surface in three containers (tray, window, tab); the composer has input, mic,
  attach and send; cite-sources, show-sources and the audio-device picker are in its ⋯.
- The voice state chip reads as BRAND.md's reserved hues: listening cyan, thinking violet,
  speaking green, needs-you amber, failed red. Motion only while she speaks or works.
- The landing cluster is the shell's (§10); it is the only chrome on the desktop with a
  chat field.

## 8. Keyboard and the command palette

- **Ctrl+K / Cmd+K** opens the palette from anywhere. It lists, in order: actions of the
  focused workspace, workspaces ("Open News", "Open News in a tab"), settings rows (by label
  and phrase), approvals ("Approve: send the follow-up…"), people and routines after
  typing. Every toolbar and ⋯ action registers itself; nothing is reachable only by mouse.
- **Global**: Ctrl+K palette · Ctrl+/ chat field (the shell) · Ctrl+Shift+Space voice (the
  shell) · Ctrl+Shift+F fullscreen with chat (the shell) · Ctrl+, Settings · Esc closes
  the topmost panel · F9 condensed · Ctrl+Shift+Q stop computer control.
- **Lists** (§4.4) share one key map. **Workspaces** may add keys only for their own content
  (Messages: e archive, r reply) and must list them in ⋯ → Keyboard shortcuts.
- A shortcuts sheet (? or Ctrl+/ twice) lists everything, generated from the registry of
  actions, never hand-written.

## 9. Accessibility and performance budgets

- Focus is visible everywhere (`--fr-focus`). Tab order follows reading order. Every control
  has a name. Icon-only buttons have a tooltip and an `aria-label`.
- Minimum target 32px in lists, 44px in the dock and on touch.
- Reduced motion turns the reveal to an instant and stills the scene's chrome (the scene
  itself follows the avatar spec's 3D-off switch).
- Text size is a setting (Appearance & Hologram); the type scale is BRAND.md's; density
  does not drop below `--fr-row-h-dense`.
- Budgets, measured and shown in Health: the shell interactive under 3 s; an approval card
  rendered under 300 ms; a workspace's first list under 1 s from cache, 2 s from the source.

## 10. The critic's checklists

**Any workspace.** Mounts the frame · header with name and a plain status line · at most
four toolbar actions · sections in the sidebar, no row of tab buttons · one of the five
content patterns per section · the three states from the one component · a failure never
shown as zero · container-query density · every action in the palette and in ⋯ · the list
keys · no status hue on decoration · no glyph in headings · no raw id in a label · opens in
a tab with the same header.

**Settings.** Grouped rail · search that jumps to a row · every row with four lines ·
consequence present · provenance and undo present · status blocks first and tagged · one
home per setting · danger zone last · sentence-case titles · every row has a path and a
phrase · the diff-and-yes flow for a typed or spoken sentence · nothing but `--fr-*` colours.

**Desktop.** Eight bar controls · telemetry behind the connection light · the needs-you pill
only when something waits · dock in three groups with the core set · motion only on an
event · windows snap, maximize, open in a tab · the scene uncovered by default.

**Brand.** Only tokens · Orbitron only for display · amber only for needs-you and the maker's
name in the wordmark · deny magenta only for a refusal or a stop · error red only for a
failure or a destructive action · glass panels · the hologram untouched.
