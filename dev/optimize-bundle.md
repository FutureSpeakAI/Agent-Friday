# Optimize Bundle — Reduce Build Size

## Objective
Minimize the production bundle size while maintaining all functionality.
Focus on dead code elimination, import optimization, and dependency pruning.

## Editable Surface
- src/renderer/**/*.tsx
- src/renderer/**/*.ts
- src/main/**/*.ts
- package.json (only `dependencies` and `devDependencies` sections)

## Metric
Bundle size in bytes — lower is better.
`npm run build 2>&1 | grep -oP 'dist.*?[\d.]+ [kKmM]B' | tail -1 || du -sb dist/ | cut -f1`

## Loop
1. Run `npm run build` and capture output with bundle sizes
2. Identify the largest chunks or files in the output
3. Analyze imports in the largest files — find unused or heavy imports
4. Apply optimization (tree-shake, lazy-load, replace heavy deps with lighter ones)
5. Rebuild and compare bundle sizes
6. If smaller: commit. If larger or broken: revert.

## Constraints
- Never remove functionality — only optimize delivery
- Never change the public API or IPC contract
- Prefer lazy imports (`import()`) for heavy modules not needed at startup
- Never modify the Electron main process entry point
- All changes must pass `npm run build` without errors

## Budget
5 minutes per cycle, 10 cycles

## Circuit Breaker
- Build fails
- Bundle size increases by more than 10%
- Runtime import error detected
