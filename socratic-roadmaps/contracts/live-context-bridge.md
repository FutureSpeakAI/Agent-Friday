# Contract: LiveContextBridge

## Module
`src/main/live-context-bridge.ts`

## Purpose
Final synthesis module of Track C. Subscribes to context stream events, runs the ContextInjector, and pushes enriched per-app context to the renderer via IPC. Includes debouncing and a circuit breaker for the execution feedback loop.

## Singleton
```typescript
import { liveContextBridge } from './live-context-bridge';
```

## API

### `start(mainWindow: BrowserWindow): void`
Begin the live context feed. Subscribes to `contextStream.on()` and pushes enriched context to the renderer on each update (debounced to 2s).

Idempotent — calling `start()` twice does not double-subscribe.

### `stop(): void`
Stop the feed and clean up all subscriptions, debounce timers, and window reference.

### `getContextForApp(appId: string): AppContext`
Synchronous read of enriched context for a specific app. Always current — the injector is refreshed eagerly on every stream event (not debounced).

### `feedExecutionResult(result: { tool_use_id: string; content: string | any[]; is_error?: boolean }): void`
Feed an execution result back into the context graph as a `tool-invoke` event. Includes a 5-second cooldown circuit breaker to prevent runaway feedback loops.

## IPC Channels

| Channel | Direction | Payload |
|---------|-----------|---------|
| `app-context:get` | renderer → main | `appId: string` → `AppContext` |
| `app-context:update` | main → renderer (push) | `AppContext` |

## Preload Namespace
```typescript
window.eve.appContext.get(appId)
window.eve.appContext.onUpdate(callback)
```

## Renderer Hook
```typescript
import { useAppContext } from '../hooks/useAppContext';

const { context, briefing, entities } = useAppContext('notes');
```

## Dependencies
- `contextStream.on()` — subscribe to context events
- `contextStream.push()` — feed execution results back
- `contextGraph.getActiveStream()` — current work stream
- `contextGraph.getTopEntities(n)` — top entities by occurrence
- `briefingDelivery.getRecentBriefings(n)` — recent briefing intelligence
- `ContextInjector.ingest() / getContextForApp()` — pure computation

## Serialization Boundary
The bridge serializes `WorkStream` (main process, with `eventTypes: Set`) to `SerializedStream` (renderer-safe, with `eventTypes: string[]`) at the `refreshInjector()` boundary.

## Types
```typescript
interface AppContext {
  activeStream: SerializedStream | null;
  entities: EntityRef[];
  briefingSummary: string | null;
}
```
