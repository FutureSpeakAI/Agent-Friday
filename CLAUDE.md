# CLAUDE.md — Agent Friday Development Guide

## Project Identity
Agent Friday is a sovereign AI desktop operating system built by FutureSpeak.AI.
Electron + TypeScript + Vite. Local-first with cryptographically enforced safety (cLaws).

## Critical Commands
```bash
npm run dev            # Start dev (Vite + Electron)
npm run test           # Full test suite (vitest, ~5000 tests)
npm run test:watch     # Watch mode
npx tsc --noEmit       # Type check (strict)
npm run lint           # ESLint
npm run build          # Production build
```

## Architecture (Quick Reference)
- **Main process**: `src/main/` — 47 IPC handler modules, all singletons
- **Renderer**: `src/renderer/` — React SPA with FridayCore 3D visualization
- **IPC bridge**: `src/main/preload.ts` ↔ `src/renderer/types.d.ts` (must stay in sync)
- **Tests**: `tests/` — 141 test files, vitest, heavy use of `vi.hoisted()` mocks
- **Agents**: `src/main/agents/` — 9 builtin agents including autoresearch engines
- **Theme engine**: `src/renderer/themes/` — JSON token themes with mood modifiers, ThemeProvider context
- **Session persistence**: `src/main/session-persistence.ts` — JSONL DAG with auto-compaction
- **Cost tracking**: `src/main/cost-tracker.ts` — per-turn USD tracking, daily aggregates
- **FridayForge**: Sovereign coding environment with syntax highlighting, agent panel, cost display

## Safety: DO NOT MODIFY
These files are cLaw-protected. Never edit without explicit user instruction:
- `src/main/integrity/` — entire directory
- `src/main/core-laws.ts`, `attestation-protocol.ts`, `memory-watchdog.ts`
- `src/main/vault.ts`, `vault-crypto.ts`, `consent-gate.ts`
- CI gate: `npm run test:claw-gate` must pass

## Patterns to Follow

### IPC Handlers
All IPC handlers use `assertString()`, `assertSafePath()`, `assertObject()` from `./ipc/validate`.
File operations use `assertConfinedPath()` with `homedir()`. Never skip validation.

### Agent Definitions
```typescript
const myAgent: AgentDefinition = {
  name: 'my-agent',
  description: 'What it does',
  execute: async (input, ctx) => {
    ctx.setPhase('working');
    ctx.log('status');
    ctx.think('phase', 'reasoning');
    if (ctx.isCancelled()) return 'Cancelled';
    const result = await ctx.callClaude(prompt);
    ctx.setProgress(100);
    return result;
  },
};
```
Register in `builtin-agents.ts`, add IPC in `agent-handlers.ts`.

### Testing Patterns
- `vi.hoisted()` for mocks needed before `vi.mock()`
- `vi.clearAllMocks()` in `beforeEach` — re-set mock implementations after clear
- Confirmation gate tests must echo the `challenge` back: `handleConfirmationResponse(id, approved, challenge)`
- Provider tests mock `settingsManager` methods, not `process.env`

### Unified Tool Loop
- Tool loop uses `llmClient.complete()` for all providers — never instantiate Anthropic SDK directly
- Tool results use dual content/details pattern (truncated for LLM, full for UI)

### TUNABLE Markers
Performance-critical constants are marked with `// --- TUNABLE ---` blocks.
The autoresearch iteration engine can safely adjust values within these zones.
Files with TUNABLE zones: `memory-consolidation.ts`, `personality-calibration.ts`,
`audio-capture.ts`, `agent-runner.ts`.

## Autoresearch System
Dev directives in `dev/` define autonomous improvement loops:
- `dev/autoresearch.md` — master debug loop
- `dev/fix-tests.md`, `fix-type-errors.md` — code quality
- `dev/optimize-prompts.md`, `tune-calibration.md` — intelligence tuning
- `dev/tune-memory.md`, `optimize-voice.md` — system optimization
- `dev/optimize-context.md`, `tune-delegation.md` — efficiency

Self-improvement engines: `prompt-evolver.ts`, `model-breeder.ts`, `self-improver.ts`.

## Startup
`index.ts` uses parallel initialization batches (Promise.all) for 40+ engines.
Batch 1: sync engines. Batch 2: 26 async engines in parallel (includes costTracker, sessionManager). Batch 3: dependency chains.
Phase A completes before vault unlock. Phase B after vault passphrase.

## Common Gotchas
- `preload.ts` and `types.d.ts` must stay in sync — run `preload-type-contract.test.ts`
- `voice-pipeline-handlers.test.ts` has a hardcoded handler count — update when adding handlers
- `speech-synthesis.ts` resolves (not rejects) dropped utterances to avoid unhandled rejections
- `pageindex.ts` uses async file I/O — all callers must `await`
- Calendar OAuth listener uses `removeAllListeners('tokens')` guard against accumulation
