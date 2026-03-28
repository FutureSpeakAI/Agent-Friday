# Fix Tests — Autonomous Test Repair

## Objective
Achieve 100% test pass rate. Systematically work through failing tests,
diagnosing root causes and applying minimal fixes. Never stop until all
tests pass or the cycle budget is exhausted.

## Editable Surface
- src/main/**/*.ts
- src/renderer/**/*.tsx
- tests/**/*.test.ts

## Metric
Number of failing tests — lower is better.
`npm run test 2>&1 | grep -oP '\d+ failed' | grep -oP '\d+' || echo 0`

## Loop
1. Run `npm run test` and capture full output
2. Parse the output for failing test names and error messages
3. For the FIRST failing test: read the test file and the source file it tests
4. Diagnose: is this a source bug or a stale test expectation?
5. Apply the minimal fix (prefer fixing source over test)
6. Run the specific test file to verify: `npx vitest run <test-file>`
7. Run the full suite to check for regressions
8. If total failures decreased: `git add -A && git commit -m "fix: <description>"`
9. If total failures increased or unchanged: `git checkout -- .`

## Constraints
- Never delete or skip tests (no .skip, no commenting out)
- Never weaken type checking (no `any`, no @ts-ignore)
- Never modify cLaw/integrity files
- Never install or remove dependencies
- Fix source bugs before adjusting test expectations
- One fix per cycle — small, verifiable changes only

## Budget
3 minutes per cycle, 30 cycles

## Circuit Breaker
- TypeScript compilation fails
- Test count drops (tests were deleted)
- More than 5 consecutive failures with no improvement
