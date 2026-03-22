import { useEffect, useRef, useCallback } from 'react';

/**
 * RAF loop for audio levels — avoids re-renders by reading directly from
 * AnalyserNodes into a mutable ref. Returns a stable `getLevels` callback.
 */
export function useAudioLevels(
  getMicLevel: () => number,
  getOutputLevel: () => number,
): () => { mic: number; output: number } {
  const audioLevelsRef = useRef({ mic: 0, output: 0 });

  useEffect(() => {
    let rafId: number;
    const tick = () => {
      audioLevelsRef.current.mic = getMicLevel();
      audioLevelsRef.current.output = getOutputLevel();
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [getMicLevel, getOutputLevel]);

  return useCallback(() => audioLevelsRef.current, []);
}
