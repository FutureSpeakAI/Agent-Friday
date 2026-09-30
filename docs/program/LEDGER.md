# Build program ledger (pointer)

The "build all pending specs" program keeps its working ledger outside this
repository. The repository is public, and AGENTS.md keeps status reports and
handoff notes out of the tree. This file only says where the durable,
engineering records live.

| What | Where |
|---|---|
| The north star (target-state spec) | [`docs/design/north-star/agent-friday-ideal-product-spec.md`](../design/north-star/agent-friday-ideal-product-spec.md) |
| The owner's amendments, which override it | [`docs/design/north-star/AMENDMENTS.md`](../design/north-star/AMENDMENTS.md) |
| Every requirement mapped to the code | [`docs/design/north-star/GAP-MATRIX.md`](../design/north-star/GAP-MATRIX.md) |
| Verified status of every design doc, and the build order | [`docs/design/ROADMAP.md`](../design/ROADMAP.md), section "Re-verified 2026-09-29" |
| Decisions taken under the owner's delegation | [`docs/decisions/2026-09-29-program-delegated-decisions.md`](../decisions/2026-09-29-program-delegated-decisions.md) |

## Standing gates

- **Deploys** go only to the owner's own running Friday. Each one is guarded,
  health-checked, and rolled back automatically if the health check fails.
- **A public release needs a discussion with the owner first.** Until then
  there is no version tag, no GitHub release, no published installer, no push
  of rewritten public history, and no announcement.

## How a piece is judged

Each piece of work goes through four checks, and it lands only when all four
pass:

1. **Tests:** the new tests fail on the old code and pass on the new code.
2. **Blind comparison:** a judge compares it with the best shipped equivalent
   without being told which is which, and picks ours.
3. **Brand:** it is unmistakably Friday.
4. **North star:** it meets every north-star requirement it touches.
