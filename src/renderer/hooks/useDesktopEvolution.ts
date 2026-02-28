/**
 * useDesktopEvolution.ts — Desktop visualization evolution state hook.
 *
 * Manages which of the 13 holographic structures is currently displayed,
 * persists via IPC to main-process settings, and provides controls for
 * manual structure switching + future weekly auto-evolution.
 *
 * The 13 structures in EVOLUTION_PATH:
 *   0: CUBES (Genesis Lattice)
 *   1: ICOSAHEDRON (Sacred Sphere)
 *   2: NETWORK (Shannon Network)
 *   3: DOME (Geodesic Cathedral)
 *   4: ASTROLABE (Lovelace Astrolabe)
 *   5: TESSERACT (Von Neumann Tesseract)
 *   6: QUANTUM (Dirac Probability)
 *   7: MANDELBROT (Mandelbrot Set)
 *   8: MOBIUS (Turing Mobius)
 *   9: GRID (Ocean of Light)
 *  10: CABLES (Fibonacci Nerve)
 *  11: NONE (Transcendence)
 *  12: EDEN (Giga Earth / REZ Tribute)
 */

import { useState, useEffect, useCallback, useRef } from 'react';

const MAX_INDEX = 12;

export interface DesktopEvolutionState {
  /** Current evolution index (0–12) */
  evolutionIndex: number;
  /** 0–1 blend for gradual transitions (future: week-long morphs) */
  transitionBlend: number;
  /** Set index immediately (triggers metamorphosis in DesktopViz) */
  setEvolution: (index: number) => void;
  /** Advance to next structure (wraps around) */
  nextEvolution: () => void;
  /** Go back to previous structure (wraps around) */
  prevEvolution: () => void;
}

export function useDesktopEvolution(): DesktopEvolutionState {
  const [evolutionIndex, setEvolutionIndex] = useState(0);
  const [transitionBlend, setTransitionBlend] = useState(1.0);
  const initialized = useRef(false);

  // Load persisted index on mount
  useEffect(() => {
    (async () => {
      try {
        const idx = await window.eve.desktopEvolution.getIndex();
        const clamped = Math.max(0, Math.min(MAX_INDEX, idx ?? 0));
        setEvolutionIndex(clamped);
        initialized.current = true;
      } catch (e) {
        console.warn('[useDesktopEvolution] Failed to load persisted index:', e);
        initialized.current = true;
      }
    })();
  }, []);

  const setEvolution = useCallback((index: number) => {
    const clamped = Math.max(0, Math.min(MAX_INDEX, index));
    setEvolutionIndex(clamped);
    setTransitionBlend(1.0);
    // Persist to main process
    window.eve.desktopEvolution.setIndex(clamped).catch((e: unknown) => {
      console.warn('[useDesktopEvolution] Failed to persist index:', e);
    });
  }, []);

  const nextEvolution = useCallback(() => {
    setEvolution((evolutionIndex + 1) % (MAX_INDEX + 1));
  }, [evolutionIndex, setEvolution]);

  const prevEvolution = useCallback(() => {
    setEvolution(evolutionIndex === 0 ? MAX_INDEX : evolutionIndex - 1);
  }, [evolutionIndex, setEvolution]);

  return {
    evolutionIndex,
    transitionBlend,
    setEvolution,
    nextEvolution,
    prevEvolution,
  };
}
