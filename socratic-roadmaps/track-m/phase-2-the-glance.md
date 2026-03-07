# Phase M.2 — The Glance
## ScreenContext: Screenshot Capture and UI Analysis

### Hermeneutic Focus
*The gaze can understand images, but the system has no way to look at the screen. This phase adds screen awareness — capturing what the user sees and providing that visual context to the LLM. The glance is passive perception — the system observes without acting.*

### Current State (Post-M.1)
- VisionProvider can describe images and answer visual questions
- Electron provides `desktopCapturer` for screen capture
- No screenshot infrastructure exists
- No screen-aware context exists in the LLM pipeline

### Architecture Context
```
ScreenContext (this phase)
├── captureScreen()       — Screenshot of current display
├── captureWindow(id?)    — Screenshot of specific window
├── captureRegion(rect)   — Screenshot of screen region
├── getContext()           — Latest screen description (cached)
├── startAutoCapture(interval) — Periodic capture for context
├── stopAutoCapture()     — Stop periodic capture
└── on('context-update', cb) — New screen context available
```

### Validation Criteria (Test-First)
1. `captureScreen()` returns a PNG buffer of the display
2. `captureWindow()` returns a PNG buffer of a specific window
3. `captureRegion(rect)` captures a defined rectangular area
4. Captured image is passed to VisionProvider for description
5. `getContext()` returns cached description (no redundant vision calls)
6. `startAutoCapture(30000)` captures every 30 seconds
7. `stopAutoCapture()` clears the interval
8. `context-update` event fires when new description differs from previous
9. Screen capture handles multi-monitor setups
10. All tests mock desktopCapturer and VisionProvider

### Socratic Inquiry

**Boundary:** *How often should the screen be analyzed?*
Not continuously — VLM inference takes 1-2 seconds per image. Default: on-demand (user asks "what am I looking at?"). Optional: periodic (every 30-60s) for proactive context. User-configurable.

**Inversion:** *What if the user hasn't granted screen capture permission?*
`captureScreen()` returns null. `getContext()` returns "Screen context unavailable." System operates without visual awareness. Permission requested only when user first requests screen context.

**Constraint Discovery:** *How to avoid sending sensitive screen content to cloud?*
Screen descriptions are text — they flow through the LLM pipeline which is local-first (Sprint 3). Raw screenshots never leave the machine. VisionProvider is local (Moondream). Only the text description might reach cloud via CloudGate if the LLM escalates.

**Precedent:** *How does AudioCapture handle the renderer↔main split?*
desktopCapturer runs in main process (unlike getUserMedia which is renderer). Simpler architecture — no IPC needed for capture. The image stays in main, goes to VisionProvider in main, description stays in main.

**Tension:** *Resolution vs. inference speed?*
Moondream works well with 384x384 to 768x768 images. Full 4K screenshots are wasteful. Resize to 768px on longest edge before sending to VisionProvider. This keeps inference under 2 seconds.

### Boundary Constraints
- Creates `src/main/vision/screen-context.ts` (~120-150 lines)
- Creates `tests/sprint-5/vision/screen-context.test.ts`
- Does NOT modify VisionProvider
- Does NOT handle user image input (that's M.3)
- Screenshot resolution capped at 768px for VLM efficiency

### Files to Read
1. `src/main/vision/vision-provider.ts` — Image description API
2. `src/main/window-manager.ts` — Window/display management

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-m-phase-2.md` before closing.
