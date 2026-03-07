# Phase E.2: "The Synapses" — Context-Aware Intelligence Apps

**Track:** E — The Mesh
**Hermeneutic Focus:** Intelligence apps (Browser, Code, Forge, Comms) are where the system's understanding becomes most visible. These apps don't just show data — they help the user think. Context injection makes them proactive: the browser suggests searches based on work context, the code editor highlights relevant files, the forge recommends capabilities.

## Current State

Four intelligence apps have IPC backends but no context awareness:
- FridayBrowser: Uses `window.eve.browser.*` and `window.eve.mcp.*`
- FridayCode: Uses `window.eve.gitLoader` and `window.eve.container`
- FridayForge: Uses `window.eve.ecosystem.*`, `window.eve.superpowers.*`
- FridayComms: Uses `window.eve.inbox.*`, `window.eve.outbound.*`

## Validation Criteria

Write failing tests first, then make them pass:

1. FridayBrowser calls `useAppContext('friday-browser')` and shows context-relevant search suggestions
2. FridayCode calls `useAppContext('friday-code')` and highlights files related to active work stream
3. FridayForge calls `useAppContext('friday-forge')` and surfaces capabilities related to current task
4. FridayComms calls `useAppContext('friday-comms')` and prioritizes messages related to active work
5. Each app's context section follows the same visual pattern established in E.1
6. Context-derived suggestions are clearly labeled as AI-suggested (not user-created)
7. Users can dismiss or hide the context section per-app

## Socratic Inquiry

**Synthesis:** Intelligence apps are where parts (individual app data) and whole (system context) most visibly merge. How does the browser's search history combine with the work stream context? Does the code editor's file list reorder based on context relevance?

**Boundary:** Context-driven suggestions could be wrong. A suggestion to search for "React hooks" when the user is debugging a Python script would erode trust. How does each app validate context relevance before displaying suggestions?

**Tension:** Comms might surface a "high priority" message based on context, but the user might consider it spam. The system's priority model may diverge from the user's. How is this tension resolved?

**Safety Gate:** Adding `useAppContext` to apps that already have complex state (FridayComms with inbox + outbound + conversations) — does this introduce performance regressions? Profile re-render frequency.

## Boundary Constraints

- **Max new lines per app:** ~35
- **Modify:** 4 files in `src/renderer/components/apps/`
- **Create:** `tests/sprint-2/intelligence-app-context.test.ts`
- **Depends on:** E.1 (establishes the visual pattern for context display)

## Files to Read

- `journals/track-e-phase-1.md` (previous phase — visual pattern established)
- `contracts/live-context-bridge.md`
- One intelligence app (e.g., `FridayBrowser.tsx`) for state complexity

## Session Journal Reminder

Before closing, write `journals/track-e-phase-2.md` covering:
- How context suggestions are generated per-app
- The labeling strategy for AI-suggested vs user-created content
- Performance observations from adding context to complex apps
