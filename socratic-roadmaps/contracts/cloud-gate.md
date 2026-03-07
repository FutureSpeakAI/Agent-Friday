## Interface Contract: CloudGate
**Sprint:** 3, Phase H.2
**Source:** src/main/cloud-gate.ts (to be created)

### Exports
- `cloudGate` — singleton instance
- `GateDecision` — interface
- `GatePolicy` — interface

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| start(mainWindow?) | `(BrowserWindow?): void` | Initialize, load persisted policies |
| stop() | `(): void` | Clear session policies |
| requestEscalation(context) | `(EscalationContext): Promise<GateDecision>` | Check policy or prompt user |
| setPolicy(category, decision, scope) | `(TaskCategory, 'allow'\|'deny', PolicyScope): void` | Store policy |
| getPolicy(category) | `(TaskCategory): GatePolicy \| null` | Check stored policy |
| getStats() | `(): EscalationStats` | Counts of local/escalated/denied |

### Types
```typescript
interface EscalationContext {
  taskCategory: TaskCategory;
  confidence: ConfidenceResult;
  promptPreview: string;        // First 100 chars, no sensitive data
  targetProvider: ProviderName;
}

interface GateDecision {
  allowed: boolean;
  reason: 'policy-allow' | 'policy-deny' | 'user-allow' | 'user-deny' | 'no-renderer' | 'no-cloud';
}

type PolicyScope = 'once' | 'session' | 'always';

interface GatePolicy {
  decision: 'allow' | 'deny';
  scope: PolicyScope;
  createdAt: number;
}

interface EscalationStats {
  localDelivered: number;
  escalatedAllowed: number;
  escalatedDenied: number;
}
```

### IPC Channels
- `cloud-gate:request-consent` — main → renderer (show dialog)
- `cloud-gate:consent-response` — renderer → main (user decision)

### Dependencies
- Requires: BrowserWindow (for IPC), vault (for 'always' policy persistence)
- Required by: IntelligenceRouter (H.3), integration test (I.1)
