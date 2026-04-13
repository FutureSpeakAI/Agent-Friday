# Vibe Mode — Extracted Visual Systems for Friday Desktop

> Consolidated from 8 Agent Friday repos by Claude, April 2026.

---

## What's Here

Everything visual, mood-reactive, personality-driven, and audio-sensitive from the Agent Friday ecosystem, extracted and reorganized for the Friday Desktop's Vibe Mode tab.

### File Inventory

| File | Lines | Source Repo | What It Does |
|------|-------|-------------|--------------|
| `three-scene.js` | ~350 | Agent-Friday/desktop-viz | Core Three.js scene: renderer, post-processing, camera, background particles, structure transitions, animation loop |
| `structures.js` | ~400 | Agent-Friday/desktop-viz | 13 evolution structure builders (CUBES → EDEN) with full Three.js geometry |
| `animators.js` | ~250 | Agent-Friday/desktop-viz | Per-structure animation: audio-reactive, mood-reactive, physics |
| `materials.js` | ~60 | Agent-Friday/desktop-viz | Glow/cloud texture generators, material factory, opacity helpers |
| `personality-visuals.js` | ~220 | personality-evolution-engine | Trait→visual mapping (30+ traits to hue/energy/complexity/warmth), 50-session maturity ramp, psychological profiling |
| `mood-system.js` | ~120 | Agent-Friday + anti-sycophancy | 6 mood configs, 6 adaptive style dimensions, anti-sycophancy circuit breaker, signal detection |
| `audio-reactive.js` | ~200 | Agent-Friday + asimovs-radio | Audio level synthesis, idle detection, emotional arc engine (mirror/shift/celebration) |
| `evolution-path.js` | ~20 | Agent-Friday/desktop-viz | 13-stage evolution sequence with semantic names |
| `shaders/holographic.js` | ~40 | Agent-Friday/desktop-viz | GLSL post-processing: chromatic aberration, scanlines, film grain |
| `pixel-office/` | ref | agent-fridays-pixel-office | Reference docs for the 2D pixel art office layer |

---

## Architecture Overview

### The Three.js Holographic Desktop

The visual heart of Vibe Mode. A WebGL scene with post-processing that creates a holographic aesthetic:

```
Scene
├── Background Layer
│   ├── 800 orbital particles (mood-colored, audio-reactive speed)
│   ├── 20 energy flare curves (pulse on high-frequency audio)
│   └── 15 nebula cloud sprites (drift, mood-colored)
├── Structure Layer (1 of 13 active at a time)
│   ├── CUBES — 3x3x3 grid with breathing expansion
│   ├── ICOSAHEDRON — Nested wireframe spheres
│   ├── NETWORK — 120 physics-driven nodes with dynamic connections
│   ├── DOME — Cathedral with abyss particles and crystal rings
│   ├── ASTROLABE — 8 nested rotating torus rings
│   ├── TESSERACT — 4D→3D projection with dual-axis rotation
│   ├── QUANTUM — 30 wave rings with rainbow color cycling
│   ├── MANDELBROT — 3D fractal point cloud with height animation
│   ├── MOBIUS — Parametric strip with flow animation
│   ├── GRID — 80x80 ocean of light with sinusoidal waves
│   ├── CABLES — 80 Fibonacci-spiral tube curves
│   ├── NONE — Matrix rain vertical drift
│   └── EDEN — REZ-tribute tunnel with boss sphere and player orbit
└── Post-Processing
    ├── UnrealBloomPass (strength driven by mood + audio)
    └── HolographicShader (chromatic aberration + scanlines + grain)
```

### The 13-Stage Evolution Path

The agent evolves through 13 visual stages, each with a semantic name:

| # | ID | Name | Visual Character |
|---|-----|------|-----------------|
| 0 | CUBES | Genesis Lattice | Floating cube grid with organic breathing |
| 1 | ICOSAHEDRON | Sacred Sphere | Nested geodesic wireframes |
| 2 | NETWORK | Shannon Network | Self-organizing node physics |
| 3 | DOME | Geodesic Cathedral | Hemispherical vault with abyss |
| 4 | ASTROLABE | Lovelace Astrolabe | Nested rotating rings |
| 5 | TESSERACT | Von Neumann Tesseract | 4D hypercube projection |
| 6 | QUANTUM | Dirac Probability | Wave-deformed rainbow rings |
| 7 | MANDELBROT | Mandelbrot Set | 3D fractal landscape |
| 8 | MOBIUS | Turing Mobius | Flowing parametric strip |
| 9 | GRID | Ocean of Light | Infinite wave-plane |
| 10 | CABLES | Fibonacci Nerve | Spiral tube network |
| 11 | NONE | Transcendence | Matrix rain |
| 12 | EDEN | Giga Earth (REZ) | Box tunnel with boss fight geometry |

### Mood System

6 semantic states drive the visual atmosphere:

| Mood | Base Color | Accent Color | Rotation | Bloom | Particle Speed | Grain |
|------|-----------|-------------|----------|-------|---------------|-------|
| LISTENING | Cyan #00d2ff | Purple #8a2be2 | 0.001 | 0.8 | 1.0x | 0.035 |
| REASONING | Indigo #4b0082 | Cyan #00ffff | 0.003 | 0.6 | 0.5x | 0.02 |
| EXECUTING | Amber #ffaa00 | Red #ff3300 | 0.008 | 1.2 | 1.8x | 0.05 |
| SUB_AGENTS | Amber #ffaa00 | Red #ff3300 | 0.008 | 1.2 | 1.8x | 0.05 |
| EXCITED | White #ffffff | Cyan #00e5ff | 0.015 | 1.8 | 2.5x | 0.08 |
| CALM | Navy #001133 | Blue #0055aa | 0.0002 | 0.4 | 0.2x | 0.05 |

Colors lerp smoothly between states at `delta * 0.5` rate.

### Personality → Visual Mapping

30+ personality traits map to 4 visual dimensions:

- **Hue** (0-360°): warm→30°(orange), calm→120°(green), analytical→200°(cyan), creative→270°(purple), playful→320°(pink)
- **Energy** (0.5-2.0): energetic→1.8, balanced→1.0, calm→0.7, patient→0.5
- **Complexity** (0-1): creative→0.8, mysterious→0.7, analytical→0.5, calm→0.2
- **Warmth** (0.5-2.0): warm→1.8, empathetic→1.7, analytical→0.7, stoic→0.5

### 50-Session Maturity Ramp

Visual personality emerges gradually: `maturityFactor = Math.min(sessionCount / 50, 1)`

- Session 0: Agent looks identical to every other agent (factor = 0)
- Session 25: Personality is 50% visible
- Session 50+: Full visual uniqueness (factor = 1)

This is an **anti-sycophancy measure** — prevents the agent from immediately mirroring user preferences.

### Anti-Sycophancy Visual Indicators

The calibration engine tracks 6 adaptive dimensions and fires a hard reset when:
- Agreement streak ≥ 8 consecutive positive signals AND
- Positivity bias ≥ 85%

On circuit breaker fire:
- Emotional warmth clamps back to 0.6 (from >0.7)
- Humor clamps back to 0.6 (from >0.7)
- Agreement streak resets to 0
- Positivity bias resets to 0.5

### Audio Reactivity

Raw mic/output levels are synthesized into frequency bands:
- **Low** = level × 0.8 + heartbeat × 0.05 → drives cube breathing, dome scaling, grid waves
- **Mid** = level × 0.5 → drives astrolabe speed, particle speed, network connections
- **High** = level × 0.3 → drives quantum ring deformation, energy line flashes

Idle detection: 6 seconds of silence → idleFactor fades to 0.2 (ambient mode).

### Emotional Arc Engine (from Asimov's Radio)

Three modes that auto-transition based on agent performance:

- **Mirror**: Reflects current emotional state. Active by default.
- **Shift**: Activates on 3+ consecutive failures with frustration > 50%. Leans toward resolution.
- **Celebration**: Fires when tests pass / builds succeed / deploys land.

---

## Source Repos

| Repo | What It Contributed |
|------|-------------------|
| `Agent-Friday` | Three.js desktop-viz (scene, structures, animators, shaders, materials), personality system, voice pipeline, theme engine |
| `agent-fridays-personality-evolution-engine` | Trait→visual mapping, 50-session maturity ramp, psychological profiling |
| `anti-sycophancy` | CalibrationEngine, 6 adaptive dimensions, circuit breaker, signal detection |
| `asimovs-radio` | Emotional arc engine (mirror/shift/celebration), frustration detection |
| `asimovs-mind` | Holographic dashboard (Three.js HUD), personality subsystem integration, neural binding |
| `agent-fridays-pixel-office` | 2D pixel art office (characters, furniture, layout editor) |
| `friday-desktop` | Target app (existing HTML single-page app) |

---

## Integration Guide

### Quick Start

```html
<div id="vibe-container" style="position: absolute; inset: 0;"></div>
<script type="importmap">
{ "imports": { "three": "https://unpkg.com/three@0.170.0/build/three.module.js" } }
</script>
<script type="module">
import { initVibeScene } from './vibe-mode/three-scene.js';

const scene = initVibeScene(document.getElementById('vibe-container'), {
  getLevels: () => ({ mic: 0, output: 0 }),
  evolutionIndex: 0,
});

// Control the vibe
scene.setMood('LISTENING');    // LISTENING, REASONING, EXECUTING, EXCITED, CALM
scene.setEvolution(3);         // Switch to Geodesic Cathedral
scene.setSpeaking(true);       // Enable audio reactivity
scene.setListening(false);
```

### Connecting Personality

```javascript
import { computeEvolution, getMaturityFactor } from './vibe-mode/personality-visuals.js';

const traits = ['warm', 'creative', 'empathetic', 'playful'];
const evolution = computeEvolution(traits, sessionCount);
const maturity = getMaturityFactor(sessionCount);

// Blend default hue (200° cyan) with evolved hue
const finalHue = 200 * (1 - maturity) + evolution.primaryHue * maturity;
```

### Connecting Anti-Sycophancy

```javascript
import { detectExplicitSignal, detectImplicitSignals } from './vibe-mode/mood-system.js';

// On each user message
const explicit = detectExplicitSignal(userText);
if (explicit) {
  // Apply signal to calibration engine
}
const implicit = detectImplicitSignals(userText, responseTimeMs);
```

---

## What's NOT Extracted (Intentionally)

- **Voice pipeline state machine** — Referenced in audio-reactive.js but the full 16-state machine with fallback manager is in Agent-Friday. Not needed for visuals.
- **Asimov's Radio music library** — The emotional arc engine is extracted, but the actual song database and injection system stay in asimovs-radio.
- **cLaw integrity system** — Cryptographic governance is Agent-Friday core, not visual.
- **Memory/context/trust subsystems** — Data layers, not visual.
- **Pixel Office full source** — It's a VS Code extension fork with its own build system. Reference docs included.

---

Built by Stephen C. Webster at [FutureSpeak.AI](https://futurespeak.ai).
Extracted by Claude Opus 4.6, April 2026.
