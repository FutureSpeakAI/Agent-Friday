# Phase O.1 — The Nerves
## HardwareProfiler: Know Thyself

### Hermeneutic Focus
*The system has eyes, ears, voice, and mind — but doesn't know its own body. Before it can intelligently configure itself, it must know what hardware it inhabits. This is the deepest self-awareness: not "what can I do?" but "what am I?"*

### Current State (Post-Sprint 5)
- OllamaLifecycle tracks VRAM usage of loaded models
- Electron provides `app.getGPUInfo('complete')` for GPU detection
- Node.js provides `os.totalmem()`, `os.cpus()`, `os.freemem()`
- No centralized hardware profile exists
- No hardware-based decision-making exists

### Architecture Context
```
HardwareProfiler (this phase)
├── detect()              — Run full hardware detection
├── getProfile()          — Cached hardware profile
├── getGPUInfo()          — GPU name, VRAM, driver
├── getRAMInfo()          — Total, available, usage
├── getCPUInfo()          — Model, cores, frequency
├── getDiskInfo()         — Available space for models
└── on('profile-ready', cb) — Detection complete
```

### Validation Criteria (Test-First)
1. `detect()` populates a complete HardwareProfile
2. `getGPUInfo()` returns GPU name, VRAM total/available
3. `getGPUInfo()` handles no-GPU systems gracefully (integrated graphics)
4. `getRAMInfo()` returns total and available system RAM
5. `getCPUInfo()` returns model name, core count, frequency
6. `getDiskInfo()` returns available disk space at userData path
7. `getProfile()` returns cached result (no redundant detection)
8. NVIDIA GPU detected via `app.getGPUInfo()` with VRAM figures
9. AMD/Intel GPU detected with degraded VRAM info (no nvidia-smi)
10. All tests mock Electron and OS APIs — no real hardware dependency

### Socratic Inquiry

**Boundary:** *What hardware info do we actually need?*
Just enough to choose a tier: GPU VRAM (which models fit), system RAM (can Ollama run), CPU cores (how fast is CPU inference), disk space (room for model files). Don't over-detect — we're not a benchmark tool.

**Inversion:** *What if we can't detect the GPU at all?*
Treat as 0 VRAM. The system defaults to Whisper tier (CPU-only). STT still works (tiny model on CPU), TTS still works (CPU), LLM goes cloud-only. Still functional, just not sovereign.

**Constraint Discovery:** *How reliable is app.getGPUInfo('complete')?*
On Windows with NVIDIA: very reliable — returns name, VRAM, driver. On Linux: varies by driver. On macOS: returns Metal GPU info. For NVIDIA, supplement with `nvidia-smi` subprocess for precise VRAM free/used.

**Precedent:** *How does OllamaLifecycle track VRAM?*
Via Ollama's `/api/ps` endpoint which reports per-model VRAM. HardwareProfiler gets the TOTAL VRAM from the system level. Together they tell us: total budget vs. currently used.

**Tension:** *One-time detection vs. continuous monitoring?*
Hardware doesn't change at runtime (no hot-swapping GPUs). Detect once at startup, cache the result. VRAM usage changes (models load/unload) — that's OllamaLifecycle's job, not ours.

### Boundary Constraints
- Creates `src/main/hardware/hardware-profiler.ts` (~120-150 lines)
- Creates `tests/sprint-6/hardware/hardware-profiler.test.ts`
- Does NOT make recommendations (that's O.2)
- Does NOT manage models (that's O.3)
- Detection runs once at startup, result is cached

### Files to Read
1. `src/main/ollama-lifecycle.ts` — VRAM tracking pattern
2. `src/main/main.ts` — App lifecycle for detection timing

### Session Journal Reminder
Write `socratic-roadmaps/journals/track-o-phase-1.md` before closing.
