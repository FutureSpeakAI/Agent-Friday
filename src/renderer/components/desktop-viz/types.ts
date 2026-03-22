/**
 * desktop-viz/types.ts — Shared types and constants for DesktopViz.
 */

import type { MoodPalette } from '../../contexts/MoodContext';
import type { SemanticState } from '../FridayCore';

// ── Component Props ─────────────────────────────────────────────────────────

export interface DesktopVizProps {
  getLevels?: () => { mic: number; output: number };
  semanticState?: SemanticState;
  isSpeaking?: boolean;
  isListening?: boolean;
  moodPalette?: MoodPalette;
  moodIntensity?: number;
  moodTurbulence?: number;
  /** Index into EVOLUTION_PATH (0–12). Driven by useDesktopEvolution hook. */
  evolutionIndex?: number;
  /** 0–1 blend between current and next structure (for gradual week-long transitions). */
  transitionBlend?: number;
}

// ── Mood Mapping ────────────────────────────────────────────────────────────

export interface MoodConfig {
  baseColor: number;
  accentColor: number;
  rotationSpeed: number;
  bloomStrength: number;
  particleSpeedScale: number;
  grain: number;
}

export const MOODS: Record<string, MoodConfig> = {
  LISTENING:  { baseColor: 0x00d2ff, accentColor: 0x8a2be2, rotationSpeed: 0.001, bloomStrength: 0.8, particleSpeedScale: 1.0, grain: 0.035 },
  REASONING:  { baseColor: 0x4b0082, accentColor: 0x00ffff, rotationSpeed: 0.003, bloomStrength: 0.6, particleSpeedScale: 0.5, grain: 0.02 },
  EXECUTING:  { baseColor: 0xffaa00, accentColor: 0xff3300, rotationSpeed: 0.008, bloomStrength: 1.2, particleSpeedScale: 1.8, grain: 0.05 },
  SUB_AGENTS: { baseColor: 0xffaa00, accentColor: 0xff3300, rotationSpeed: 0.008, bloomStrength: 1.2, particleSpeedScale: 1.8, grain: 0.05 },
  EXCITED:    { baseColor: 0xffffff, accentColor: 0x00e5ff, rotationSpeed: 0.015, bloomStrength: 1.8, particleSpeedScale: 2.5, grain: 0.08 },
  CALM:       { baseColor: 0x001133, accentColor: 0x0055aa, rotationSpeed: 0.0002, bloomStrength: 0.4, particleSpeedScale: 0.2, grain: 0.05 },
};
