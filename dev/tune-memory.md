# Tune Memory — Optimize Consolidation Thresholds

## Objective
Improve memory quality by tuning the consolidation engine's promotion
thresholds, merge similarity, and scoring weights. Better memory means
Friday remembers the right things and forgets the right things.

## Editable Surface
- src/main/memory-consolidation.ts (TUNABLE zone only)

## Metric
Memory quality F1 score — higher is better (maximize).
Evaluate by running the memory quality test suite.
`npm run test -- tests/services/memory-quality.test.ts 2>&1 | grep -oP 'passed|failed' | head -1`

## Loop
1. Read current TUNABLE values from memory-consolidation.ts
2. Run memory quality tests to establish baseline metrics
3. Adjust ONE parameter (promotion threshold, merge similarity, or min occurrences)
4. Re-run memory quality tests
5. If F1 improved: commit the change
6. If F1 decreased: revert and try a different parameter
7. Move to next parameter. Repeat.

## Constraints
- PROMOTION_SCORE_THRESHOLD must stay between 5 and 20
- PROMOTION_MIN_OCCURRENCES must stay between 2 and 5
- MERGE_SIMILARITY_THRESHOLD must stay between 0.70 and 0.95
- CONSOLIDATION_INTERVAL_MS must stay between 1 hour and 24 hours
- Never modify the computePromotionScore formula structure

## Budget
3 minutes per cycle, 10 cycles

## Circuit Breaker
- Memory quality tests fail
- A threshold is set outside the allowed range
- More than 3 consecutive cycles with no improvement
