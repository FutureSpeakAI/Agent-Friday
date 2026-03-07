# Session Journal: Track E, Phase 3 — "The Peripheral Nervous System"

## Date
2026-03-06

## What was built
- Modified 9 peripheral apps with ContextBar import + usage
- Created `tests/sprint-2/peripheral-app-context.test.ts` (7 test cases, 23 test instances with parametric expansion)

## Apps Modified
| App | appId | Lines Changed |
|-----|-------|--------------|
| FridayMonitor | `friday-monitor` | +2 (import + JSX) |
| FridayWeather | `friday-weather` | +2 (import + JSX) |
| FridayGallery | `friday-gallery` | +2 (import + JSX) |
| FridayMedia | `friday-media` | +2 (import + JSX) |
| FridayNews | `friday-news` | +2 (import + JSX) |
| FridayGateway | `friday-gateway` | +2 (import + JSX) |
| FridayDocs | `friday-docs` | +2 (import + JSX) |
| FridayTerminal | `friday-terminal` | +2 (import + JSX) |
| FridayContacts | `friday-contacts` | +2 (import + JSX) |

## Apps Deliberately Excluded
| App | Reason |
|-----|--------|
| FridayCalc | Pure calculator — no meaningful context integration point |
| FridayCamera | Camera capture — context display would distract from viewfinder |
| FridayCanvas | Drawing canvas — full-screen creative tool, context bar would reduce canvas space |
| FridayMaps | Map viewer — context overlay would conflict with map controls |
| FridayRecorder | Audio recorder — minimal UI, context adds noise to a focused tool |

## Key Decisions

### Socratic Constraint Discovery: selective context integration
The Constraint Discovery question asked: which apps genuinely benefit from context, and which are better left simple? Answer: apps with IPC backends benefit because they can use context to filter/highlight relevant data. Pure client-side tools (Calc, Camera, Canvas, Maps, Recorder) are self-contained utilities — adding "You're working on: Sprint Planning" to a calculator is noise, not signal.

### Inversion answer: the line between helpful context and noise
Context is helpful when it can influence what the app shows (Monitor highlighting GPU during video rendering, Weather showing forecast for an outdoor meeting). Context is noise when the app's function is orthogonal to work streams (calculating 2+2, taking a photo, drawing).

### Total context-aware app count: 17
- Productivity (E.1): 4 apps (Notes, Tasks, Calendar, Files)
- Intelligence (E.2): 4 apps (Browser, Code, Forge, Comms)
- Peripheral (E.3): 9 apps (Monitor, Weather, Gallery, Media, News, Gateway, Docs, Terminal, Contacts)
- Excluded: 5 apps (Calc, Camera, Canvas, Maps, Recorder)
- Total: 17 context-aware out of 22 apps (77%)

## Test Count
- Before: 3,984 tests
- After: 4,007 tests (+23, includes parametric expansion across 9 app IDs)
- TypeScript errors: 0
