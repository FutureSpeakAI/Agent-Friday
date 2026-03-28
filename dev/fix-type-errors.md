# Fix Type Errors — TypeScript Strict Mode Compliance

## Objective
Achieve zero TypeScript compilation errors. Work through type errors
systematically, strengthening type safety rather than weakening it.

## Editable Surface
- src/main/**/*.ts
- src/renderer/**/*.tsx

## Metric
Number of TypeScript errors — lower is better.
`npx tsc --noEmit 2>&1 | grep -c 'error TS' || echo 0`

## Loop
1. Run `npx tsc --noEmit` and capture all errors
2. Group errors by file — start with the file that has the most errors
3. Read the file and understand the type relationships
4. Fix the type error properly (add types, fix interfaces, correct logic)
5. Re-run `npx tsc --noEmit` to count remaining errors
6. If error count decreased: commit
7. If error count increased or unchanged: revert

## Constraints
- NEVER use `any` type — find the correct type or use `unknown` with guards
- NEVER use @ts-ignore or @ts-expect-error
- NEVER weaken existing type definitions
- Fix the type, don't suppress the error
- Maintain backward compatibility of exported interfaces

## Budget
2 minutes per cycle, 40 cycles

## Circuit Breaker
- `any` type is introduced
- Exported interface is changed in a breaking way
- Error count increases by more than 5 in a single cycle
