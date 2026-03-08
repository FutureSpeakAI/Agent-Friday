# Track M Phase 2: The Glance -- ScreenContext

**Date:** 2026-03-08
**Sprint:** 5
**Track:** M (Vision)
**Phase:** M.2

## What Was Built

ScreenContext singleton module that captures screenshots of the display,
specific windows, or screen regions using Electron's desktopCapturer.
Passes captured images to VisionProvider for natural language descriptions.
Caches the latest description and emits events when the screen context
changes. Supports auto-capture at configurable intervals for continuous
environmental awareness.

### Files Created

- **Implementation:** src/main/vision/screen-context.ts (~150 lines)
- **Tests:** tests/sprint-5/vision/screen-context.test.ts (10 tests)

### Public API

| Method | Description |
|--------|-------------|
| captureScreen() | Full display screenshot via desktopCapturer, returns PNG Buffer |
| captureWindow(windowId?) | Capture specific window by id, returns PNG Buffer |
| captureRegion(rect) | Capture rectangular region via crop, returns PNG Buffer |
| getContext() | Return cached screen description (no vision call) |
| startAutoCapture(ms?) | Start periodic capture (default 30s interval) |
| stopAutoCapture() | Clear the auto-capture interval |
| on(event, cb) | Subscribe to events, returns unsubscribe function |
| getInstance() / resetInstance() | Singleton lifecycle |

### Events

| Event | Payload | Fires When |
|-------|---------|------------|
| context-update | string (description) | New description differs from previous |

### Architecture Decisions

- **Electron desktopCapturer for screen capture**: Uses the main-process
  desktopCapturer API with types: ['screen'] or ['window'] and a
  768x768 thumbnail size cap for VLM efficiency.

- **Multi-monitor support**: Queries screen.getPrimaryDisplay() to find
  the primary display id, then matches against desktopCapturer source
  display_id. Falls back to first source if no match.

- **Region capture via NativeImage.crop()**: Captures full screen first,
  then crops the NativeImage to the requested rectangle before converting
  to PNG buffer.

- **VisionProvider integration**: After every capture, if VisionProvider
  is ready, passes the PNG buffer to visionProvider.describe(). Caches
  the result in lastContext. Only emits context-update if the new
  description differs from the previous one.

- **Cached context pattern**: getContext() returns lastContext without
  triggering any capture or vision call. This avoids redundant API calls
  when the agent needs the current screen description.

- **Auto-capture via setInterval**: startAutoCapture() creates a
  setInterval that calls captureScreen() on each tick. stopAutoCapture()
  clears it. No immediate capture on start (first tick fires after the
  interval elapses).

- **Same event emitter pattern**: Uses Map<event, Set<callback>> with
  unsubscribe-returning on() method, consistent with AudioCapture and
  other modules in the project.

- **Graceful error handling**: All capture methods catch errors and
  return null. Vision describe failures are silently caught to preserve
  the previous context.

### Validation Results

All 10 tests pass:
1. captureScreen() returns a PNG buffer of the display
2. captureWindow() returns a PNG buffer of a specific window
3. captureRegion(rect) captures a defined rectangular area
4. Captured image is passed to VisionProvider for description
5. getContext() returns cached description (no redundant vision calls)
6. startAutoCapture(30000) captures every 30 seconds
7. stopAutoCapture() clears the interval
8. context-update event fires when new description differs from previous
9. Screen capture handles multi-monitor setups (returns primary display)
10. Singleton pattern and mocks work correctly

### Safety Gate

- npx tsc --noEmit: 0 errors
- npx vitest run: 114 test files, 4185 tests passed, 0 failures
