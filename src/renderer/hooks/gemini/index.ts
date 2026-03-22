/**
 * Gemini Live hook — re-exports.
 *
 * The hook is still importable from the parent `useGeminiLive.ts` file
 * (which is what App.tsx uses). This index provides access to types and
 * sub-modules for any code that wants to import from `gemini/` directly.
 */

export { useGeminiLive } from '../useGeminiLive';
export type { UseGeminiLiveOptions, GeminiLiveState } from './types';
