# Autoresearch — Agent Friday Debug Loop

## Objective
Autonomously debug and improve Agent Friday. This is the master directive
that combines test fixing, type safety, and code quality into a single
relentless iteration loop. Like Karpathy's autoresearch running overnight
on a GPU — but for a desktop AI operating system.

NEVER STOP. Do NOT pause to ask the human. The loop runs until the human
interrupts you, period. Each cycle should take ~3 minutes. At 20 cycles
per hour, an overnight run yields ~100+ improvements.

## Editable Surface
- src/main/**/*.ts
- src/renderer/**/*.tsx
- tests/**/*.test.ts

## Metric
Combined health score — lower is better.
`npm run test 2>&1 | grep -oP '\d+ failed' | grep -oP '\d+' | head -1 || echo 0`

The primary metric is the number of failing tests. Secondary metrics
(type errors, lint warnings) are tracked but the test count drives
keep/discard decisions.

## Loop
1. Run full test suite: `npm run test 2>&1 > /tmp/friday-test-output.txt`
2. Parse output: count failures, identify first failing test
3. Read the failing test file and the source file it exercises
4. Analyze the failure: stack trace, expected vs actual, error type
5. Plan the minimal fix (source fix preferred over test adjustment)
6. Implement the fix — edit only the files needed
7. Re-run the specific test: `npx vitest run <test-file>`
8. If fixed: run full suite to check regressions
9. If regression-free improvement:
   - `git add -A`
   - `git commit -m "autoresearch: fix <test-name> — <brief description>"`
10. If regression or no improvement:
    - `git checkout -- .`
    - Log what was tried and why it failed
11. Move to next failing test. GOTO step 1.

## Constraints
- NEVER delete, skip, or comment out tests
- NEVER use `any` type or @ts-ignore
- NEVER modify cLaw/integrity/crypto files:
  - core-laws.ts, attestation-protocol.ts, memory-watchdog.ts
  - Any file in src/main/integrity/
- NEVER install or remove npm packages
- NEVER modify package.json or lock files
- One fix per cycle — atomic, verifiable changes
- Fix source bugs before adjusting test expectations
- If a test is genuinely wrong, explain WHY in the commit message
- Prefer the simplest fix that works
- Track what you've tried — don't repeat failed approaches

## Budget
3 minutes per cycle, unlimited cycles

## Circuit Breaker
- TypeScript compilation fails (`npx tsc --noEmit` exits non-zero)
- Test count drops (tests were deleted, not fixed)
- 5 consecutive cycles with zero improvement
- Build breaks: `npm run build` fails
- A cLaw-protected file is modified
