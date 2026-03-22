import React from 'react';
import FridayCore, { SemanticState } from './FridayCore';
import DesktopViz from './DesktopViz';
import VoiceOrb from './VoiceOrb';
import { useMood } from '../contexts/MoodContext';

// ── Utilities ────────────────────────────────────────────────────────────────

/** Blend two hex colors at ratio t (0=a, 1=b) */
export function blendHex(a: string, b: string, t: number): string {
  const ha = a.replace('#', ''), hb = b.replace('#', '');
  const r = Math.round(parseInt(ha.substring(0, 2), 16) * (1 - t) + parseInt(hb.substring(0, 2), 16) * t);
  const g = Math.round(parseInt(ha.substring(2, 4), 16) * (1 - t) + parseInt(hb.substring(2, 4), 16) * t);
  const bl = Math.round(parseInt(ha.substring(4, 6), 16) * (1 - t) + parseInt(hb.substring(4, 6), 16) * t);
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${bl.toString(16).padStart(2, '0')}`;
}

// ── Constants ────────────────────────────────────────────────────────────────

export const SEMANTIC_COLORS: Record<SemanticState, string> = {
  LISTENING: '#00f0ff',
  REASONING: '#8A2BE2',
  SUB_AGENTS: '#D4A574',
  EXECUTING: '#22c55e',
};

export const SEMANTIC_COLORS_ALPHA: Record<SemanticState, string> = {
  LISTENING: 'rgba(0, 240, 255, 0.5)',
  REASONING: 'rgba(138, 43, 226, 0.5)',
  SUB_AGENTS: 'rgba(212, 165, 116, 0.5)',
  EXECUTING: 'rgba(34, 197, 94, 0.5)',
};

// ── Mood-aware wrapper components (must be children of MoodProvider) ──────────

export function MoodFridayCore({ getLevels, semanticState, isSpeaking, evolutionState }: {
  getLevels: () => { mic: number; output: number };
  semanticState: SemanticState;
  isSpeaking: boolean;
  evolutionState?: { sessionCount: number; primaryHue: number; secondaryHue: number; particleSpeed: number; cubeFragmentation: number; coreScale: number; dustDensity: number; glowIntensity: number } | null;
}) {
  const mood = useMood();
  return (
    <FridayCore
      getLevels={getLevels}
      semanticState={semanticState}
      isSpeaking={isSpeaking}
      moodPalette={mood.palette}
      moodIntensity={mood.intensity}
      moodTurbulence={mood.turbulence}
      evolutionState={evolutionState}
    />
  );
}

export function MoodDesktopViz({ getLevels, semanticState, isSpeaking, isListening, evolutionIndex, transitionBlend }: {
  getLevels: () => { mic: number; output: number };
  semanticState: SemanticState;
  isSpeaking: boolean;
  isListening: boolean;
  evolutionIndex: number;
  transitionBlend: number;
}) {
  const mood = useMood();
  return (
    <DesktopViz
      getLevels={getLevels}
      semanticState={semanticState}
      isSpeaking={isSpeaking}
      isListening={isListening}
      moodPalette={mood.palette}
      moodIntensity={mood.intensity}
      moodTurbulence={mood.turbulence}
      evolutionIndex={evolutionIndex}
      transitionBlend={transitionBlend}
    />
  );
}

export function MoodVoiceOrb({ isListening, isProcessing, isStreaming, onClick, interimTranscript, getLevels }: {
  isListening: boolean;
  isProcessing: boolean;
  isStreaming?: boolean;
  onClick: () => void;
  interimTranscript: string;
  getLevels?: () => { mic: number; output: number };
}) {
  const mood = useMood();
  return (
    <VoiceOrb
      isListening={isListening}
      isProcessing={isProcessing}
      isStreaming={isStreaming}
      onClick={onClick}
      interimTranscript={interimTranscript}
      getLevels={getLevels}
      moodPalette={mood.palette}
      moodIntensity={mood.intensity}
    />
  );
}

export function MoodBrandSub({ semanticState }: { semanticState: SemanticState }) {
  const mood = useMood();
  const base = SEMANTIC_COLORS[semanticState];
  const color = mood.confidence > 0.3 ? blendHex(base, mood.palette.text, 0.35) : base;
  return (
    <div style={{ color }}>
      SYS.CORE // {semanticState.replace('_', '-')}
    </div>
  );
}

export function MoodStatusLabel({ semanticState, statusText }: { semanticState: SemanticState; statusText: string }) {
  const mood = useMood();
  const base = SEMANTIC_COLORS[semanticState];
  const color = mood.confidence > 0.3
    ? blendHex(base, mood.palette.text, 0.25) + '80'
    : SEMANTIC_COLORS_ALPHA[semanticState];
  return (
    <div style={{ color }}>
      {statusText}
    </div>
  );
}
