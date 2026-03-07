# Phase E.1: "The Nerve Endings" — Context-Aware Productivity Apps

**Track:** E — The Mesh
**Hermeneutic Focus:** Apps are the user's window into the system's understanding. Without context injection, each app is an island — it knows only what the user explicitly tells it. The nerve endings connect apps to the system's intelligence, so Notes knows what you're working on, Tasks knows what's urgent, and Calendar knows what's relevant.

## Current State

Four core productivity apps (Notes, Tasks, Calendar, Files) have full IPC backends via `window.eve.*` but none use `useAppContext()`. The hook exists (`src/renderer/hooks/useAppContext.ts`) but has zero consumers. Each app manages its own state with `useState`/`useRef`.

## Architecture Context

```
Current:   App → useState → window.eve.* → IPC → main process (per-app silo)

Target:    App → useAppContext(appId)  → enriched { context, briefings, entities }
               → useState              → app-specific local state
               → window.eve.*          → IPC backend calls
           All three data sources compose into the component's view.
```

## Validation Criteria

Write failing tests first, then make them pass:

1. FridayNotes calls `useAppContext('friday-notes')` and receives enriched context
2. FridayTasks calls `useAppContext('friday-tasks')` and receives enriched context
3. FridayCalendar calls `useAppContext('friday-calendar')` and receives enriched context
4. FridayFiles calls `useAppContext('friday-files')` and receives enriched context
5. Each app renders a context-aware section showing active work stream name (if any)
6. Each app renders relevant briefing summaries (if any) in a dismissible panel
7. Context updates re-render only the context section, not the entire app
8. Apps degrade gracefully when context is empty (no work stream, no briefings)

## Socratic Inquiry

**Boundary:** How much context should an app display? A full briefing panel? A subtle badge? A sidebar? The user shouldn't feel overwhelmed. What's the minimum visible surface that makes context feel *useful* without being noisy?

**Precedent:** How do the apps currently render their headers/toolbars? Add context display in the existing layout pattern — don't create a new layout paradigm.

**Tension:** Notes might show "You're working on: Sprint 2 planning" while the user is actually writing a grocery list. How does the system handle context that's wrong or irrelevant? Allow dismissal? Let the user override?

**Constraint Discovery:** `useAppContext` returns `{ context, loading, error }`. The `context` includes `activeStream`, `briefings`, `entities`. But rendering all of this would bloat each app. What's the minimal subset each app should show?

**Inversion:** What if apps consumed context but never displayed it? The system would track everything invisibly. That's creepy. What if they displayed everything? That's overwhelming. The nerve endings should surface just enough to feel helpful.

## Boundary Constraints

- **Max new lines per app:** ~30 (hook call + context display section)
- **Modify:** 4 files in `src/renderer/components/apps/`
- **Create:** `tests/sprint-2/app-context-integration.test.ts`
- **Import:** `useAppContext` from `src/renderer/hooks/useAppContext`
- **Depends on:** D.1 + D.2 (bridge started, handler registered)

## Files to Read

- `journals/track-d-phase-3.md` (previous phase journal)
- `contracts/live-context-bridge.md` (AppContext shape)
- `src/renderer/hooks/useAppContext.ts` (hook API)
- One sample app (e.g., `FridayNotes.tsx`) for layout patterns

## Session Journal Reminder

Before closing, write `journals/track-e-phase-1.md` covering:
- Which apps were modified and what context they display
- The UI pattern chosen for context display
- Graceful degradation behavior when context is empty
