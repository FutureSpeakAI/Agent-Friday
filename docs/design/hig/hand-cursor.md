# HIG chapter: the hand cursor and big mode

> **Status:** in build on `feat/hand-cursor` (Layer 1 first, then Layer 2), owner-approved
> 2026-10-02. Part of the [Agent Friday™ Human Interface Guidelines](README.md).
> **Builds on:** the existing hand and head tracking (MediaPipe hands and face, the pinch
> click, the "Minority Report hands" toggle in the scene menu, the `tracking.*` settings), the
> unified shell, and the HIG's target sizes (§9). It forks none of them.
> **Owned elsewhere:** the avatar scene and the orb layer (the avatar session): orbs are
> targets in this registry, and their own gravity and click behaviour are unchanged.

## 0. What the owner asked

"We also need a 'Minority Report mode' that supports the hand tracking and pinch clicking,
since most of our buttons are small it can be hard to click. Maybe a snap-to like feature for
the hand cursor? Maybe a switch to an easier interface with larger buttons/cards?"

Both, in two layers. **Layer 1** makes the hand cursor steady and smart on every surface, so
the interface we have becomes usable by hand without changing it. **Layer 2** is big mode: a
large-target layout for the surfaces people drive by hand most, on when tracking is on.

## 1. Layer 1: the hand cursor

The tracker gives a noisy point about thirty times a second. The cursor layer turns it into a
point that lands where the user means. Every step is a pure function in
`static/hand_cursor_core.js`, tested in node; the DOM layer only draws and dispatches.

### 1.1 Smoothing

A One Euro filter on each axis (min cutoff 1.0 Hz, beta 0.02, derivative cutoff 1.0 Hz): still
when the hand is still, quick when the hand moves. While the cursor is locked on a target the
min cutoff drops to 0.6 Hz for extra steadiness; it rises again on release. No moving
average, no fixed delay.

### 1.2 Magnetic snap with hysteresis

- **Targets** come from one registry (§1.6). A target is a rect on screen with an id, a
  weight and a guarded flag.
- **Engage** when the cursor is within 40 px of a target's *edge* (not its centre, so a large
  card does not out-pull the small button sitting on it; on a near tie the smaller target
  wins). **Release** only when the cursor is more than 64 px from the locked target's edge.
  The gap between the two is the hysteresis: a cursor wandering between two neighbours stays
  on the one it has.
- **Friction** while locked: the drawn point is pulled 35 % of the way toward the target's
  centre each frame and the reticle itself sits on the target, so small motions of the hand
  do not move the aim.
- **Highlight:** the locked target gets `.fr-snap` (a 2 px `--fr-cyan` ring at 60 % with a soft
  outer glow, scale 1.02) so the user knows exactly what a pinch will hit. The ring fades in
  and out over 350 ms; it never blinks.
- In big mode both radii scale by 1.5.

### 1.3 Pinch freeze

The pinch gesture moves the fingertip the tracker follows, which is exactly what used to drag
the cursor off a 24 px button. At pinch onset (strength above 0.6) the cursor **freezes** at
the snapped point; it stays frozen until release (strength below 0.4; the gap keeps a
wavering pinch from double-firing). A release without movement past 24 px is a **click at
the frozen point**. A pinch that moves past 24 px is a **drag** (§1.5), and its release is
not a click.

### 1.4 Dwell to click

An alternative to the pinch, for hands that tire or trackers that lose the thumb: hover a
locked target for 650 ms and it clicks. A ring around the reticle fills over the dwell; leaving
the target empties it; the same target cannot dwell-click twice without the cursor leaving it.
Pinch and dwell are the existing "Click method" setting in Settings → Voice & Tracking; the
dwell time is its existing slider.

### 1.5 Pinch-drag and two-hand zoom

- A pinch that moves scrolls the nearest scrollable ancestor under the frozen point (lists,
  reading views, the tray) and moves a card that declares `data-fr-draggable` (the Pipeline
  board, windows by their title bar).
- Two pinching hands zoom where zoom means something: the galaxy, the 3D views, an image in
  the Media viewer. The layer emits `friday:hand-zoom` with the scale; a surface that does not
  listen is unaffected.

### 1.6 One target registry

Every actionable thing registers once, automatically: buttons, links, inputs, `[role=button]`,
`[role=option]`, `[role=menuitem]`, `[role=tab]`, dock entries, cards with a click handler
(`[data-fr-target]`), and orbs (reported by the scene's own hook with their screen rects and
weight 1.4 so a small orb is easy to reach). Rects refresh on scroll, resize and mutation;
hidden, disabled and `inert` elements leave the registry. An element can opt out with
`data-fr-target="off"`; a container can declare `data-fr-guarded` or a button can be
classified guarded by its action (send, post, publish, delete, erase, pay, buy, approve).

**The discovery test** walks each workspace's DOM in a browser, finds everything that is
focusable or has a click handler or an interactive role, and fails when the registry does
not hold it. Snap therefore works everywhere with no per-screen code, and a new workspace
cannot ship a target the cursor cannot reach.

### 1.7 The reticle

An abstract glowing ring in `--fr-cyan`: 18 px, 1.5 px stroke, a soft outer glow; never a
hand, an arrow or a face (BRAND.md; the avatar rule against anthropomorphising applies to
every representation of her and of the user's hand alike). States, each a 350 ms opacity or
scale transition and nothing else:

| State | Reticle |
|---|---|
| Free | ring at 70 % |
| Locked | ring at 100 %, a second thin ring expands to the target's outline |
| Pinched (frozen) | ring contracts to 14 px and holds |
| Dwell or hold in progress | an arc fills clockwise around the ring |
| Guarded hold complete | the arc closes, the ring brightens once |

Photosensitivity: at most one visual state change per 334 ms (a limiter in the core), opacity
and scale only, no colour strobing, no flashing. The scene's own flash limits are untouched.

### 1.8 Orbs

Orbs keep their gravity and their click. The registry holds each orb's rect; snapping to an
orb hands the pointer to the orb's own hover and click handlers, so from the orb layer's
point of view nothing changed but a steadier pointer. The reticle never draws over an orb's
label.

## 2. Layer 2: big mode

### 2.1 When it is on

Big mode turns on when hand tracking starts and off when it stops, unless the user has
chosen otherwise. "Big mode" and "big mode off" by voice, a control in the scene menu and the
palette, and the setting `big_mode` (auto, the default; on; off) all set the same thing. The
choice is remembered.

### 2.2 The layout contract

`body.fr-big` is the only switch; every surface reads it through the frame, never through
per-screen code:

- Every target at least 72 px on its short side; primaries 96 px; the dock 88 px.
- Fewer items per screen: lists show one line per row at 56 px rows, grids drop to two or
  three columns, text at `--fr-text-lg`.
- Primary actions are big cards; everything else is behind one "More" card that opens the
  ⋯ menu as a card grid.
- Spacing doubles (`--fr-gap` 16 px); nothing sits within 16 px of a screen edge.
- The snap radii scale 1.5; the reticle grows to 24 px.

### 2.3 Rollout

Home (the landing cluster and the dock), chat (the composer and the message list), News (the
front page cards and the toolbar), the notification tray, then the rest through the frame.

### 2.4 Safety

In big mode anything that sends, deletes, spends, publishes or approves needs a
**pinch-and-hold** (700 ms; the arc fills; releasing early cancels) or a spoken "yes". A
stray pinch cannot fire it. Approval gates are unchanged: a guarded action that already
raises a card still raises it; the hold only replaces the click, never the card. Nothing in
this chapter adds a bypass.

### 2.5 Voice parity

"Open News" (the existing `navigate_to`), "next card", "select", "back", "big mode", "big
mode off": the last five are two tools per the voice tool contract, `big_mode(on|off)` and
`hand_cursor(next|select|back)`, internal, ring 1, governed, with their own answers
(`BIG_MODE_ON`, `BIG_MODE_OFF`, `CURSOR_MOVED`, `CURSOR_SELECTED`, `CURSOR_NO_TARGET`).
"Select" on a guarded target answers with the card, never with the action.

## 3. Settings rows (Voice & Tracking → Hand cursor)

| Row | Key | Default |
|---|---|---|
| Click method | `tracking.click_method` (exists) | pinch |
| Dwell time | `tracking.dwell_ms` (exists) | 650 |
| Snap to targets | `tracking.snap` | on |
| Snap reach | `tracking.snap_radius` | 40 px (engage; release is 1.6×) |
| Big mode | `big_mode` | auto |

`tracking` is written whole (it is not deep-merged).

## 4. Verification

- `tests/unit/test_hand_cursor_core.py`: node runs the core's cases (hysteresis, no flicker,
  tie to the smaller target, pinch freeze at onset, drag past the slop, guarded hold, dwell
  once per visit, One Euro jitter and lag, zoom, the change limiter). Shown red on a broken
  hysteresis and a broken freeze, then green.
- A registry discovery spec (Playwright, scratch server, `FRIDAY_BASE` set): every workspace.
- Frames, looked at: snap engaged on a small button, pinch freeze during a pinch, before and
  after big mode on each rolled-out surface.
- The critic's checklist: reticle abstract · lock visible · no flicker between neighbours ·
  click lands at the onset point · guarded actions hold · orbs unchanged · ≤ 3 state changes
  per second · brand tokens only · big mode targets ≥ 72 px · voice phrases answer per the
  contract.
