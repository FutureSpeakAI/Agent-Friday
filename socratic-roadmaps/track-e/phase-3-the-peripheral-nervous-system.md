# Phase E.3: "The Peripheral Nervous System" — Context-Aware System & Media Apps

**Track:** E — The Mesh
**Hermeneutic Focus:** Even peripheral apps benefit from knowing the whole. A system monitor that knows you're rendering video can highlight GPU usage. A weather app that knows you have an outdoor meeting can emphasize the forecast for that time. The peripheral nervous system extends awareness to the edges.

## Current State

Remaining IPC-backed apps without context: Monitor, Weather, Gallery, Media, News, Gateway, Docs, Terminal, Contacts.

## Validation Criteria

Write failing tests first, then make them pass:

1. FridayMonitor calls `useAppContext('friday-monitor')` and highlights resources relevant to active work
2. FridayWeather calls `useAppContext('friday-weather')` and shows forecast relevant to scheduled events
3. At least 4 additional apps integrate `useAppContext` following the E.1/E.2 pattern
4. All context-integrated apps degrade gracefully when context is empty
5. The 5 pure client-side apps (Calc, Camera, Canvas, Maps, Recorder) are NOT modified — they have no meaningful context integration point
6. Total useAppContext consumers reaches at least 10 apps

## Socratic Inquiry

**Constraint Discovery:** Not all apps benefit equally from context. FridayCalc is a calculator — what context could possibly help it? The peripheral nervous system should be selective: which apps genuinely benefit from context, and which are better left simple?

**Inversion:** What if we added context to EVERY app? The Calculator would show "You're working on: Sprint planning" — useless. The Camera would show briefings while you're taking a photo — distracting. Where is the line between helpful context and noise?

**Precedent:** Follow the exact visual pattern from E.1. No new UI paradigms. Context display should feel consistent across all apps.

## Boundary Constraints

- **Max new lines per app:** ~25
- **Modify:** 6-9 files in `src/renderer/components/apps/`
- **Create:** `tests/sprint-2/peripheral-app-context.test.ts`
- **Depends on:** E.1 + E.2 (pattern established)

## Files to Read

- `journals/track-e-phase-2.md` (previous phase)
- One peripheral app (e.g., `FridayMonitor.tsx`)

## Session Journal Reminder

Before closing, write `journals/track-e-phase-3.md` covering:
- Which apps were selected for context integration and why
- Which apps were deliberately excluded and why
- Final count of context-aware apps
