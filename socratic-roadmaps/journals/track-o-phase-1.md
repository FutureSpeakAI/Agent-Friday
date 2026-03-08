# Track O Phase 1 -- "The Nerves" -- HardwareProfiler

**Date:** 2026-03-08
**Sprint:** 6
**Phase:** O.1

## What Was Built

HardwareProfiler -- a singleton module that detects GPU, VRAM, RAM, CPU, and
disk space at startup. Caches the result and emits a `hardware-detected` event.

### Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `src/main/hardware/hardware-profiler.ts` | ~230 | Singleton hardware detector |
| `tests/sprint-6/hardware/hardware-profiler.test.ts` | ~235 | 10 validation tests |

### Public API

| Method | Signature | Description |
|--------|-----------|-------------|
| `detect()` | `() -> Promise<HardwareProfile>` | Run full detection, cache result, emit event |
| `getProfile()` | `() -> HardwareProfile \| null` | Return cached profile or null |
| `refresh()` | `() -> Promise<HardwareProfile>` | Force re-detection (clears cache) |
| `getEffectiveVRAM()` | `() -> number` | Total VRAM minus 1.5 GB system reserved |
| `on(event, cb)` | `(event, cb) -> () => void` | Subscribe to events, returns unsub |
| `getInstance()` | `static` | Singleton accessor |
| `resetInstance()` | `static` | Singleton teardown for tests |

### Contract Types

- `HardwareProfile` -- top-level container with gpu, vram, ram, cpu, disk, detectedAt
- `GPUInfo` -- name, vendor, driver, available
- `VRAMInfo` -- total, available, systemReserved (bytes)
- `RAMInfo` -- total, available (bytes)
- `CPUInfo` -- model, cores, threads
- `DiskInfo` -- modelStoragePath, totalSpace, freeSpace (bytes)

## Architecture Decisions

1. **Singleton pattern** -- consistent with VoiceProfileManager, ScreenContext,
   and all other modules in the codebase.

2. **Detect once, cache forever** -- hardware does not change at runtime.
   `detect()` returns cached result on subsequent calls; `refresh()` clears
   cache first for explicit re-detection.

3. **GPU detection via Electron + nvidia-smi** -- `app.getGPUInfo('complete')`
   provides vendor ID and driver version. For NVIDIA GPUs, `nvidia-smi` is
   queried for exact GPU name and VRAM figures. Non-NVIDIA GPUs get degraded
   VRAM info (total=0) since there is no reliable cross-vendor VRAM query.

4. **System reserved VRAM** -- estimated at 1.5 GB for desktop compositor.
   Applied as a constant; `getEffectiveVRAM()` subtracts this from total.

5. **No recommendations** -- this module only detects. Recommendation logic
   belongs to O.2 "The Measure".

6. **Event emitter with unsubscribe** -- `on('hardware-detected', cb)` returns
   an unsubscribe function, matching the pattern used by OllamaLifecycle,
   ScreenContext, and AudioCapture.

7. **Parallel detection** -- GPU, RAM, CPU, and disk are detected concurrently
   via `Promise.all()` for faster startup.

8. **CPU core heuristic** -- `Math.floor(threads / 2)` since `os.cpus()` only
   reports logical threads; physical cores require platform-specific queries
   that add complexity without clear benefit at this stage.

## Validation Results

All 10 validation criteria passed:

| # | Criterion | Result |
|---|-----------|--------|
| 1 | detect() populates complete HardwareProfile | PASS |
| 2 | detect() populates GPU fields from Electron getGPUInfo | PASS |
| 3 | detect() handles no-GPU systems gracefully | PASS |
| 4 | getProfile() returns RAM via os.totalmem/freemem | PASS |
| 5 | CPU info returns model, cores, threads via os.cpus() | PASS |
| 6 | Disk info returns space at userData path via statfs | PASS |
| 7 | getProfile() returns cached result (no redundant detect) | PASS |
| 8 | NVIDIA GPU detected with VRAM from nvidia-smi | PASS |
| 9 | AMD/Intel GPU detected with degraded VRAM (total=0) | PASS |
| 10 | Event emission with unsubscribe | PASS |

## Safety Gate

| Check | Result |
|-------|--------|
| `npx tsc --noEmit` | 0 errors |
| `npx vitest run` | 4215 passed, 0 failed (117 files) |
