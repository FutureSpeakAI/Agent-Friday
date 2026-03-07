# Track H Phase 1: ConfidenceAssessor — "The Mirror"

## Summary
Implemented `ConfidenceAssessor`, a set of pure functions that evaluate LLM
response quality using structural signals. This is the foundation for Agent
Friday's self-assessment capability — it can now detect malformed tool calls,
truncated outputs, empty responses, and unexpectedly brief answers without
any ML inference.

## What Was Built
- **`src/main/confidence-assessor.ts`** (~130 lines)
  - Exported types: `ConfidenceResult`, `ConfidenceSignal`
  - Exported function: `assessConfidence(request, response, tools?, options?)`
  - Internal signal checkers: `checkToolCallValidity`, `checkTruncation`,
    `checkEmptyResponse`, `checkBrevity`
  - Score clamping with floating-point rounding to avoid artifacts

- **`tests/sprint-3/confidence-assessor.test.ts`** (10 tests)

## Confidence Signals Implemented
| Signal | Condition | Weight |
|--------|-----------|--------|
| `malformed-tool-call` | Tool call input is not a valid object | -0.7 |
| `unknown-tool` | Tool name not in provided definitions | -0.5 |
| `truncated` | stopReason === 'max_tokens' | -0.3 |
| `empty-response` | No content and no tool calls | -0.8 |
| `unexpectedly-brief` | Content < 20 chars for non-tool-call response | -0.2 |

## Design Decisions
1. **Pure function, not a class** — `assessConfidence` is stateless and
   deterministic. Same inputs always produce same outputs.
2. **Floating-point rounding** — Score calculation rounds to 10 decimal
   places to avoid IEEE 754 artifacts (e.g., `1.0 - 0.7 = 0.30000000000000004`).
3. **Brevity check skips tool-use responses** — Tool call responses are
   expected to have minimal text content, so brevity is only checked
   when there are no tool calls.
4. **Configurable threshold** — Default escalation threshold is 0.5, but
   callers can override via `options.threshold`.

## Safety Gate
- **tsc:** Clean (no errors)
- **Tests:** 4,058 passed across 102 files (+10 from entering count of 4,048)
- **Test count increase:** 10 (meets minimum requirement of +10)

## What's Next
- Phase H.2 could add semantic confidence signals (embedding similarity,
  self-consistency checks) on top of these structural signals.
- Integration with the Intelligence Router for automatic model fallback
  when confidence is low.
