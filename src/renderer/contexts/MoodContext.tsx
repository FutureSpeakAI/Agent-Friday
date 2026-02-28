/**
 * MoodContext.tsx — Mood-responsive visual parameter system.
 *
 * Polls the sentiment engine every 5 seconds and derives visual parameters
 * (palette, intensity, warmth, turbulence) that drive the UI's mood-responsive
 * color shifts and animation modulations.
 *
 * Components consume via useMood() hook — no prop drilling needed.
 */

import React, { createContext, useContext, useRef, useState, useEffect, useCallback } from 'react';
import type { SemanticState } from '../components/FridayCore';

// ── Types ────────────────────────────────────────────────────────────────────

export type Mood = 'positive' | 'neutral' | 'frustrated' | 'tired' | 'excited' | 'stressed' | 'curious' | 'focused';

export interface MoodPalette {
  primary: string;     // Main accent color (hex)
  secondary: string;   // Secondary accent (hex)
  glow: string;        // Glow/bloom color (hex)
  background: string;  // Subtle background tint (hex)
  text: string;        // Text accent color (hex)
}

export interface MoodVisuals {
  // Raw sentiment data
  currentMood: Mood;
  confidence: number;       // 0–1
  energyLevel: number;      // 0–1
  moodStreak: number;       // consecutive same-mood count

  // Derived visual parameters
  palette: MoodPalette;
  intensity: number;        // 0–1 — drives glow strength, particle count, animation speed
  warmth: number;           // 0–1 — color temperature shift (cool blue → warm gold)
  turbulence: number;       // 0–1 — drives chaos/calm in particle systems

  // Semantic state (passed through from App)
  semanticState: SemanticState;
}

// ── Mood → Visual Mapping ────────────────────────────────────────────────────

interface MoodConfig {
  palette: MoodPalette;
  intensity: number;
  warmth: number;
  turbulence: number;
}

const MOOD_CONFIGS: Record<Mood, MoodConfig> = {
  positive: {
    palette: {
      primary: '#00e5ff',
      secondary: '#ffd700',
      glow: '#00e5ff',
      background: '#0a1520',
      text: '#00e5ff',
    },
    intensity: 0.7,
    warmth: 0.5,
    turbulence: 0.2,
  },
  excited: {
    palette: {
      primary: '#ff3399',
      secondary: '#00ccff',
      glow: '#ff3399',
      background: '#150818',
      text: '#ff66b2',
    },
    intensity: 0.9,
    warmth: 0.7,
    turbulence: 0.6,
  },
  curious: {
    palette: {
      primary: '#00bfa5',
      secondary: '#9c64ff',
      glow: '#00bfa5',
      background: '#081518',
      text: '#00bfa5',
    },
    intensity: 0.6,
    warmth: 0.4,
    turbulence: 0.4,
  },
  focused: {
    palette: {
      primary: '#3366ff',
      secondary: '#c0c0c0',
      glow: '#3366ff',
      background: '#080c18',
      text: '#6699ff',
    },
    intensity: 0.5,
    warmth: 0.2,
    turbulence: 0.1,
  },
  neutral: {
    palette: {
      primary: '#00e5ff',
      secondary: '#b026ff',
      glow: '#00e5ff',
      background: '#050508',
      text: '#00e5ff',
    },
    intensity: 0.4,
    warmth: 0.3,
    turbulence: 0.2,
  },
  tired: {
    palette: {
      primary: '#9090c0',
      secondary: '#606080',
      glow: '#7070a0',
      background: '#060610',
      text: '#8080a0',
    },
    intensity: 0.2,
    warmth: 0.3,
    turbulence: 0.1,
  },
  frustrated: {
    palette: {
      primary: '#ff5533',
      secondary: '#9926cc',
      glow: '#ff5533',
      background: '#180808',
      text: '#ff7755',
    },
    intensity: 0.8,
    warmth: 0.8,
    turbulence: 0.8,
  },
  stressed: {
    palette: {
      primary: '#ffaa00',
      secondary: '#ff6600',
      glow: '#ffcc44',
      background: '#151008',
      text: '#ffcc66',
    },
    intensity: 0.7,
    warmth: 0.6,
    turbulence: 0.7,
  },
};

// ── Color Interpolation Helpers ──────────────────────────────────────────────

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.substring(0, 2), 16),
    parseInt(h.substring(2, 4), 16),
    parseInt(h.substring(4, 6), 16),
  ];
}

function rgbToHex(r: number, g: number, b: number): string {
  const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  return '#' + [clamp(r), clamp(g), clamp(b)]
    .map((c) => c.toString(16).padStart(2, '0'))
    .join('');
}

function lerpColor(a: string, b: string, t: number): string {
  const [ar, ag, ab] = hexToRgb(a);
  const [br, bg, bb] = hexToRgb(b);
  return rgbToHex(
    ar + (br - ar) * t,
    ag + (bg - ag) * t,
    ab + (bb - ab) * t,
  );
}

function lerpPalette(a: MoodPalette, b: MoodPalette, t: number): MoodPalette {
  return {
    primary: lerpColor(a.primary, b.primary, t),
    secondary: lerpColor(a.secondary, b.secondary, t),
    glow: lerpColor(a.glow, b.glow, t),
    background: lerpColor(a.background, b.background, t),
    text: lerpColor(a.text, b.text, t),
  };
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

// ── Default values ───────────────────────────────────────────────────────────

const DEFAULT_VISUALS: MoodVisuals = {
  currentMood: 'neutral',
  confidence: 0,
  energyLevel: 0.5,
  moodStreak: 0,
  palette: { ...MOOD_CONFIGS.neutral.palette },
  intensity: MOOD_CONFIGS.neutral.intensity,
  warmth: MOOD_CONFIGS.neutral.warmth,
  turbulence: MOOD_CONFIGS.neutral.turbulence,
  semanticState: 'LISTENING',
};

// ── Context ──────────────────────────────────────────────────────────────────

const MoodContext = createContext<MoodVisuals>(DEFAULT_VISUALS);

export function useMood(): MoodVisuals {
  return useContext(MoodContext);
}

// ── Provider ─────────────────────────────────────────────────────────────────

interface MoodProviderProps {
  semanticState: SemanticState;
  children: React.ReactNode;
}

export function MoodProvider({ semanticState, children }: MoodProviderProps) {
  // Raw sentiment data from main process
  const [rawMood, setRawMood] = useState<Mood>('neutral');
  const [rawConfidence, setRawConfidence] = useState(0);
  const [rawEnergy, setRawEnergy] = useState(0.5);
  const [rawStreak, setRawStreak] = useState(0);

  // Interpolated visual parameters (for smooth transitions)
  const [visuals, setVisuals] = useState<MoodVisuals>(DEFAULT_VISUALS);

  // Target config ref (what we're lerping towards)
  const targetRef = useRef<MoodConfig>(MOOD_CONFIGS.neutral);
  const currentRef = useRef<MoodConfig>({
    palette: { ...MOOD_CONFIGS.neutral.palette },
    intensity: MOOD_CONFIGS.neutral.intensity,
    warmth: MOOD_CONFIGS.neutral.warmth,
    turbulence: MOOD_CONFIGS.neutral.turbulence,
  });

  // ── Poll sentiment engine every 5 seconds ──
  useEffect(() => {
    let alive = true;

    const poll = async () => {
      try {
        const state = await window.eve.sentiment.getState();
        if (!alive) return;
        const mood = (state.currentMood || 'neutral') as Mood;
        setRawMood(mood);
        setRawConfidence(state.confidence ?? 0);
        setRawEnergy(state.energyLevel ?? 0.5);
        setRawStreak(state.moodStreak ?? 0);

        // Update target and kick off lerp
        targetRef.current = MOOD_CONFIGS[mood] || MOOD_CONFIGS.neutral;
        startLerp();
      } catch {
        // Sentiment not available — stay neutral
      }
    };

    poll(); // Initial fetch
    const id = setInterval(poll, 5000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // ── Smooth interpolation loop — runs RAF only while lerping towards target ──
  const rafIdRef = useRef<number>(0);
  const isLerpingRef = useRef(false);

  const startLerp = useCallback(() => {
    if (isLerpingRef.current) return; // Already running
    isLerpingRef.current = true;

    const LERP_SPEED = 0.03;
    const CONVERGENCE_THRESHOLD = 0.005; // Stop when difference < 0.5%

    const tick = () => {
      const target = targetRef.current;
      const current = currentRef.current;

      // Lerp palette colors
      current.palette = lerpPalette(current.palette, target.palette, LERP_SPEED);

      // Lerp scalar values
      current.intensity = lerp(current.intensity, target.intensity, LERP_SPEED);
      current.warmth = lerp(current.warmth, target.warmth, LERP_SPEED);
      current.turbulence = lerp(current.turbulence, target.turbulence, LERP_SPEED);

      // Check convergence — stop RAF when values are close enough
      const diff = Math.abs(current.intensity - target.intensity)
        + Math.abs(current.warmth - target.warmth)
        + Math.abs(current.turbulence - target.turbulence);

      if (diff < CONVERGENCE_THRESHOLD) {
        // Snap to target and stop looping
        current.intensity = target.intensity;
        current.warmth = target.warmth;
        current.turbulence = target.turbulence;
        current.palette = { ...target.palette };
        isLerpingRef.current = false;
        return;
      }

      rafIdRef.current = requestAnimationFrame(tick);
    };

    rafIdRef.current = requestAnimationFrame(tick);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => cancelAnimationFrame(rafIdRef.current);
  }, []);

  // ── Update visuals state at a lower frequency (every 100ms) to avoid React overhead ──
  // Only push a new object when values actually changed (skip no-op updates).
  const lastPushedRef = useRef({ intensity: 0, warmth: 0, turbulence: 0 });

  useEffect(() => {
    const id = setInterval(() => {
      const current = currentRef.current;
      const last = lastPushedRef.current;

      // Skip if interpolated values haven't moved since last push
      const delta = Math.abs(current.intensity - last.intensity)
        + Math.abs(current.warmth - last.warmth)
        + Math.abs(current.turbulence - last.turbulence);

      if (delta < 0.001) return; // Nothing changed — skip React render

      last.intensity = current.intensity;
      last.warmth = current.warmth;
      last.turbulence = current.turbulence;

      setVisuals({
        currentMood: rawMood,
        confidence: rawConfidence,
        energyLevel: rawEnergy,
        moodStreak: rawStreak,
        palette: { ...current.palette },
        intensity: current.intensity,
        warmth: current.warmth,
        turbulence: current.turbulence,
        semanticState,
      });
    }, 100);

    return () => clearInterval(id);
  }, [rawMood, rawConfidence, rawEnergy, rawStreak, semanticState]);

  return (
    <MoodContext.Provider value={visuals}>
      {children}
    </MoodContext.Provider>
  );
}
