## Interface Contract: System Monitor
**Generated:** 2026-03-06
**Source:** src/main/system-monitor.ts (161 lines)

### Exports
- `systemMonitor` — singleton: `{ getStats(), getProcesses() }`

### Public Methods
| Method | Signature | Description |
|--------|-----------|-------------|
| getStats() | `(): Promise<SystemStats>` | CPU, memory, disk, uptime |
| getProcesses(limit?) | `(limit?: number): Promise<ProcessEntry[]>` | Running processes sorted by CPU |

### Return Types
```typescript
interface SystemStats {
  cpuPercent: number;       // 0-100
  memUsedMB: number;
  memTotalMB: number;
  diskUsedGB: number;
  diskTotalGB: number;
  uptime: number;           // seconds
}

interface ProcessEntry {
  name: string;
  pid: number;
  cpu: number;              // percent
  mem: number;              // MB
}
```

### IPC Channels
| Channel | Request | Response |
|---------|---------|----------|
| system:stats | — | `SystemStats` |
| system:processes | `number?` (limit) | `ProcessEntry[]` |

### Tool Registry Shape (for Track B.1)
```typescript
// system_stats
{ name: 'system_stats', safetyLevel: 'read-only', input_schema: { type: 'object', properties: {} } }

// system_processes
{ name: 'system_processes', safetyLevel: 'read-only', input_schema: { type: 'object', properties: { limit: { type: 'number', default: 20 } } } }
```

### Dependencies
- Requires: os module, child_process (PowerShell for disk + processes)
- Required by: tool-registry (Track B.1), FridayMonitor app
