## Interface Contract: ConfidenceAssessor
**Sprint:** 3, Phase H.1
**Source:** src/main/confidence-assessor.ts (to be created)

### Exports
- `assessConfidence` — pure function (no singleton needed)
- `ConfidenceResult` — interface

### Functions
| Function | Signature | Description |
|----------|-----------|-------------|
| assessConfidence(request, response, tools?) | `(LLMRequest, LLMResponse, ToolDefinition[]?): ConfidenceResult` | Evaluate output quality |

### Types
```typescript
interface ConfidenceResult {
  score: number;           // 0-1, where 1 = fully confident
  signals: ConfidenceSignal[];
  escalate: boolean;       // true when score < threshold
}

interface ConfidenceSignal {
  name: string;            // e.g., 'malformed-tool-call', 'truncated', 'empty-response'
  weight: number;          // Impact on score (-0.5 to 0)
  detail?: string;         // Human-readable explanation
}
```

### Confidence Signals
| Signal | Condition | Weight |
|--------|-----------|--------|
| `malformed-tool-call` | Tool call JSON doesn't parse | -0.7 |
| `unknown-tool` | Tool name not in provided definitions | -0.5 |
| `truncated` | stopReason === 'max_tokens' | -0.3 |
| `empty-response` | No content and no tool calls | -0.8 |
| `unexpectedly-brief` | Content < 20 chars for moderate+ complexity | -0.2 |

### Dependencies
- Requires: LLMRequest + LLMResponse types from llm-client
- Required by: IntelligenceRouter (H.3), integration test (I.1)
