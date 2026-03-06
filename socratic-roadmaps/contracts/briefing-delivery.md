## Interface Contract: Briefing Delivery
**Generated:** 2026-03-06
**Source:** src/main/briefing-delivery.ts (~175 lines), src/main/ipc/briefing-delivery-handlers.ts (~25 lines)

### Exports
- `briefingDelivery` — singleton instance of `BriefingDelivery`
- `BriefingDelivery` — class for test isolation
- `DeliveredBriefing` — interface: `{ id, topic, content, priority, timestamp, dismissed }`

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| start(mainWindow) | `(mainWindow: BrowserWindow): void` | Wire pipeline → scorer → research → IPC push chain |
| stop() | `(): void` | Unsubscribe from pipeline, clear batch timers |
| getRecentBriefings(limit?) | `(limit?: number): DeliveredBriefing[]` | Recent briefings sorted by priority then recency |
| dismissBriefing(id) | `(id: string): boolean` | Mark briefing as dismissed; returns false if not found |

### IPC Channels
| Channel | Direction | Description |
|---------|-----------|-------------|
| `briefing:list` | request-response | Returns `getRecentBriefings()` |
| `briefing:dismiss` | request-response | Calls `dismissBriefing(id)`, validates string id |
| `briefing:new` | push (main→renderer) | Emitted via `webContents.send()` with `{ id, topic, content, priority, timestamp }` |

### Priority Delivery
| Priority | Delivery | Timing |
|----------|----------|--------|
| `urgent` | immediate | `webContents.send()` on trigger |
| `relevant` | immediate | `webContents.send()` on trigger |
| `informational` | batched | max 1 push per 10 minutes |

### Preload API
```typescript
eve.briefingDelivery.list()           // → Promise<DeliveredBriefing[]>
eve.briefingDelivery.dismiss(id)      // → Promise<boolean>
eve.briefingDelivery.onNew(callback)  // → unsubscribe function
```

### Dependencies
- Requires: briefing-pipeline (onTrigger), briefing-scoring (scoreTrigger), intelligence (intelligenceEngine)
- Required by: Track C renderer components (dashboard briefing panel)
