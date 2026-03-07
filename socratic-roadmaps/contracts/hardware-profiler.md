# Contract: HardwareProfiler

## Module
`src/main/hardware/hardware-profiler.ts`

## Singleton
`HardwareProfiler.getInstance()`

## Types

```typescript
interface HardwareProfile {
  gpu: GPUInfo;
  vram: VRAMInfo;
  ram: RAMInfo;
  cpu: CPUInfo;
  disk: DiskInfo;
  detectedAt: number; // timestamp
}

interface GPUInfo {
  name: string;        // e.g., "NVIDIA GeForce RTX 4070"
  vendor: string;      // "nvidia" | "amd" | "intel" | "unknown"
  driver: string;      // driver version string
  available: boolean;  // GPU detected and functional
}

interface VRAMInfo {
  total: number;       // bytes
  available: number;   // bytes (total minus system reservation)
  systemReserved: number; // ~1.5GB for desktop compositor
}

interface RAMInfo {
  total: number;       // bytes
  available: number;   // bytes (free at detection time)
}

interface CPUInfo {
  model: string;       // e.g., "AMD Ryzen 7 5800X"
  cores: number;       // physical cores
  threads: number;     // logical threads
}

interface DiskInfo {
  modelStoragePath: string; // where models are stored
  totalSpace: number;       // bytes
  freeSpace: number;        // bytes
}
```

## Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `detect` | `() → Promise<HardwareProfile>` | Run full detection, cache result |
| `getProfile` | `() → HardwareProfile \| null` | Return cached profile or null |
| `refresh` | `() → Promise<HardwareProfile>` | Force re-detection |
| `getEffectiveVRAM` | `() → number` | Total VRAM minus system reserved |

## Detection Strategy
1. GPU: `app.getGPUInfo('complete')` + `nvidia-smi` fallback
2. VRAM: `nvidia-smi --query-gpu=memory.total` or AMD equivalent
3. RAM: `os.totalmem()` / `os.freemem()`
4. CPU: `os.cpus()`
5. Disk: `checkDiskSpace(modelStoragePath)`

## Events
- `hardware-detected` → `HardwareProfile`

## Boundary
- Detect only, never modify
- Cache result — hardware doesn't change during a session
- No GPU → `VRAMInfo.total = 0`, `GPUInfo.available = false`
