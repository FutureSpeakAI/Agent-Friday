/**
 * MoodDesktopViz.tsx — Lazy-loadable mood-aware DesktopViz wrapper.
 *
 * Extracted from MoodWrappers.tsx so that Three.js (525 kB) is only loaded
 * when this component is rendered via React.lazy(), not on initial page load.
 */

import React from 'react';
import type { SemanticState } from './FridayCore';
import DesktopViz from './DesktopViz';
import { useMood } from '../contexts/MoodContext';

function MoodDesktopViz({ getLevels, semanticState, isSpeaking, isListening, evolutionIndex, transitionBlend }: {
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

export default MoodDesktopViz;
